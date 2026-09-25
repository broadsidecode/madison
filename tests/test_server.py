import http.client
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

from timeline_reviewer.server import make_server
from test_manifest import manifest


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.github = patch('timeline_reviewer.identity._github_comparison',
                            return_value={'status': 'not_verified',
                                          'checkedAt': '2026-09-23T12:00:00Z'})
        self.github.start()
        self.addCleanup(self.github.stop)
        self.engine = patch('timeline_reviewer.server.engine_status',
                            return_value={'state': 'not_verified', 'installed': None})
        self.engine.start()
        self.addCleanup(self.engine.stop)
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

    def test_runtime_status_reports_sanitized_engine_state(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        done = threading.Event()

        def checker():
            done.set()
            return {'state': 'madison_update', 'installed': '0.2.0', 'official': '9.9.9',
                    'checkedAt': '2026-09-24T12:00:00Z', 'path': 'private-location'}
        self.server = make_server(self.root, 0, engine_checker=checker)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertTrue(done.wait(2))
        for _ in range(50):
            engine = json.loads(self.request('/runtime-status')[2])['tesseract']
            if engine['state'] != 'checking':
                break
            threading.Event().wait(.02)
        self.assertEqual(engine['state'], 'madison_update')
        self.assertTrue(engine['attention'])
        self.assertEqual(engine['official'], '9.9.9')
        self.assertNotIn('path', engine)

    def test_engine_checker_failure_is_not_verified(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

        def checker():
            raise RuntimeError('offline')
        self.server = make_server(self.root, 0, engine_checker=checker)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        for _ in range(50):
            engine = json.loads(self.request('/runtime-status')[2])['tesseract']
            if engine['state'] != 'checking':
                break
            threading.Event().wait(.02)
        self.assertEqual(engine['state'], 'not_verified')
        self.assertFalse(engine['attention'])

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

    def test_health_and_runtime_status_report_frozen_identity_without_paths_or_tokens(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        identity = {
            'protocolVersion': 1, 'version': '9.8.7', 'buildId': 'build-123',
            'startIdentity': 'start-123', 'instanceId': 'instance-123',
            'startedAt': '2026-09-23T12:00:00Z', 'pid': 4321,
            'configFingerprint': 'config-123', 'installType': 'checkout',
            'source': {'commit': 'a' * 40, 'branch': 'main', 'localChanges': False,
                       'drift': False, 'manifestVerified': False},
            'github': {'status': 'current', 'checkedAt': '2026-09-23T12:00:01Z',
                       'mainMatches': True, 'releaseMatches': True,
                       'releaseTag': 'v9.8.7'},
        }
        self.server = make_server(self.root, 0, runtime_identity=identity,
                                  shutdown_token='do-not-expose')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        health = json.loads(self.request('/health')[2])
        self.assertEqual({key: health[key] for key in ('protocolVersion', 'version', 'buildId',
                         'instanceId', 'startIdentity', 'configFingerprint', 'pid')},
                         {key: identity[key] for key in ('protocolVersion', 'version', 'buildId',
                         'instanceId', 'startIdentity', 'configFingerprint', 'pid')})
        status = json.loads(self.request('/runtime-status')[2])
        self.assertEqual(status['projectLabel'], 'Sample review')
        self.assertEqual(status['modes'], {'review': True, 'editing': False, 'capcutSync': False})
        self.assertTrue(status['activity']['restartSafe'])
        self.assertEqual(status['github'], {'status': 'current',
                         'checkedAt': '2026-09-23T12:00:01Z',
                         'mainMatches': True, 'releaseMatches': True,
                         'releaseTag': 'v9.8.7'})
        for route, name in [('/', 'index.html'), ('/app.js', 'app.js'), ('/styles.css', 'styles.css')]:
            self.assertEqual(status['assets'][name], sha256(self.request(route)[2]).hexdigest())
        self.assertNotIn('do-not-expose', json.dumps(status))
        self.assertNotIn(str(self.root), json.dumps(status))

    def test_runtime_identity_rejects_values_that_could_escape_the_page_metadata(self):
        with self.assertRaises(ValueError):
            make_server(self.root, 0, runtime_identity={
                'version': '9.8.7\"><script>', 'buildId': 'safe-build',
                'instanceId': 'safe-instance'})

    def test_served_application_is_snapshotted_and_index_is_build_stamped(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        web = self.root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<!doctype html><html><head><link rel="stylesheet" href="./styles.css"><script src="./app.js" defer></script></head><body><meta name="madison-runtime-anchor"></body></html>', encoding='utf-8')
        (web / 'app.js').write_text('const SNAPSHOT = 1;', encoding='utf-8')
        (web / 'styles.css').write_text('body{color:red}', encoding='utf-8')
        identity = {'version': '9.8.7', 'buildId': 'build-abc', 'instanceId': 'instance-abc'}
        with patch('timeline_reviewer.server.WEB', web):
            self.server = make_server(self.root, 0, runtime_identity=identity)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (web / 'app.js').write_text('const SNAPSHOT = 2;', encoding='utf-8')
        body = self.request('/')[2].decode('utf-8')
        self.assertIn('./app.js?v=build-abc', body)
        self.assertIn('name="madison-build" content="build-abc"', body)
        self.assertEqual(self.request('/app.js?v=build-abc')[2], b'const SNAPSHOT = 1;')
        self.assertTrue(json.loads(self.request('/runtime-status')[2])['source']['drift'])

    def test_browser_heartbeat_is_same_origin_tokenized_and_marks_old_page_stale(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        identity = {'version': '9.8.7', 'buildId': 'build-new', 'instanceId': 'instance-new'}
        self.server = make_server(self.root, 0, runtime_identity=identity)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, page_headers, page_bytes = self.request('/')
        page = page_bytes.decode('utf-8')
        import re
        token = re.search(r'MadisonHeartbeat=([^;]+)', page_headers['Set-Cookie']).group(1)
        self.assertNotIn(token, page)
        body = json.dumps({'clientId': 'stale-tab', 'buildId': 'build-old', 'instanceId': 'instance-old',
                           'unappliedEdits': True,
                           'features': {'versionStatus': True, 'projectStatus': True,
                                        'capcutControl': True}}).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Content-Length': str(len(body)),
                   'Origin': f'http://127.0.0.1:{self.server.server_port}',
                   'Cookie': f'MadisonHeartbeat={token}'}
        no_body_headers = dict(headers)
        no_body_headers.pop('Content-Length')
        self.assertEqual(self.request('/runtime-heartbeat', 'POST', no_body_headers)[0], 411)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', '/runtime-heartbeat', body=body, headers=headers)
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 200)
        finally:
            connection.close()
        browser = json.loads(self.request('/runtime-status')[2])['browser']
        self.assertTrue(browser['connected'])
        self.assertTrue(browser['stale'])
        self.assertFalse(browser['verified'])
        self.assertEqual(browser['features'], {'versionStatus': False, 'projectStatus': False,
                                               'capcutControl': False})
        with patch('timeline_reviewer.server.time.monotonic', return_value=10 ** 12):
            expired = json.loads(self.request('/runtime-status')[2])
        self.assertFalse(expired['browser']['connected'])
        self.assertEqual(expired['browser']['features'], {'versionStatus': False,
                                                          'projectStatus': False,
                                                          'capcutControl': False})
        self.assertTrue(expired['activity']['restartSafe'])

    def test_multiple_browser_tabs_cannot_overwrite_each_others_active_work(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        identity = {'version': '9.8.7', 'buildId': 'build-tabs', 'instanceId': 'instance-tabs'}
        self.server = make_server(self.root, 0, runtime_identity=identity)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, headers, _ = self.request('/')
        import re
        token = re.search(r'MadisonHeartbeat=([^;]+)', headers['Set-Cookie']).group(1)
        origin = f'http://127.0.0.1:{self.server.server_port}'

        def heartbeat(client_id, dirty):
            value = {'clientId': client_id, 'buildId': 'build-tabs',
                     'instanceId': 'instance-tabs', 'unappliedEdits': dirty,
                     'features': {'versionStatus': True, 'projectStatus': True,
                                  'capcutControl': True}}
            body = json.dumps(value).encode('utf-8')
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            try:
                connection.request('POST', '/runtime-heartbeat', body=body,
                                   headers={'Content-Type': 'application/json',
                                            'Origin': origin,
                                            'Cookie': f'MadisonHeartbeat={token}'})
                response = connection.getresponse(); response.read()
                self.assertEqual(response.status, 200)
            finally:
                connection.close()

        heartbeat('dirty-tab', True)
        heartbeat('clean-tab', False)
        status = json.loads(self.request('/runtime-status')[2])
        self.assertTrue(status['browser']['verified'])
        self.assertEqual(status['browser']['clientCount'], 2)
        self.assertFalse(status['activity']['restartSafe'])

    def test_shutdown_requires_token_and_refuses_unsafe_restart(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        class PendingEditor:
            def state(self):
                return {'operations': [{'type': 'trim'}], 'renderPending': False}
            def source_files(self):
                return {}
        self.server = make_server(self.root, 0, edit_session=PendingEditor(),
                                  shutdown_token='launcher-secret')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertEqual(self.request('/launcher/shutdown', 'POST',
                         {'X-Madison-Shutdown-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('/launcher/shutdown', 'POST',
                         {'Authorization': 'Bearer launcher-secret'})[0], 409)

    def test_only_manifest_approved_files_are_exposed(self):
        self.assertEqual(self.request('/private.json')[0], 404)
        self.assertEqual(self.request('/data.json')[0], 200)
        self.assertEqual(self.request('/media/movie.mp4')[2], self.media)
        self.assertEqual(self.request('/app.js')[0], 200)

    def test_application_contains_truthful_runtime_status_and_stale_page_controls(self):
        page = self.request('/')[2].decode('utf-8')
        app = self.request('/app.js')[2].decode('utf-8')
        self.assertIn('id="runtime-summary"', page)
        self.assertIn('id="runtime-warning"', page)
        self.assertIn("fetch('./runtime-status'", app)
        self.assertIn("fetch('./runtime-heartbeat'", app)
        self.assertIn("credentials: 'same-origin'", app)
        self.assertIn('keepalive: true', app)
        self.assertIn('This Madison page is stale', app)

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

    def test_manifest_refresh_reads_new_media_without_exposing_other_files(self):
        changed = manifest()
        changed['revision'] = 'updated-review'
        changed['videoUrl'] = 'media/updated.mp4'
        (self.root / 'media/updated.mp4').write_bytes(b'new movie')
        (self.root / 'data.new.json').write_text(json.dumps(changed))
        (self.root / 'data.new.json').replace(self.root / 'data.json')
        self.assertEqual(json.loads(self.request('/data.json')[2])['revision'], 'updated-review')
        self.assertEqual(self.request('/media/updated.mp4')[2], b'new movie')
        self.assertEqual(self.request('/private.json')[0], 404)

    def test_bound_editor_requires_origin_and_token_then_rechecks_revision(self):
        class FakeEditor:
            media_roots = (self.root,)

            def __init__(self, root):
                self.root = root
                self.operations = []
                self.token = 'base'

            def state(self):
                result = deepcopy(manifest())
                result['tracks'][0]['clips'][0]['layerId'] = 7
                result['tracks'][0]['clips'][0]['hidden'] = bool(self.operations)
                return {'sourceRevision': 'native', 'token': self.token,
                        'operations': self.operations, 'manifest': result,
                        'renderPending': False}

            def source_files(self):
                return {'clip-one': {'path': self.root / 'media/movie.mp4',
                                     'canvas': {'width': 1080, 'height': 1920}}}

            def save_draft(self, operations, expected_revision):
                if expected_revision != self.token:
                    raise ValueError('Draft changed')
                self.operations = operations
                self.token = 'changed' if operations else 'base'

            def commit(self, expected_revision):
                if expected_revision != self.token or not self.operations:
                    raise ValueError('No current edits')
                self.operations = []
                self.token = 'saved'
                return {'savedProject': self.root / 'saved.tsrct'}

        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.server = make_server(self.root, 0, edit_session=FakeEditor(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertFalse(json.loads(self.request('/health')[2])['readOnly'])
        state = json.loads(self.request('/edit-state')[2])
        self.assertTrue(state['enabled'])
        self.assertEqual(state['sources']['clip-one']['canvas']['width'], 1080)
        source = state['sources']['clip-one']['url']
        self.assertEqual(self.request(source, headers={'Range': 'bytes=3-6'})[2], self.media[3:7])
        self.assertEqual(self.request(source.replace('clip-one', 'unknown'))[0], 404)
        payload = json.dumps({'revision': state['revision'],
                              'operations': [{'type': 'remove', 'clipId': 'clip-one', 'layerId': 7}]})
        base = {'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
                'X-Madison-Edit-Token': state['csrfToken']}
        def post(headers, body=payload):
            import http.client
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            try:
                connection.request('POST', '/edit-draft', body=body, headers=headers)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        self.assertEqual(post({**base, 'Origin': 'https://attacker.invalid'})[0], 403)
        self.assertEqual(post({**base, 'Origin': f'http://127.0.0.1:{self.server.server_port}',
                               'X-Madison-Edit-Token': 'wrong'})[0], 403)
        origin = {**base, 'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        status, body = post(origin)
        self.assertEqual(status, 200)
        updated = json.loads(body)
        self.assertTrue(updated['manifest']['tracks'][0]['clips'][0]['hidden'])
        self.assertEqual(post(origin)[0], 409)

    def test_external_project_change_falls_back_to_fresh_read_only_review(self):
        class StaleEditor:
            def state(self):
                raise ValueError('The native project changed outside Madison')

            def source_files(self):
                return {}

        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.server = make_server(self.root, 0, edit_session=StaleEditor())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        changed = manifest()
        changed['revision'] = 'agent-new-cut'
        changed['duration'] = 3
        changed['tracks'][0]['clips'][0]['end'] = 3
        changed['tracks'][0]['clips'][0]['duration'] = 3
        (self.root / 'data.next.json').write_text(json.dumps(changed))
        (self.root / 'data.next.json').replace(self.root / 'data.json')
        state = json.loads(self.request('/edit-state')[2])
        self.assertFalse(state['enabled'])
        self.assertTrue(state['rebindRequired'])
        self.assertEqual(json.loads(self.request('/data.json')[2])['revision'], 'agent-new-cut')

    def test_source_request_after_external_project_change_returns_503(self):
        class ChangedSource:
            media_roots = (self.root,)
            calls = 0

            def __init__(self, root):
                self.root = root

            def state(self):
                return {'sourceRevision': 'native', 'token': 'draft',
                        'operations': [], 'manifest': manifest()}

            def source_files(self):
                self.calls += 1
                if self.calls > 1:
                    raise ValueError('The project changed outside Madison')
                return {'clip-one': {'path': self.root / 'media/movie.mp4'}}

        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.server = make_server(self.root, 0, edit_session=ChangedSource(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        source = json.loads(self.request('/edit-state')[2])['sources']['clip-one']['url']
        self.assertEqual(self.request(source)[0], 503)
        self.assertEqual(self.request('/health')[0], 200)


if __name__ == '__main__':
    unittest.main()
