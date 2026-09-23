"""Portable single-instance launcher for the local Madison server."""
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser


PROTOCOL_VERSION = 1
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8464
SERVICE_NAMES = {'madison', 'timeline-reviewer'}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def app_data_dir(env=None, home=None, platform_name=None):
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == 'win32':
        base = env.get('LOCALAPPDATA')
        return (Path(base) if base else home / 'AppData' / 'Local') / 'Madison'
    if platform_name == 'darwin':
        return home / 'Library' / 'Application Support' / 'Madison'
    base = env.get('XDG_STATE_HOME')
    return (Path(base) if base else home / '.local' / 'state') / 'madison'


def default_config():
    return {'mode': 'demo', 'host': DEFAULT_HOST, 'port': DEFAULT_PORT}


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'Invalid private launcher file: {path.name}: {exc}') from exc


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    with suppress(OSError):
        temp.chmod(0o600)
    os.replace(temp, path)


def load_config(state_dir=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    stored = _read_json(state_dir / 'config.json', default=None)
    config = default_config() if stored is None else stored
    return validate_config(config)


def validate_config(config):
    config = dict(config)
    mode = config.get('mode', 'demo')
    if mode not in ('demo', 'serve'):
        raise ValueError('Mode must be demo or serve.')
    host = config.get('host', DEFAULT_HOST)
    if host != DEFAULT_HOST:
        raise ValueError('Madison must bind to 127.0.0.1.')
    port = config.get('port', DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Port must be an integer from 1 to 65535.')
    config['mode'], config['host'], config['port'] = mode, host, port
    if mode == 'serve' and not config.get('bundle'):
        raise ValueError('Serve mode requires a review bundle.')
    capcut = [config.get('capcutProject'), config.get('capcutTimeline'), config.get('syncRoot')]
    if any(capcut) and not all(capcut):
        raise ValueError('CapCut project, timeline, and sync root must be configured together.')
    if mode == 'demo':
        for name in ('bundle', 'editableProject', 'mediaRoots', 'tesseract',
                     'capcutProject', 'capcutTimeline', 'syncRoot', 'projectLabel'):
            config.pop(name, None)
    return config


def configure(state_dir=None, *, mode='demo', port=DEFAULT_PORT, bundle=None,
              editable_project=None, media_roots=None, tesseract=None,
              capcut_project=None, capcut_timeline=None, sync_root=None,
              project_label=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    config = {'mode': mode, 'host': DEFAULT_HOST, 'port': port}
    path_fields = {
        'bundle': bundle, 'editableProject': editable_project,
        'tesseract': tesseract, 'capcutProject': capcut_project,
        'syncRoot': sync_root,
    }
    for key, value in path_fields.items():
        if value is not None:
            config[key] = str(Path(value).expanduser().resolve())
    roots = [str(Path(item).expanduser().resolve()) for item in (media_roots or [])]
    if roots:
        config['mediaRoots'] = roots
    if capcut_timeline:
        config['capcutTimeline'] = str(capcut_timeline)
    if project_label:
        config['projectLabel'] = str(project_label)
    config = validate_config(config)
    previous = load_config(state_dir)
    if previous != config:
        root = selected_app_root(state_dir)
        package = root / 'timeline_reviewer'
        build = current_build_id(package) if package.is_dir() else None
        record_rollback(state_dir, app_root=root, config=previous, build_id=build)
    _atomic_json(state_dir / 'config.json', config)
    return config


def config_fingerprint(config):
    payload = json.dumps(validate_config(config), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def current_build_id(root=None):
    root = Path(__file__).resolve().parent if root is None else Path(root)
    from .identity import source_fingerprint
    return source_fingerprint(root)


def process_start_identity(pid):
    if pid <= 0:
        return None
    if sys.platform == 'win32':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                   f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; $p.StartTime.ToUniversalTime().Ticks"]
    elif sys.platform.startswith('linux'):
        try:
            return (Path('/proc') / str(pid) / 'stat').read_text(encoding='utf-8').split()[21]
        except (OSError, IndexError):
            return None
    else:
        command = ['ps', '-p', str(pid), '-o', 'lstart=']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def process_matches(pid, expected_start_identity):
    actual = process_start_identity(int(pid))
    return bool(actual and expected_start_identity and actual == expected_start_identity)


class LaunchLock:
    def __init__(self, state_dir, alive=process_matches):
        self.path = Path(state_dir) / 'launch.lock'
        self.alive = alive
        self.held = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        own_identity = process_start_identity(os.getpid()) or f'launcher-{os.getpid()}'
        value = {'pid': os.getpid(), 'startIdentity': own_identity, 'createdAt': utc_now()}
        for _ in range(2):
            try:
                handle = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(handle, 'w', encoding='utf-8') as file:
                    json.dump(value, file)
                self.held = True
                return self
            except FileExistsError:
                existing = _read_json(self.path, default={}) or {}
                if self.alive(existing.get('pid', -1), existing.get('startIdentity')):
                    raise RuntimeError('A Madison launch is already in progress.')
                with suppress(FileNotFoundError):
                    self.path.unlink()
        raise RuntimeError('Could not acquire the Madison launch lock.')

    def release(self):
        if self.held:
            with suppress(FileNotFoundError):
                self.path.unlink()
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def process_record_path(state_dir):
    return Path(state_dir) / 'process.json'


def write_process_record(state_dir, record):
    _atomic_json(process_record_path(state_dir), record)


def probe_json(port, route, timeout=1.0, method='GET', token=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(f'http://{DEFAULT_HOST}:{int(port)}{route}', method=method, headers=headers,
                      data=b'' if method != 'GET' else None)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def request_shutdown(port, token):
    result = probe_json(port, '/launcher/shutdown', timeout=3, method='POST', token=token)
    if not result or not result.get('accepted'):
        raise RuntimeError('Madison refused the authenticated launcher shutdown request.')


def wait_for_exit(pid, identity=None, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if identity:
            if not process_matches(pid, identity):
                return True
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
        time.sleep(0.1)
    return False


def _command_line(pid):
    if sys.platform == 'win32':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                   f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"]
    elif sys.platform.startswith('linux'):
        try:
            return (Path('/proc') / str(pid) / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
        except OSError:
            return ''
    else:
        command = ['ps', '-p', str(pid), '-o', 'command=']
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def verified_madison_command(pid):
    command = _command_line(pid).lower().replace('\\', '/')
    return ('timeline_reviewer' in command or 'timeline-reviewer' in command) and ('python' in command or 'madison' in command)


def find_port_owner_pid(port):
    if sys.platform == 'win32':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                   f"(Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort {int(port)} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)"]
    else:
        command = ['lsof', '-nP', f'-iTCP@127.0.0.1:{int(port)}', '-sTCP:LISTEN', '-t']
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False).stdout.strip().splitlines()
        return int(output[0]) if output else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def terminate_process(pid):
    if sys.platform == 'win32':
        result = subprocess.run(['taskkill.exe', '/PID', str(int(pid))], capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise RuntimeError('The verified legacy Madison process could not be stopped safely.')
    else:
        os.kill(int(pid), 15)


def _legacy_restart_safe(port):
    edit = probe_json(port, '/edit-state', timeout=1)
    capcut = probe_json(port, '/capcut-sync-status', timeout=1)
    if edit is None or capcut is None:
        return False
    edit_busy = bool(edit.get('enabled') and (edit.get('operations') or edit.get('renderPending')))
    capcut_busy = bool(capcut.get('enabled') and capcut.get('state') not in (None, 'idle', 'complete', 'no-change'))
    return not edit_busy and not capcut_busy


def _managed_health_matches(health, record):
    return bool(health and health.get('protocolVersion') == PROTOCOL_VERSION
                and health.get('instanceId') == record.get('instanceId')
                and health.get('pid') == record.get('pid')
                and health.get('startIdentity') == record.get('startIdentity')
                and process_matches(record.get('pid', -1), record.get('processStartIdentity')))


def _build_command(config, state_dir, app_root, runtime):
    command = [sys.executable, '-m', 'timeline_reviewer', '_run', config['mode'],
               '--port', str(config['port']), '--state-dir', str(Path(state_dir).resolve()),
               '--instance-id', runtime['instanceId'], '--start-identity', runtime['startIdentity'],
               '--build-id', runtime['buildId'], '--config-fingerprint', runtime['configFingerprint'],
               '--shutdown-token', runtime['shutdownToken'], '--app-root', str(Path(app_root).resolve())]
    if config['mode'] == 'serve':
        command.extend(['--bundle', config['bundle']])
        option_map = [('editableProject', '--editable-project'), ('tesseract', '--tesseract'),
                      ('capcutProject', '--capcut-project'), ('capcutTimeline', '--capcut-timeline'),
                      ('syncRoot', '--sync-root'), ('projectLabel', '--project-label')]
        for key, flag in option_map:
            if config.get(key):
                command.extend([flag, config[key]])
        for root in config.get('mediaRoots', []):
            command.extend(['--media-root', root])
    return command


def spawn_server(config, state_dir, app_root, runtime):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    log = (state_dir / 'launcher.log').open('ab', buffering=0)
    options = {'cwd': str(Path(app_root).resolve()), 'stdin': subprocess.DEVNULL,
               'stdout': log, 'stderr': subprocess.STDOUT, 'close_fds': True}
    if sys.platform == 'win32':
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
    else:
        options['start_new_session'] = True
    try:
        return subprocess.Popen(_build_command(config, state_dir, app_root, runtime), **options)
    finally:
        log.close()


@dataclass(frozen=True)
class LaunchResult:
    action: str
    url: str
    pid: int
    instance_id: str


def _browser_url(url, identity):
    query = urlencode({'buildId': identity['buildId'], 'instanceId': identity['instanceId']})
    return f'{url}?{query}'


def _wait_for_handshake(port, expected, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = probe_json(port, '/health', timeout=0.5)
        if health and all(health.get(key) == expected.get(key)
                          for key in ('protocolVersion', 'instanceId', 'pid', 'buildId',
                                      'configFingerprint', 'startIdentity')):
            return health
        time.sleep(0.1)
    raise RuntimeError('Madison started but did not complete its ownership handshake.')


def launch(state_dir=None, *, open_browser=True, app_root=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    app_root = Path(__file__).resolve().parents[1] if app_root is None else Path(app_root).resolve()
    config = load_config(state_dir)
    port = config['port']
    url = f'http://{DEFAULT_HOST}:{port}/'
    with LaunchLock(state_dir):
        build = current_build_id(app_root / 'timeline_reviewer')
        fingerprint = config_fingerprint(config)
        record = _read_json(process_record_path(state_dir), default={}) or {}
        health = probe_json(port, '/health', timeout=1)
        if health:
            if _managed_health_matches(health, record):
                correct = health.get('buildId') == build and health.get('configFingerprint') == fingerprint
                if correct:
                    if open_browser:
                        webbrowser.open(_browser_url(url, health))
                    return LaunchResult('reused', url, record['pid'], record['instanceId'])
                status = probe_json(port, '/runtime-status', timeout=1)
                if not status or not status.get('activity', {}).get('restartSafe'):
                    raise RuntimeError('Madison has active work. Resolve imports, renders, or unapplied edits before restart.')
                record_rollback(state_dir, app_root=record.get('appRoot', app_root),
                                config=record.get('config', config),
                                build_id=record.get('buildId'))
                request_shutdown(port, record['shutdownToken'])
                if not wait_for_exit(record['pid'], record.get('processStartIdentity')):
                    raise RuntimeError('The old Madison process did not stop; it was left untouched.')
                record = {}
            elif health.get('service') in SERVICE_NAMES and not health.get('protocolVersion'):
                owner = find_port_owner_pid(port)
                if not owner or not verified_madison_command(owner):
                    raise RuntimeError(f'Port {port} is occupied by an unrelated application. It was left untouched.')
                if not _legacy_restart_safe(port):
                    raise RuntimeError('Legacy Madison has active work or unknown state. Resolve it before restart.')
                owner_identity = process_start_identity(owner)
                if (not owner_identity or not verified_madison_command(owner)
                        or process_start_identity(owner) != owner_identity):
                    raise RuntimeError(f'Port {port} is occupied by an unrelated application. It was left untouched.')
                terminate_process(owner)
                if not wait_for_exit(owner, owner_identity):
                    raise RuntimeError('Legacy Madison did not stop; it was left untouched.')
            else:
                raise RuntimeError(f'Port {port} is occupied by an unrelated application. It was left untouched.')
        elif find_port_owner_pid(port) is not None:
            raise RuntimeError(f'Port {port} is occupied by an unrelated application. It was left untouched.')

        if record and process_matches(record.get('pid', -1), record.get('processStartIdentity')):
            raise RuntimeError('A recorded Madison process is still running without a verified handshake. It was left untouched.')
        from .identity import capture_runtime_identity
        runtime = capture_runtime_identity(config.get('projectLabel'), fingerprint,
                                           package_root=app_root / 'timeline_reviewer',
                                           instance_id=secrets.token_urlsafe(18))
        if runtime['buildId'] != build:
            raise RuntimeError('Madison source changed during launch. Launch again.')
        runtime['shutdownToken'] = secrets.token_urlsafe(32)
        process = spawn_server(config, state_dir, app_root, runtime)
        actual_start = None
        deadline = time.monotonic() + 3
        while actual_start is None and time.monotonic() < deadline:
            actual_start = process_start_identity(process.pid)
            if actual_start is None:
                time.sleep(0.05)
        if actual_start is None:
            raise RuntimeError('Madison started but its process identity could not be verified.')
        record = {**runtime, 'pid': process.pid, 'processStartIdentity': actual_start,
                  'appRoot': str(app_root), 'config': config,
                  'startedAt': utc_now(), 'port': port}
        write_process_record(state_dir, record)
        expected = {**runtime, 'pid': process.pid}
        _wait_for_handshake(port, expected)
        if open_browser:
            webbrowser.open(_browser_url(url, expected))
        return LaunchResult('started', url, process.pid, runtime['instanceId'])


def stop(state_dir=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    record = _read_json(process_record_path(state_dir), default={}) or {}
    port = record.get('port', load_config(state_dir)['port'])
    health = probe_json(port, '/health', timeout=1)
    if not record or not _managed_health_matches(health, record):
        raise RuntimeError('No owned Madison instance is available to stop.')
    status = probe_json(port, '/runtime-status', timeout=1)
    if not status or not status.get('activity', {}).get('restartSafe'):
        raise RuntimeError('Madison has active work. Resolve imports, renders, or unapplied edits before shutdown.')
    request_shutdown(port, record['shutdownToken'])
    if not wait_for_exit(record['pid'], record.get('processStartIdentity')):
        raise RuntimeError('Madison did not stop; the process was left untouched.')
    return True


def record_rollback(state_dir=None, *, app_root, config=None, build_id=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    selection = {'appRoot': str(Path(app_root).expanduser().resolve()),
                 'config': validate_config(config or load_config(state_dir)),
                 'recordedAt': utc_now()}
    if build_id:
        selection['buildId'] = str(build_id)
    _atomic_json(state_dir / 'rollback.json', selection)
    return selection


def rollback(state_dir=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    selection = _read_json(state_dir / 'rollback.json', default=None)
    if not selection:
        raise RuntimeError('No previous Madison launcher selection is recorded.')
    config = validate_config(selection['config'])
    _atomic_json(state_dir / 'config.json', config)
    active = {'appRoot': str(Path(selection['appRoot']).resolve()), 'selectedAt': utc_now()}
    if selection.get('buildId'):
        active['buildId'] = selection['buildId']
    _atomic_json(state_dir / 'app-selection.json', active)
    return active


def select_app(state_dir=None, app_root=None, *, build_id=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    if app_root is None:
        raise ValueError('An application folder is required.')
    new_root = Path(app_root).expanduser().resolve()
    if not (new_root / 'timeline_reviewer').is_dir():
        raise ValueError('The selected Madison application folder does not exist.')
    current_root = selected_app_root(state_dir)
    current_selection = _read_json(state_dir / 'app-selection.json', default={}) or {}
    current_build = current_selection.get('buildId')
    if current_build is None and (current_root / 'timeline_reviewer').is_dir():
        current_build = current_build_id(current_root / 'timeline_reviewer')
    if new_root != current_root or (build_id and build_id != current_build):
        record_rollback(state_dir, app_root=current_root, config=load_config(state_dir),
                        build_id=current_build)
    active = {'appRoot': str(new_root), 'selectedAt': utc_now()}
    if build_id:
        active['buildId'] = str(build_id)
    _atomic_json(state_dir / 'app-selection.json', active)
    return active


def selected_app_root(state_dir=None):
    state_dir = app_data_dir() if state_dir is None else Path(state_dir)
    selection = _read_json(state_dir / 'app-selection.json', default=None)
    if selection and selection.get('appRoot'):
        return Path(selection['appRoot']).resolve()
    return Path(__file__).resolve().parents[1]


def parser():
    root = argparse.ArgumentParser(description='Configure and open one private local Madison instance.')
    root.add_argument('--state-dir', type=Path, help=argparse.SUPPRESS)
    commands = root.add_subparsers(dest='command', required=True)
    launch_parser = commands.add_parser('launch', help='Start or reuse the configured local Madison server')
    launch_parser.add_argument('--no-open', action='store_true')
    commands.add_parser('stop', help='Stop the owned local Madison server when restart safe')
    config = commands.add_parser('configure', help='Save the private local project selection')
    config.add_argument('--mode', choices=('demo', 'serve'), default='demo')
    config.add_argument('--port', type=int, default=DEFAULT_PORT)
    config.add_argument('--bundle', type=Path)
    config.add_argument('--editable-project', type=Path)
    config.add_argument('--media-root', type=Path, action='append', default=[])
    config.add_argument('--tesseract', type=Path)
    config.add_argument('--capcut-project', type=Path)
    config.add_argument('--capcut-timeline')
    config.add_argument('--sync-root', type=Path)
    config.add_argument('--project-label')
    rollback_parser = commands.add_parser('rollback', help='Select the previously recorded application and config')
    rollback_parser.add_argument('--launch', action='store_true')
    select_parser = commands.add_parser('select-app', help='Select a Madison application folder and record rollback')
    select_parser.add_argument('app_root', type=Path)
    select_parser.add_argument('--build-id')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    state_dir = args.state_dir or app_data_dir()
    try:
        if args.command == 'configure':
            config = configure(state_dir, mode=args.mode, port=args.port, bundle=args.bundle,
                               editable_project=args.editable_project, media_roots=args.media_root,
                               tesseract=args.tesseract, capcut_project=args.capcut_project,
                               capcut_timeline=args.capcut_timeline, sync_root=args.sync_root,
                               project_label=args.project_label)
            print(f"Madison configuration saved for {config.get('projectLabel', config['mode'])}.")
        elif args.command == 'launch':
            result = launch(state_dir, open_browser=not args.no_open,
                            app_root=selected_app_root(state_dir))
            print(f'Madison {result.action}: {result.url}')
        elif args.command == 'stop':
            stop(state_dir)
            print('Madison stopped.')
        elif args.command == 'rollback':
            selection = rollback(state_dir)
            print(f"Madison launcher restored: {selection['appRoot']}")
            if args.launch:
                result = launch(state_dir, app_root=selection['appRoot'])
                print(f'Madison {result.action}: {result.url}')
        elif args.command == 'select-app':
            selection = select_app(state_dir, args.app_root, build_id=args.build_id)
            print(f"Madison application selected: {selection['appRoot']}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
