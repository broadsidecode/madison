"""Frozen, path-safe runtime identity for Madison servers and release archives."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Callable
from urllib.request import Request, urlopen

from . import __version__


PROTOCOL_VERSION = 1
MANIFEST_NAME = 'madison-release.json'
_SAFE_GITHUB = {'current', 'different', 'ahead', 'behind', 'diverged', 'not_verified'}
_HEX40 = re.compile(r'[0-9a-f]{40}')


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _source_files(package_root: Path) -> tuple[Path, ...]:
    allowed = {'.py', '.html', '.js', '.css'}
    return tuple(sorted((path for path in package_root.rglob('*')
                         if path.is_file() and path.suffix.lower() in allowed
                         and '__pycache__' not in path.parts),
                        key=lambda path: path.relative_to(package_root).as_posix()))


def source_fingerprint(package_root: Path) -> str:
    package_root = Path(package_root).resolve()
    digest = sha256()
    for path in _source_files(package_root):
        name = path.relative_to(package_root).as_posix().encode('utf-8')
        data = path.read_bytes()
        digest.update(len(name).to_bytes(4, 'big'))
        digest.update(name)
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    return digest.hexdigest()


def _git_details(package_root: Path) -> dict | None:
    root = package_root.parent
    try:
        def run(*args):
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                    text=True, timeout=4, check=True)
            return result.stdout.strip()
        commit = run('rev-parse', 'HEAD').lower()
        branch = run('branch', '--show-current')
        if not _HEX40.fullmatch(commit) or not branch:
            return None
        lines = run('status', '--porcelain', '--untracked-files=all').splitlines()
        lines = [line for line in lines if not (
            line.startswith('?? ') and line[3:].replace('\\', '/') == 'images/madison-logo.png')]
        changed = bool(lines)
        return {'commit': commit, 'branch': branch, 'localChanges': changed}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _release_manifest(package_root: Path, version: str, build_id: str) -> dict | None:
    path = package_root.parent / MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1 or
            value.get('version') != version or value.get('buildId') != build_id):
        return None
    commit = value.get('commit')
    if commit is not None and (not isinstance(commit, str) or not _HEX40.fullmatch(commit.lower())):
        return None
    return {'commit': commit.lower() if commit else None}


def _github_result(checker: Callable[[], dict] | None) -> dict:
    unknown = {'status': 'not_verified', 'checkedAt': _timestamp()}
    if checker is None:
        return unknown
    try:
        value = checker()
    except Exception:
        return unknown
    if not isinstance(value, dict) or value.get('status') not in _SAFE_GITHUB:
        return unknown
    checked = value.get('checkedAt')
    if checked is not None and not isinstance(checked, str):
        return unknown
    answer = {'status': value['status'], 'checkedAt': checked}
    for key in ('mainMatches', 'releaseMatches'):
        if isinstance(value.get(key), bool):
            answer[key] = value[key]
    if isinstance(value.get('releaseTag'), str) and re.fullmatch(r'v?[A-Za-z0-9._-]{1,64}', value['releaseTag']):
        answer['releaseTag'] = value['releaseTag']
    return answer


def _github_comparison(commit: str | None, version: str, *, opener=urlopen,
                       checked_at=None) -> dict:
    """Read public main/release metadata quickly; never infer current while offline."""
    checked_at = checked_at or _timestamp()
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Madison-runtime-status'}
    try:
        main_request = Request('https://api.github.com/repos/broadsidecode/madison/commits/main', headers=headers)
        release_request = Request('https://api.github.com/repos/broadsidecode/madison/releases/latest', headers=headers)
        with opener(main_request, timeout=2) as response:
            main = json.loads(response.read().decode('utf-8'))
        with opener(release_request, timeout=2) as response:
            release = json.loads(response.read().decode('utf-8'))
        main_commit = main.get('sha') if isinstance(main, dict) else None
        release_tag = release.get('tag_name') if isinstance(release, dict) else None
        if (not isinstance(main_commit, str) or not _HEX40.fullmatch(main_commit.lower()) or
                not isinstance(release_tag, str) or not re.fullmatch(r'v?[A-Za-z0-9._-]{1,64}', release_tag)):
            raise ValueError('Unexpected GitHub response')
        main_matches = bool(commit and main_commit.lower() == commit.lower())
        release_matches = release_tag == f'v{version}'
        status = 'current' if main_matches and release_matches else 'different'
        return {'status': status, 'checkedAt': checked_at, 'mainMatches': main_matches,
                'releaseMatches': release_matches, 'releaseTag': release_tag}
    except Exception:
        return {'status': 'not_verified', 'checkedAt': checked_at}


def capture_runtime_identity(project_label=None, config_fingerprint=None,
                             github_checker=None, *, package_root=None, version=None,
                             instance_id=None, start_identity=None,
                             started_at=None, pid=None) -> dict:
    """Capture code and supplemental source facts once, before the server starts."""
    package_root = Path(package_root or Path(__file__).resolve().parent).resolve()
    version = version or __version__
    build_id = source_fingerprint(package_root)
    release = _release_manifest(package_root, version, build_id)
    git = _git_details(package_root)
    source = {
        'commit': release['commit'] if release else git.get('commit') if git else None,
        'branch': git.get('branch') if git else None,
        'localChanges': bool(git and git.get('localChanges')),
        'drift': False,
        'manifestVerified': release is not None,
    }
    stable = json.dumps({'version': version, 'buildId': build_id,
                         'configFingerprint': config_fingerprint}, sort_keys=True,
                        separators=(',', ':')).encode('utf-8')
    answer = {
        'protocolVersion': PROTOCOL_VERSION,
        'version': version,
        'buildId': build_id,
        'startIdentity': start_identity or sha256(stable).hexdigest(),
        'instanceId': instance_id or secrets.token_hex(16),
        'startedAt': started_at or _timestamp(),
        'pid': pid if isinstance(pid, int) and pid > 0 else os.getpid(),
        'configFingerprint': config_fingerprint,
        'installType': 'release-zip' if release else 'checkout' if git else 'portable',
        'source': source,
        'github': (_github_comparison(source['commit'], version)
                   if github_checker is None else _github_result(github_checker)),
    }
    if isinstance(project_label, str) and project_label.strip():
        answer['projectLabel'] = project_label.strip()[:200]
    return answer


def source_drift(identity: dict, package_root=None) -> bool:
    """Compare disk source with startup without changing the frozen identity."""
    package_root = Path(package_root or Path(__file__).resolve().parent).resolve()
    try:
        return source_fingerprint(package_root) != identity.get('buildId')
    except OSError:
        return True
