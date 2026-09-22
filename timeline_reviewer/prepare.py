"""Prepare a new review bundle from a rendered movie and optional lane metadata."""
from pathlib import Path
from fractions import Fraction
import array
import hashlib
import json
import shutil
import subprocess
import sys

from .manifest import validate_manifest, asset_path, MAX_PEAKS


def _run(command):
    try:
        return subprocess.run(command, capture_output=True, check=True).stdout
    except FileNotFoundError as exc:
        raise ValueError(f'{command[0]} is required for this command. Install FFmpeg and ffprobe separately.') from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode('utf-8', errors='replace').strip()[-2000:]
        raise ValueError(f'{command[0]} could not process the selected media: {detail}') from exc


def probe(video):
    result = json.loads(_run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
    streams = result.get('streams', [])
    picture = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if picture is None:
        raise ValueError('The selected file has no video stream.')
    duration = float(result['format']['duration'])
    try:
        fps = float(Fraction(picture.get('avg_frame_rate') or picture.get('r_frame_rate')))
    except (ValueError, ZeroDivisionError, TypeError):
        fps = 30
    if not 0 < duration <= 86400 or not 0 < fps <= 240:
        raise ValueError('The media duration or frame rate exceeds the supported limits.')
    return result, picture, duration, fps


def waveform(video, duration):
    rate = 16000
    step = max(.1, duration / (MAX_PEAKS - 1))
    count = max(1, round(rate * step))
    step = count / rate
    process = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', str(rate), '-f', 'f32le', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    peaks = []
    try:
        while True:
            block = process.stdout.read(count * 4)
            if not block:
                break
            samples = array.array('f')
            samples.frombytes(block[:len(block) // 4 * 4])
            if sys.byteorder != 'little':
                samples.byteswap()
            peaks.append(round(min(1.0, max((abs(v) for v in samples), default=0.0)), 4))
            if len(peaks) > MAX_PEAKS:
                raise ValueError('Unexpected waveform length.')
        status = process.wait()
        if status != 0:
            raise ValueError('FFmpeg could not read the preview audio.')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    return {'step': step, 'peaks': peaks}


def prepare_bundle(video, output, timeline=None, title=None):
    video = Path(video).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if not video.is_file():
        raise ValueError('Video file not found.')
    if output.exists():
        raise ValueError('Output already exists. Choose a new review bundle directory.')
    info, picture, duration, fps = probe(video)
    if timeline:
        timeline = Path(timeline).expanduser().resolve()
        if timeline.stat().st_size > 10 * 1024 * 1024:
            raise ValueError('Timeline metadata exceeds 10 MB.')
        data = json.loads(timeline.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Timeline metadata must be an object.')
        data['duration'] = duration
        data.setdefault('fps', fps)
        data.setdefault('title', video.stem)
        data.setdefault('schemaVersion', 1)
    else:
        data = {'schemaVersion': 1, 'title': video.stem, 'duration': duration, 'fps': fps,
                'tracks': [{'id': 'picture', 'name': 'Picture', 'kind': 'video', 'clips': [
                    {'id': 'full-movie', 'label': 'Full movie', 'start': 0, 'end': duration, 'duration': duration,
                     'sourceStart': 0, 'sourceEnd': duration, 'speed': 1, 'hidden': False}]}]}
    if title:
        data['title'] = title
    data['videoUrl'] = 'media/preview.mp4'
    data['posterUrl'] = 'media/poster.jpg'
    digest = hashlib.sha256()
    with video.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    data['revision'] = digest.hexdigest()[:16]
    data['waveform'] = None
    data = validate_manifest(data)
    thumbnails = []
    if timeline:
        for track in data['tracks']:
            for clip in track['clips']:
                if clip.get('thumbnail'):
                    relative = asset_path(clip['thumbnail'], 'thumbnail')
                    source = (timeline.parent / relative).resolve()
                    if not source.is_relative_to(timeline.parent) or not source.is_file():
                        raise ValueError(f'Thumbnail is missing or outside the metadata directory: {relative}')
                    if source.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                        raise ValueError('Unsupported thumbnail type.')
                    destination = f'thumbs/{len(thumbnails):04d}{source.suffix.lower()}'
                    thumbnails.append((source, destination))
                    clip['thumbnail'] = destination
    output.mkdir(parents=True, exist_ok=False)
    (output / 'media').mkdir()
    if thumbnails:
        (output / 'thumbs').mkdir()
    for source, destination in thumbnails:
        shutil.copy2(source, output / destination)
    # A separate derivative is deliberate. Source input is never rewritten.
    command = ['ffmpeg', '-v', 'error', '-n', '-i', str(video), '-map', '0:v:0', '-map', '0:a:0?']
    if picture['codec_name'] == 'h264':
        command += ['-c:v', 'copy']
    else:
        command += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p']
    command += ['-c:a', 'aac', '-b:a', '320k', '-movflags', '+faststart', str(output / 'media/preview.mp4')]
    _run(command)
    _run(['ffmpeg', '-v', 'error', '-n', '-i', str(output / 'media/preview.mp4'), '-frames:v', '1', '-q:v', '3', str(output / 'media/poster.jpg')])
    if any(s.get('codec_type') == 'audio' for s in info['streams']):
        data['waveform'] = waveform(output / 'media/preview.mp4', duration)
    data = validate_manifest(data)
    (output / 'data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return {'bundle': str(output), 'duration': duration, 'clips': sum(len(t['clips']) for t in data['tracks'])}
