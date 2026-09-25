"""Local read-only viewer server. It never exposes a whole project directory."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote, urlsplit
import json
from hashlib import sha256
import mimetypes
import re
import os
import socket
import secrets
import threading
import time
from datetime import datetime, timezone

from .engine import engine_status, public_status as public_engine_status
from .identity import capture_runtime_identity, source_drift
from .manifest import load_manifest, asset_files

WEB = Path(__file__).resolve().parent / 'web'
HEARTBEAT_FRESH_SECONDS = 15


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow a second listener to hijack a bound port.
    allow_reuse_address = os.name != 'nt'
    daemon_threads = True

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(bundle, port=8765, edit_session=None, capcut_sync=None,
                runtime_identity=None, shutdown_token=None, engine_checker=None):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('Port must be an integer from 0 to 65535.')
    bundle = Path(bundle).resolve()
    manifest = load_manifest(bundle / 'data.json')
    approved = asset_files(bundle, manifest)
    manifest_stamp = (bundle / 'data.json').stat().st_mtime_ns
    manifest_cache = (manifest, approved, manifest_stamp)
    edit_lock = threading.RLock()
    edit_token = secrets.token_urlsafe(32)
    source_token = secrets.token_urlsafe(24)
    heartbeat_token = secrets.token_urlsafe(32)
    web_paths = {'index': WEB / 'index.html', 'app': WEB / 'app.js',
                 'styles': WEB / 'styles.css'}
    for file in web_paths.values():
        if not file.is_file():
            raise ValueError(f'Application file is missing: {file.name}')
    web_snapshot = {name: path.read_bytes() for name, path in web_paths.items()}
    captured = capture_runtime_identity()
    if runtime_identity is not None:
        if not isinstance(runtime_identity, dict):
            raise ValueError('Runtime identity must be a JSON object.')
        captured.update({key: runtime_identity[key] for key in (
            'protocolVersion', 'version', 'buildId', 'startIdentity', 'instanceId',
            'startedAt', 'pid', 'configFingerprint', 'installType', 'source', 'github',
            'projectLabel') if key in runtime_identity})
    runtime = json.loads(json.dumps(captured))
    for required in ('version', 'buildId', 'instanceId'):
        if (not isinstance(runtime.get(required), str) or
                not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', runtime[required])):
            raise ValueError(f'Runtime identity is missing {required}.')
    runtime.setdefault('protocolVersion', 1)
    runtime.setdefault('startIdentity', runtime['buildId'])
    runtime.setdefault('startedAt', datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'))
    runtime.setdefault('pid', os.getpid())
    runtime.setdefault('configFingerprint', None)
    runtime.setdefault('installType', 'portable')
    runtime.setdefault('source', {'commit': None, 'branch': None, 'localChanges': False,
                                  'drift': False, 'manifestVerified': False})
    runtime.setdefault('github', {'status': 'not_verified', 'checkedAt': None})
    build_query = runtime['buildId']
    index = web_snapshot['index'].decode('utf-8')
    index = index.replace('href="./styles.css"', f'href="./styles.css?v={build_query}"')
    index = index.replace('src="./app.js"', f'src="./app.js?v={build_query}"')
    runtime_meta = (f'<meta name="madison-version" content="{runtime["version"]}">'
                    f'<meta name="madison-build" content="{runtime["buildId"]}">'
                    f'<meta name="madison-instance" content="{runtime["instanceId"]}">')
    anchor = '<meta name="madison-runtime-anchor">'
    index = index.replace(anchor, runtime_meta) if anchor in index else index.replace('</head>', f'  {runtime_meta}\n</head>')
    web_snapshot['index'] = index.encode('utf-8')
    asset_hashes = {'index.html': sha256(web_snapshot['index']).hexdigest(),
                    'app.js': sha256(web_snapshot['app']).hexdigest(),
                    'styles.css': sha256(web_snapshot['styles']).hexdigest()}
    fixed = {'/': ('index', 'text/html; charset=utf-8'),
             '/index.html': ('index', 'text/html; charset=utf-8'),
             '/app.js': ('app', 'application/javascript; charset=utf-8'),
             '/styles.css': ('styles', 'text/css; charset=utf-8')}
    browser_lock = threading.Lock()
    browser_clients = {}
    engine_lock = threading.Lock()
    engine_state = {'value': None}
    engine_cli = getattr(edit_session, 'cli', None) or getattr(capcut_sync, 'cli', None)

    def check_engine():
        # Runs once per server start, off the request path. Failures stay
        # "not verified"; the viewer never waits on the network or the engine.
        try:
            value = (engine_checker or (lambda: engine_status(engine_cli)))()
        except Exception:
            value = {'state': 'not_verified'}
        with engine_lock:
            engine_state['value'] = value

    threading.Thread(target=check_engine, name='madison-engine-status', daemon=True).start()

    def browser_snapshot():
        now = time.monotonic()
        with browser_lock:
            expired = [key for key, value in browser_clients.items()
                       if now - value['_seenMonotonic'] > HEARTBEAT_FRESH_SECONDS or
                       now < value['_seenMonotonic']]
            for key in expired:
                del browser_clients[key]
            clients = [dict(value) for value in browser_clients.values()]
        current = [value for value in clients if not value['stale']]
        feature_names = ('versionStatus', 'projectStatus', 'capcutControl')
        features = {name: any(value['features'][name] for value in current)
                    for name in feature_names}
        verified = any(all(value['features'][name] for name in feature_names)
                       for value in current)
        newest = max(clients, key=lambda value: value['_seenMonotonic']) if clients else None
        return {'connected': bool(clients), 'verified': verified,
                'stale': any(value['stale'] for value in clients),
                'lastSeenAt': newest['lastSeenAt'] if newest else None,
                'unappliedEdits': any(value['unappliedEdits'] for value in clients),
                'features': features, 'clientCount': len(clients)}

    def current_bundle():
        nonlocal manifest_cache
        stamp = (bundle / 'data.json').stat().st_mtime_ns
        if stamp != manifest_cache[2]:
            updated = load_manifest(bundle / 'data.json')
            manifest_cache = (updated, asset_files(bundle, updated), stamp)
        return manifest_cache[0], manifest_cache[1]

    def edit_state():
        if edit_session is None:
            return {'enabled': False}
        state = edit_session.state()
        source_records = edit_session.source_files()
        sources = {}
        for clip_id, record in source_records.items():
            sources[clip_id] = {'url': f'/edit-source/{source_token}/{clip_id}'}
            if isinstance(record, dict) and record.get('transform') is not None:
                sources[clip_id]['transform'] = record['transform']
            if isinstance(record, dict) and record.get('canvas') is not None:
                sources[clip_id]['canvas'] = record['canvas']
        return {'enabled': True, 'revision': state['token'],
                'sourceRevision': state['sourceRevision'], 'csrfToken': edit_token,
                'operations': state['operations'], 'manifest': state['manifest'],
                'sources': sources, 'renderPending': bool(state.get('renderPending', False))}

    def activate_native(document, output):
        nonlocal edit_session
        if edit_session is None:
            return False
        from .editing import EditSession
        with edit_lock:
            roots = [*edit_session.media_roots, output]
            # Validate every new layer against the matching native project
            # before moving the clean draft from the previous review.
            preflight = EditSession(output / 'review-bundle', document,
                                    cli=edit_session.cli, media_roots=roots)
            preflight_record, _, preflight_native, _, _ = preflight._current()
            for track in preflight_record['baseManifest']['tracks']:
                for clip in track['clips']:
                    if clip.get('layerId') is not None:
                        preflight._target(preflight_record['baseManifest'], preflight_native,
                                          clip['id'], clip['layerId'])
            # A clean draft still binds the previous native checksum. Keep it
            # with the retained version before creating a fresh edit session.
            draft = edit_session.draft_path
            if draft.is_file():
                archived = output / 'previous-edit-draft.json'
                if archived.exists():
                    raise ValueError('Cannot preserve the previous edit draft.')
                os.replace(draft, archived)
            updated = EditSession(bundle, document, cli=edit_session.cli, media_roots=roots)
            record, _, native, _, _ = updated._current()
            for track in record['baseManifest']['tracks']:
                for clip in track['clips']:
                    if clip.get('layerId') is not None:
                        updated._target(record['baseManifest'], native, clip['id'], clip['layerId'])
            edit_session = updated
            return True

    def activity_state():
        operations, render_pending = [], False
        if edit_session is not None:
            try:
                state = edit_session.state()
                operations = state.get('operations') or []
                render_pending = bool(state.get('renderPending', False))
            except (OSError, ValueError, AttributeError):
                # Unknown editor state is not safe to restart.
                operations = [None]
        sync_state = 'idle'
        if capcut_sync is not None:
            try:
                sync_state = capcut_sync.status().get('state', 'unknown')
            except (OSError, ValueError, AttributeError):
                sync_state = 'unknown'
        browser_dirty = bool(browser_snapshot()['unappliedEdits'])
        restart_safe = not operations and not render_pending and not browser_dirty and sync_state in ('idle', 'complete', 'failed')
        return {'capcutSync': sync_state, 'unappliedEdits': bool(operations or browser_dirty),
                'renderPending': render_pending, 'restartSafe': restart_safe}

    def public_runtime_status():
        source = runtime.get('source') if isinstance(runtime.get('source'), dict) else {}
        public_source = {key: source.get(key) for key in (
            'commit', 'branch', 'localChanges', 'manifestVerified')}
        public_source['drift'] = source_drift(runtime)
        github = runtime.get('github') if isinstance(runtime.get('github'), dict) else {}
        github_status = github.get('status')
        if github_status not in ('current', 'different', 'ahead', 'behind', 'diverged', 'not_verified'):
            github_status = 'not_verified'
        browser = browser_snapshot()
        current, _ = current_bundle()
        with engine_lock:
            tesseract = public_engine_status(engine_state['value'])
        public_github = {'status': github_status,
                         'checkedAt': github.get('checkedAt') if isinstance(github.get('checkedAt'), str) else None}
        for key in ('mainMatches', 'releaseMatches'):
            if isinstance(github.get(key), bool):
                public_github[key] = github[key]
        if isinstance(github.get('releaseTag'), str):
            public_github['releaseTag'] = github['releaseTag']
        return {
            'protocolVersion': runtime['protocolVersion'], 'version': runtime['version'],
            'buildId': runtime['buildId'], 'instanceId': runtime['instanceId'],
            'startIdentity': runtime['startIdentity'], 'startedAt': runtime['startedAt'],
            'pid': runtime['pid'], 'configFingerprint': runtime['configFingerprint'],
            'installType': runtime['installType'],
            'projectLabel': runtime.get('projectLabel') or current.get('title') or 'Untitled project',
            'source': public_source,
            'modes': {'review': True, 'editing': edit_session is not None,
                      'capcutSync': capcut_sync is not None},
            'activity': activity_state(),
            'assets': dict(asset_hashes),
            'github': public_github,
            'tesseract': tesseract,
            'browser': browser,
        }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def base_headers(self, status, length, mime, extra_headers=None):
            self.send_response(status)
            self.send_header('Content-Length', str(length))
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)

        def message(self, code, body, head=False, mime='text/plain; charset=utf-8', extra_headers=None):
            body = body.encode('utf-8') if isinstance(body, str) else body
            self.base_headers(code, len(body), mime, extra_headers)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self.send_file(False)

        def do_HEAD(self):
            self.send_file(True)

        def do_POST(self):
            route = self.route(False)
            if route is None:
                return
            if route == '/runtime-heartbeat':
                return self.runtime_heartbeat()
            if route == '/launcher/shutdown':
                return self.launcher_shutdown()
            sync_route = route == '/capcut-sync-start' and capcut_sync is not None
            if not sync_route and (edit_session is None or route not in ('/edit-draft', '/edit-commit')):
                return self.reject_write()
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Origin') != expected_origin or self.headers.get('X-Madison-Edit-Token') != edit_token:
                return self.message(403, 'Edit request was not authorized.')
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.message(415, 'Send JSON for edit requests.')
            try:
                length = int(self.headers.get('Content-Length', ''))
                if length < 2 or length > 64 * 1024:
                    raise ValueError('Edit request size is unsupported')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('Send a JSON object.')
                if sync_route:
                    if set(request) != {'expectedSourceHash', 'allowLossy'}:
                        raise ValueError('Supply the reviewed source hash and allowLossy choice.')
                    with edit_lock:
                        if edit_session is not None and edit_session.state()['operations']:
                            raise ValueError('Save or discard pending Madison edits before syncing CapCut.')
                    callback = activate_native if edit_session is not None else None
                    result = capcut_sync.start(request['expectedSourceHash'], request['allowLossy'], callback)
                    return self.message(202, json.dumps(result, ensure_ascii=False),
                                        mime='application/json; charset=utf-8')
                if not isinstance(request.get('revision'), str):
                    raise ValueError('An edit revision is required')
                with edit_lock:
                    if capcut_sync is not None and capcut_sync.status()['state'] == 'running':
                        raise ValueError('CapCut sync is running. Wait for it to finish before editing.')
                    if route == '/edit-draft':
                        if set(request) != {'revision', 'operations'} or not isinstance(request['operations'], list):
                            raise ValueError('Supply a complete list of draft operations')
                        edit_session.save_draft(request['operations'], request['revision'])
                        result = edit_state()
                    else:
                        if set(request) != {'revision'}:
                            raise ValueError('Supply the current draft revision')
                        committed = edit_session.commit(request['revision'])
                        result = edit_state()
                        result['savedProject'] = str(committed['savedProject'])
                        result['renderPending'] = True
            except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                return self.message(409, json.dumps({'error': str(exc)}), mime='application/json; charset=utf-8')
            except OSError as exc:
                return self.message(500, json.dumps({'error': str(exc)}), mime='application/json; charset=utf-8')
            return self.message(200, json.dumps(result, ensure_ascii=False), mime='application/json; charset=utf-8')

        def runtime_heartbeat(self):
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                presented = cookie['MadisonHeartbeat'].value if 'MadisonHeartbeat' in cookie else ''
            except Exception:
                presented = ''
            if (self.headers.get('Origin') != expected_origin or
                    not secrets.compare_digest(presented, heartbeat_token)):
                return self.message(403, 'Browser status was not authorized.')
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.message(415, 'Send JSON for browser status.')
            try:
                length = int(self.headers.get('Content-Length', ''))
                if length < 2 or length > 4096:
                    return self.message(411, 'Browser status size is invalid.')
                value = json.loads(self.rfile.read(length))
                features = value.get('features') if isinstance(value, dict) else None
                if (not isinstance(value, dict) or set(value) != {'clientId', 'buildId', 'instanceId', 'unappliedEdits', 'features'} or
                        not isinstance(value['clientId'], str) or
                        not re.fullmatch(r'[A-Za-z0-9._-]{8,80}', value['clientId']) or
                        not isinstance(value['buildId'], str) or not isinstance(value['instanceId'], str) or
                        not isinstance(value['unappliedEdits'], bool) or not isinstance(features, dict) or
                        set(features) != {'versionStatus', 'projectStatus', 'capcutControl'} or
                        not all(isinstance(item, bool) for item in features.values())):
                    raise ValueError
            except (ValueError, UnicodeError, json.JSONDecodeError):
                return self.message(400, 'Browser status is invalid.')
            with browser_lock:
                browser_clients[value['clientId']] = {
                    'stale': value['buildId'] != runtime['buildId'] or value['instanceId'] != runtime['instanceId'],
                    'lastSeenAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                    'unappliedEdits': value['unappliedEdits'],
                    'features': dict(features),
                    '_seenMonotonic': time.monotonic(),
                }
            return self.message(200, json.dumps({'accepted': True}), mime='application/json; charset=utf-8')

        def launcher_shutdown(self):
            authorization = self.headers.get('Authorization', '')
            presented = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else self.headers.get('X-Madison-Shutdown-Token', '')
            if not shutdown_token or not secrets.compare_digest(presented, shutdown_token):
                return self.message(403, 'Launcher shutdown was not authorized.')
            if not activity_state()['restartSafe']:
                return self.message(409, 'Madison has active work and cannot restart safely.')
            self.message(202, json.dumps({'accepted': True}), mime='application/json; charset=utf-8')
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def reject_write(self):
            self.close_connection = True
            self.message(405, 'This viewer is read only.')

        do_PUT = reject_write
        do_PATCH = reject_write
        do_DELETE = reject_write
        do_OPTIONS = reject_write

        def route(self, head):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') not in (expected, '127.0.0.1'):
                self.message(403, 'Use the IPv4 loopback address printed by the server.', head)
                return None
            try:
                if len(self.path) > 4096 or self.path.startswith('//'):
                    raise ValueError('Invalid request path')
                route = unquote(urlsplit(self.path).path, errors='strict')
                if '\x00' in route or '\\' in route or any(ord(c) < 32 for c in route):
                    raise ValueError('Invalid request path')
                if '..' in route.split('/'):
                    self.message(403, 'Not available.', head)
                    return None
            except (ValueError, UnicodeError):
                self.message(400, 'Malformed request path.', head)
                return None
            return route

        def send_file(self, head):
            route = self.route(head)
            if route is None:
                return
            if route == '/health':
                health = {key: runtime.get(key) for key in ('protocolVersion', 'version', 'buildId',
                          'instanceId', 'startIdentity', 'configFingerprint', 'pid')}
                health.update({'service': 'timeline-reviewer', 'readOnly': edit_session is None})
                return self.message(200, json.dumps(health), head, 'application/json')
            if route == '/runtime-status':
                try:
                    status = public_runtime_status()
                except (OSError, ValueError):
                    return self.message(503, json.dumps({'error': 'Runtime status is not ready.'}), head,
                                        'application/json; charset=utf-8')
                return self.message(200, json.dumps(status, ensure_ascii=False), head,
                                    'application/json; charset=utf-8')
            if route == '/edit-state':
                with edit_lock:
                    try:
                        state = edit_state()
                    except (ValueError, OSError) as exc:
                        # A newer bundle can still be reviewed immediately. Disable
                        # native edits until the agent binds its matching project.
                        state = {'enabled': False, 'rebindRequired': True,
                                 'error': str(exc)}
                    return self.message(200, json.dumps(state, ensure_ascii=False), head, 'application/json; charset=utf-8')
            if route == '/capcut-sync-state':
                if capcut_sync is None:
                    return self.message(200, json.dumps({'enabled': False}), head,
                                        'application/json; charset=utf-8')
                try:
                    current, _ = current_bundle()
                    state = capcut_sync.state(current['revision'])
                    state['csrfToken'] = edit_token
                except (ValueError, OSError) as exc:
                    return self.message(503, json.dumps({'enabled': True,
                        'error': 'The selected CapCut timeline could not be inspected. Check the saved project and local media.'}), head,
                                        'application/json; charset=utf-8')
                return self.message(200, json.dumps(state, ensure_ascii=False), head,
                                    'application/json; charset=utf-8')
            if route == '/capcut-sync-status':
                state = capcut_sync.status() if capcut_sync is not None else {'enabled': False, 'state': 'idle'}
                return self.message(200, json.dumps(state, ensure_ascii=False), head,
                                    'application/json; charset=utf-8')
            if route == '/data.json':
                try:
                    current, _ = current_bundle()
                except (OSError, ValueError) as exc:
                    return self.message(503, f'Review data is not ready: {exc}', head)
                return self.message(200, json.dumps(current, ensure_ascii=False, allow_nan=False), head, 'application/json; charset=utf-8')
            if route == '/favicon.ico':
                return self.message(204, b'', head, 'image/x-icon')
            source_route = route.startswith(f'/edit-source/{source_token}/')
            source_original = None
            if source_route and edit_session is not None:
                clip_id = route.removeprefix(f'/edit-source/{source_token}/')
                try:
                    record = edit_session.source_files().get(clip_id)
                except (ValueError, OSError):
                    return self.message(503, 'The source preview is stale. Refresh the review.', head)
                if record is not None:
                    source_original = Path(record['path'] if isinstance(record, dict) else record)
                    if source_original.suffix.lower() not in ('.mp4', '.webm', '.mov', '.m4v'):
                        source_original = None
            try:
                _, approved = current_bundle()
            except (OSError, ValueError) as exc:
                return self.message(503, f'Review data is not ready: {exc}', head)
            fixed_asset = fixed.get(route)
            if fixed_asset is not None:
                data = web_snapshot[fixed_asset[0]]
                extra = ({'Set-Cookie': f'MadisonHeartbeat={heartbeat_token}; Path=/; HttpOnly; SameSite=Strict'}
                         if fixed_asset[0] == 'index' else None)
                return self.message(200, data, head, fixed_asset[1], extra)
            original = source_original or approved.get(route)
            if original is None:
                return self.message(404, 'Not found.', head)
            # Resolve again: a media symlink changed after startup must not escape.
            target = original.resolve()
            allowed_root = target.parent if source_original is not None else bundle
            if source_original is not None and not any(
                    target.is_relative_to(root.resolve()) for root in edit_session.media_roots):
                return self.message(403, 'Source media is outside the approved folders.', head)
            if not target.is_relative_to(allowed_root) or not target.is_file():
                return self.message(403, 'Asset is no longer available.', head)
            try:
                stream = target.open('rb')
            except OSError:
                return self.message(404, 'Asset is unavailable.', head)
            with stream:
                import os
                size = os.fstat(stream.fileno()).st_size
                start, end, partial = 0, size - 1, False
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', requested)
                    if not match or not any(match.groups()):
                        return self.bad_range(size)
                    if match[1]:
                        start = int(match[1])
                        end = min(end, int(match[2])) if match[2] else end
                    else:
                        count = int(match[2])
                        if count == 0:
                            return self.bad_range(size)
                        start = max(0, size - count)
                    if start >= size or end < start:
                        return self.bad_range(size)
                    partial = True
                mime = {'.js': 'application/javascript; charset=utf-8', '.mp4': 'video/mp4', '.webm': 'video/webm'}.get(target.suffix.lower(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                self.base_headers(206 if partial else 200, max(0, end - start + 1), mime)
                self.send_header('Accept-Ranges', 'bytes')
                if partial:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                if head:
                    return
                try:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        part = stream.read(min(remaining, 256 * 1024))
                        if not part:
                            break
                        self.wfile.write(part)
                        remaining -= len(part)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        def bad_range(self, size):
            self.base_headers(416, 0, 'text/plain')
            self.send_header('Content-Range', f'bytes */{size}')
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = LocalServer(('127.0.0.1', port), Handler)
    return server
