"""Validate the deliberately small, portable review bundle format."""
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
import json
import math
import re

MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_TRACKS = 200
MAX_CLIPS = 10000
MAX_PEAKS = 50000


class ManifestError(ValueError):
    pass


def text(value, field, default=None, limit=1024):
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ManifestError(f'{field} must be a nonempty string of at most {limit} characters.')
    return value


def number(value, field, minimum=0, maximum=1e9, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f'{field} must be a finite number.')
    if value < minimum or value > maximum or (positive and value == 0):
        raise ManifestError(f'{field} is outside the supported range.')
    return value


def identifier(value, field):
    value = text(value, field, limit=128)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value):
        raise ManifestError(f'{field} must contain only letters, digits, dots, colons, underscores or hyphens.')
    return value


def asset_path(value, field):
    value = text(value, field, limit=2048)
    try:
        parsed = urlsplit(value)
        decoded = unquote(value, errors='strict')
    except (ValueError, UnicodeError) as exc:
        raise ManifestError(f'{field} is not a valid local relative path.') from exc
    parts = PurePosixPath(decoded).parts
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or decoded.startswith('/') or '\\' in decoded or any(c in decoded for c in (':', '%', '?', '#', '\x00')) or any(ord(c) < 32 for c in decoded) or '..' in parts or not parts:
        raise ManifestError(f'{field} must be a local relative asset path without traversal, a query or a fragment.')
    return PurePosixPath(decoded).as_posix()


