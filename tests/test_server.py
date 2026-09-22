import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from timeline_reviewer.server import make_server
from test_manifest import manifest


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'media').mkdir()
        self.media = bytes(range(100))
        (self.root / 'media/movie.mp4').write_bytes(self.media)
        (self.root / 'data.json').write_text(json.dumps(manifest()))
        (self.root / 'private.json').write_text('{"private":true}')
        self.server = make_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path='/', method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_loopback_headers_health_and_app(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        code, headers, body = self.request()
        self.assertEqual(code, 200); self.assertIn(b'<!doctype html>', body.lower())
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertTrue(json.loads(self.request('/health')[2])['readOnly'])

    def test_only_manifest_approved_files_are_exposed(self):
        self.assertEqual(self.request('/private.json')[0], 404)
        self.assertEqual(self.request('/data.json')[0], 200)
        self.assertEqual(self.request('/media/movie.mp4')[2], self.media)
        self.assertEqual(self.request('/app.js')[0], 200)

    def test_head_and_byte_ranges(self):
        status, headers, data = self.request('/media/movie.mp4', 'HEAD')
        self.assertEqual((status, headers['Content-Length'], data), (200, '100', b''))
        for ranged, start, end in [('bytes=5-14', 5, 14), ('bytes=-10', 90, 99), ('bytes=95-', 95, 99), ('bytes=95-999', 95, 99)]:
            status, headers, data = self.request('/media/movie.mp4', headers={'Range': ranged})
            self.assertEqual(status, 206)
            self.assertEqual(headers['Content-Range'], f'bytes {start}-{end}/100')
            self.assertEqual(data, self.media[start:end+1])

    def test_invalid_ranges_and_parser_errors_do_not_crash_service(self):
        for ranged in ['bytes=-0', 'bytes=101-', 'bytes=20-10', 'bytes=0-1,4-5', 'bytes=-', 'bytes=' + '9' * 5000 + '-']:
            with self.subTest(ranged=ranged[:60]):
                self.assertEqual(self.request('/media/movie.mp4', headers={'Range': ranged})[0], 416)
        for path in ['/%00.js', '//[', '/' + 'x' * 4100]:
            self.assertIn(self.request(path)[0], (400, 404))
        self.assertEqual(self.request('/health')[0], 200)

    def test_traversal_bad_host_and_writes(self):
        for path in ['/../private.json', '/%2e%2e/private.json', '/media\\movie.mp4']:
            self.assertIn(self.request(path)[0], (400, 403))
        self.assertEqual(self.request('/data.json', headers={'Host': 'attacker.invalid'})[0], 403)
        for method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            self.assertEqual(self.request('/data.json', method)[0], 405)
        self.assertEqual(json.loads((self.root / 'data.json').read_text()), manifest())

    def test_occupied_port_fails_without_switching_or_stopping_anything(self):
        with self.assertRaises(OSError):
            make_server(self.root, self.server.server_port)
        self.assertEqual(self.request('/health')[0], 200)


if __name__ == '__main__':
    unittest.main()
