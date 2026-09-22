"""Local read-only viewer server. It never exposes a whole project directory."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import json
import mimetypes
import re
import os
import socket

from .manifest import load_manifest, asset_files

WEB = Path(__file__).resolve().parent / 'web'


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow a second listener to hijack a bound port.
    allow_reuse_address = os.name != 'nt'
    daemon_threads = True

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(bundle, port=8765):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('Port must be an integer from 0 to 65535.')
    bundle = Path(bundle).resolve()
    manifest = load_manifest(bundle / 'data.json')
    approved = asset_files(bundle, manifest)
    fixed = {'/': WEB / 'index.html', '/index.html': WEB / 'index.html',
             '/app.js': WEB / 'app.js', '/styles.css': WEB / 'styles.css'}
    for file in fixed.values():
        if not file.is_file():
            raise ValueError(f'Application file is missing: {file.name}')
    data = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def base_headers(self, status, length, mime):
            self.send_response(status)
            self.send_header('Content-Length', str(length))
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

        def message(self, code, body, head=False, mime='text/plain; charset=utf-8'):
            body = body.encode('utf-8') if isinstance(body, str) else body
            self.base_headers(code, len(body), mime)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self.send_file(False)

        def do_HEAD(self):
            self.send_file(True)

        def reject_write(self):
            self.close_connection = True
            self.message(405, 'This viewer is read only.')

        do_POST = reject_write
        do_PUT = reject_write
        do_PATCH = reject_write
        do_DELETE = reject_write
        do_OPTIONS = reject_write

        def send_file(self, head):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') not in (expected, '127.0.0.1'):
                return self.message(403, 'Use the IPv4 loopback address printed by the server.', head)
            try:
                if len(self.path) > 4096 or self.path.startswith('//'):
                    raise ValueError('Invalid request path')
                route = unquote(urlsplit(self.path).path, errors='strict')
                if '\x00' in route or '\\' in route or any(ord(c) < 32 for c in route):
                    raise ValueError('Invalid request path')
                if '..' in route.split('/'):
                    return self.message(403, 'Not available.', head)
            except (ValueError, UnicodeError):
                return self.message(400, 'Malformed request path.', head)
            if route == '/health':
                return self.message(200, json.dumps({'service': 'timeline-reviewer', 'readOnly': True}), head, 'application/json')
            if route == '/data.json':
                return self.message(200, data, head, 'application/json; charset=utf-8')
            if route == '/favicon.ico':
                return self.message(204, b'', head, 'image/x-icon')
            original = fixed.get(route) or approved.get(route)
            if original is None:
                return self.message(404, 'Not found.', head)
            # Resolve again: a media symlink changed after startup must not escape.
            target = original.resolve()
            allowed_root = WEB.resolve() if route in fixed else bundle
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