def _boolean(value, field, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ManifestError(f'{field} must be true or false.')
    return value


def validate_manifest(data):
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or isinstance(data.get('schemaVersion'), bool):
        raise ManifestError('schemaVersion must be 1.')
    duration = number(data.get('duration'), 'duration', maximum=86400, positive=True)
    result = {'schemaVersion': 1, 'title': text(data.get('title'), 'title', limit=200),
              'duration': duration, 'fps': number(data.get('fps'), 'fps', maximum=240, positive=True),
              'revision': text(data.get('revision'), 'revision', default='local', limit=200),
              'videoUrl': asset_path(data.get('videoUrl'), 'videoUrl'), 'tracks': [], 'notes': []}
    if data.get('posterUrl'):
        result['posterUrl'] = asset_path(data['posterUrl'], 'posterUrl')
    tracks = data.get('tracks')
    if not isinstance(tracks, list) or not tracks or len(tracks) > MAX_TRACKS:
        raise ManifestError(f'tracks must contain between 1 and {MAX_TRACKS} lanes.')
    track_ids, clip_ids = set(), set()
    for i, track in enumerate(tracks):
        prefix = f'tracks[{i}]'
        if not isinstance(track, dict):
            raise ManifestError(f'{prefix} must be an object.')
        tid = identifier(track.get('id'), prefix + '.id')
        if tid in track_ids:
            raise ManifestError(f'Duplicate lane ID: {tid}')
        track_ids.add(tid)
        kind = track.get('kind')
        if kind not in ('video', 'audio'):
            raise ManifestError(f'{prefix}.kind must be video or audio.')
        clips = track.get('clips')
        if not isinstance(clips, list):
            raise ManifestError(f'{prefix}.clips must be an array.')
        out = {'id': tid, 'name': text(track.get('name'), prefix + '.name', limit=200), 'kind': kind,
               'parked': _boolean(track.get('parked'), prefix + '.parked'), 'clips': []}
        for n, clip in enumerate(clips):
            cp = f'{prefix}.clips[{n}]'
            if not isinstance(clip, dict):
                raise ManifestError(f'{cp} must be an object.')
            cid = identifier(clip.get('id'), cp + '.id')
            if cid in clip_ids:
                raise ManifestError(f'Duplicate clip ID: {cid}')
            clip_ids.add(cid)
            if len(clip_ids) > MAX_CLIPS:
                raise ManifestError(f'Too many clips; maximum {MAX_CLIPS}.')
            start = number(clip.get('start'), cp + '.start', maximum=duration)
            end = number(clip.get('end'), cp + '.end', maximum=duration + .002)
            length = number(clip.get('duration'), cp + '.duration', maximum=duration + .002, positive=True)
            if end <= start or abs(end - start - length) > .002:
                raise ManifestError(f'{cp} has inconsistent start, end or duration.')
            source_start = number(clip.get('sourceStart'), cp + '.sourceStart')
            source_end = number(clip.get('sourceEnd'), cp + '.sourceEnd')
            if source_end <= source_start:
                raise ManifestError(f'{cp}.sourceEnd must follow sourceStart.')
            c = {'id': cid, 'label': text(clip.get('label'), cp + '.label', limit=512),
                 'start': start, 'end': end, 'duration': length, 'sourceStart': source_start,
                 'sourceEnd': source_end, 'speed': number(clip.get('speed', 1), cp + '.speed', maximum=100, positive=True),
                 'hidden': _boolean(clip.get('hidden'), cp + '.hidden'),
                 'colorPending': _boolean(clip.get('colorPending'), cp + '.colorPending'), 'thumbnail': None}
            if clip.get('layerId') is not None:
                c['layerId'] = number(clip['layerId'], cp + '.layerId', maximum=2**31 - 1)
            if clip.get('sourceFilename'):
                c['sourceFilename'] = text(clip['sourceFilename'], cp + '.sourceFilename', limit=512)
            c['volume'] = None if clip.get('volume') is None else number(clip['volume'], cp + '.volume', maximum=100)
            if clip.get('thumbnail'):
                c['thumbnail'] = asset_path(clip['thumbnail'], cp + '.thumbnail')
            out['clips'].append(c)
        result['tracks'].append(out)
    notes = data.get('notes', [])
    if not isinstance(notes, list) or len(notes) > 20:
        raise ManifestError('notes must be an array of at most 20 strings.')
    result['notes'] = [text(v, 'note', limit=2000) for v in notes]
    waveform = data.get('waveform')
    result['waveform'] = None
    if waveform is not None:
        if not isinstance(waveform, dict) or not isinstance(waveform.get('peaks'), list) or len(waveform['peaks']) > MAX_PEAKS:
            raise ManifestError(f'waveform.peaks must contain at most {MAX_PEAKS} samples.')
        step = number(waveform.get('step'), 'waveform.step', maximum=duration, positive=True)
        result['waveform'] = {'step': step, 'peaks': [number(v, 'waveform peak', maximum=1) for v in waveform['peaks']]}
    return result


def load_manifest(path):
    path = Path(path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ManifestError('Manifest exceeds the 10 MB limit.')
    def pairs(items):
        d = {}
        for key, value in items:
            if key in d:
                raise ManifestError(f'Duplicate JSON field: {key}')
            d[key] = value
        return d
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError(f'Cannot read manifest JSON: {exc}') from exc
    return validate_manifest(data)


def asset_files(root, manifest):
    root = Path(root).resolve()
    names = {manifest['videoUrl']}
    if manifest.get('posterUrl'):
        names.add(manifest['posterUrl'])
    names.update(c['thumbnail'] for t in manifest['tracks'] for c in t['clips'] if c.get('thumbnail'))
    output = {}
    for name in names:
        suffix = Path(name).suffix.lower()
        permitted = {'.mp4', '.webm', '.mov', '.m4v'} if name == manifest['videoUrl'] else {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
        if suffix not in permitted:
            raise ManifestError(f'Unsupported asset extension: {name}')
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ManifestError(f'Asset escapes the bundle directory: {name}')
        if not target.is_file():
            raise ManifestError(f'Missing bundle asset: {name}')
        if name in ('index.html', 'app.js', 'styles.css', 'data.json', 'health'):
            raise ManifestError(f'Reserved asset name: {name}')
        output['/' + name] = target
    return output
