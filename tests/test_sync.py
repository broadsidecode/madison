"""Synthetic end to end checks for the optional local CapCut sync control."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from timeline_reviewer.server import make_server
from timeline_reviewer.sync import CapCutSync, _diff
from timeline_reviewer import tesseract
from timeline_reviewer.capcut import file_hash, inspect_project
from test_capcut import fixture, write_json
from test_manifest import manifest


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.clip = {
            'id': 'clip-one', 'name': 'clip.mp4', 'track_index': 1,
            'source_timerange_us': {'start': 0, 'duration': 2_000_000},
            'target_timerange_us': {'start': 1_000_000, 'duration': 2_000_000},
            'type': 'video', 'path': 'clip.mp4', 'hidden': False,
            'volume': 1, 'speed': 1, 'transform': {},
        }

    def compare(self, **updates):
        old = dict(self.clip)
        new = dict(self.clip, **updates)
        return _diff({'duration': 3.0, 'segments': [old]},
                     {'duration': 3.0, 'segments': [new]})

    def test_lane_change_is_not_a_timeline_move(self):
        diff = self.compare(track_index=2)
        self.assertEqual((diff['laneChanged'], diff['moved'], diff['nudged']), (1, 0, 0))
        self.assertTrue(any('Changed lane' in text for text in diff['examples']))

    def test_microsecond_rounding_is_not_trim_or_move(self):
        diff = self.compare(
            source_timerange_us={'start': 1, 'duration': 1_999_999},
            target_timerange_us={'start': 1_000_001, 'duration': 1_999_999})
        self.assertEqual((diff['trimmed'], diff['moved'], diff['nudged']), (0, 0, 0))

    def test_start_shift_over_fifty_milliseconds_is_moved(self):
        diff = self.compare(target_timerange_us={'start': 1_050_001, 'duration': 2_000_000})
        self.assertEqual((diff['moved'], diff['nudged'], diff['laneChanged']), (1, 0, 0))

    def test_small_timing_change_is_nudged_and_real_trim_is_counted(self):
        diff = self.compare(
            source_timerange_us={'start': 1_001, 'duration': 1_998_999},
            target_timerange_us={'start': 1_050_000, 'duration': 1_998_999})
        self.assertEqual((diff['nudged'], diff['moved'], diff['trimmed']), (1, 0, 1))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.github = patch('timeline_reviewer.identity._github_comparison',
                            return_value={'status': 'not_verified',
                                          'checkedAt': '2026-09-23T12:00:00Z'})
        self.github.start()
        self.addCleanup(self.github.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.draft, _ = fixture(self.root)
        self.bundle = self.root / 'review'
        (self.bundle / 'media').mkdir(parents=True)
        (self.bundle / 'media/movie.mp4').write_bytes(b'old movie')
        (self.bundle / 'data.json').write_text(json.dumps(manifest()), encoding='utf-8')
        self.sync = CapCutSync(self.bundle, self.project, 'First', self.root / 'sync-output')
        self.server = make_server(self.bundle, 0, capcut_sync=self.sync)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method='GET', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            try:
                decoded = json.loads(raw)
            except ValueError:
                decoded = raw.decode('utf-8', errors='replace')
            return response.status, decoded
        finally:
            connection.close()

    def test_state_exposes_comparison_without_local_paths_and_blocks_missing_media(self):
        code, state = self.request('/capcut-sync-state')
        self.assertEqual(code, 200)
        self.assertEqual(state['diff']['added'], 1)
        self.assertTrue(state['canSync'])
        self.assertNotIn(str(self.root), json.dumps(state))
        self.draft['materials']['videos'][0]['path'] = 'missing.mp4'
        write_json(self.project / 'Timelines/timeline-one/draft_content.json', self.draft)
        _, state = self.request('/capcut-sync-state')
        self.assertFalse(state['canSync'])
        self.assertEqual(state['missingMedia'][0]['name'], 'missing.mp4')
        self.assertNotIn(str(self.root), json.dumps(state))

    def test_source_hash_and_cross_origin_are_required(self):
        _, state = self.request('/capcut-sync-state')
        payload = json.dumps({'expectedSourceHash': state['sourceHash'], 'allowLossy': False})
        headers = {'Content-Type': 'application/json', 'X-Madison-Edit-Token': state['csrfToken'],
                   'Origin': 'https://attacker.invalid'}
        self.assertEqual(self.request('/capcut-sync-start', 'POST', payload, headers)[0], 403)
        headers['Origin'] = f'http://127.0.0.1:{self.server.server_port}'
        self.draft['tracks'][0]['segments'][0]['target_timerange']['start'] = 2_000_000
        write_json(self.project / 'Timelines/timeline-one/draft_content.json', self.draft)
        code, answer = self.request('/capcut-sync-start', 'POST', payload, headers)
        self.assertEqual(code, 409)
        self.assertIn('changed', answer['error'])
        self.assertEqual(json.loads((self.bundle / 'data.json').read_text()), manifest())

    def test_lossy_warning_requires_explicit_acceptance(self):
        self.draft['tracks'][0]['segments'][0]['enable_color_match_adjust'] = True
        write_json(self.project / 'Timelines/timeline-one/draft_content.json', self.draft)
        _, state = self.request('/capcut-sync-state')
        self.assertTrue(state['requiresLossy'])
        self.assertEqual(state['unsupported'][0]['count'], 1)
        payload = json.dumps({'expectedSourceHash': state['sourceHash'], 'allowLossy': False})
        headers = {'Content-Type': 'application/json', 'X-Madison-Edit-Token': state['csrfToken'],
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        code, answer = self.request('/capcut-sync-start', 'POST', payload, headers)
        self.assertEqual(code, 409)
        self.assertIn('explicitly accept', answer['error'])

    def test_trimmed_audio_warns_even_without_capcut_effect_flags(self):
        self.draft['materials']['audios'] = [{'id': 'music', 'path': str(self.root / 'synthetic.mp4'),
                                               'duration': 6_000_000}]
        self.draft['tracks'].append({'type': 'audio', 'segments': [{
            'id': 'music-segment', 'material_id': 'music',
            'source_timerange': {'start': 1_000_000, 'duration': 1_000_000},
            'target_timerange': {'start': 0, 'duration': 1_000_000},
            'visible': True, 'volume': 1, 'speed': 1}]})
        write_json(self.project / 'Timelines/timeline-one/draft_content.json', self.draft)
        _, state = self.request('/capcut-sync-state')
        self.assertTrue(state['requiresLossy'])
        caveat = next(item for item in state['unsupported'] if item['code'] == 'audio_source_offset')
        self.assertEqual(caveat['count'], 1)
        self.assertNotIn(str(self.root), json.dumps(state))

    def test_old_review_stays_live_until_matching_render_and_bundle_are_ready(self):
        gate = threading.Event()
        entered = threading.Event()
        source_hashes = {str(path): file_hash(path) for path in self.project.rglob('*.json')}

        def imported(project, timeline, output, cli, allow_lossy, render):
            self.assertTrue(render)
            self.assertFalse(allow_lossy)
            report = inspect_project(project, timeline)
            output.mkdir()
            (output / 'timeline.tsrct').write_bytes(b'synthetic native output')
            (output / 'preview.mp4').write_bytes(b'new movie')
            hashes = {item['path']: item['sha256'] for item in report['source_files']}
            hashes[str(self.root / 'synthetic.mp4')] = file_hash(self.root / 'synthetic.mp4')
            report.update({'rendered': True, 'source_hashes': hashes,
                           'layer_mapping': [{'segment_id': 'segment-one', 'layer_id': 1}]})
            entered.set()
            self.assertTrue(gate.wait(5))
            return report

        def prepared(movie, ready, timeline, title=None):
            ready.mkdir()
            (ready / 'media').mkdir()
            (ready / 'media/preview.mp4').write_bytes(b'new movie')
            (ready / 'media/poster.jpg').write_bytes(b'poster')
            data = json.loads(timeline.read_text(encoding='utf-8'))
            data.update({'videoUrl': 'media/preview.mp4', 'posterUrl': 'media/poster.jpg',
                         'revision': 'prepared'})
            (ready / 'data.json').write_text(json.dumps(data), encoding='utf-8')

        with patch('timeline_reviewer.sync.import_project', side_effect=imported), \
             patch('timeline_reviewer.sync.probe', return_value=({}, {}, 6.0, 30.0)), \
             patch('timeline_reviewer.sync.prepare_bundle', side_effect=prepared):
            _, state = self.request('/capcut-sync-state')
            payload = json.dumps({'expectedSourceHash': state['sourceHash'], 'allowLossy': False})
            headers = {'Content-Type': 'application/json', 'X-Madison-Edit-Token': state['csrfToken'],
                       'Origin': f'http://127.0.0.1:{self.server.server_port}'}
            code, running = self.request('/capcut-sync-start', 'POST', payload, headers)
            self.assertEqual((code, running['state']), (202, 'running'))
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.request('/data.json')[1]['revision'], 'sample')
            self.assertEqual(self.request('/capcut-sync-status')[1]['state'], 'running')
            gate.set()
            for _ in range(100):
                result = self.request('/capcut-sync-status')[1]
                if result['state'] != 'running':
                    break
                time.sleep(.02)
            self.assertEqual(result['state'], 'complete', result)
            new = self.request('/data.json')[1]
            self.assertEqual(new['revision'], result['revision'])
            self.assertEqual(new['tracks'][0]['clips'][0]['layerId'], 1)
            self.assertEqual(self.request('/' + new['videoUrl'])[0], 200)
            self.assertEqual((self.bundle / 'media/movie.mp4').read_bytes(), b'old movie')
            self.assertEqual(source_hashes, {str(path): file_hash(path) for path in self.project.rglob('*.json')})
            self.assertTrue((self.sync.snapshot_path).is_file())
            self.assertTrue(self.request('/capcut-sync-state')[1]['noChanges'])
            code, answer = self.request('/capcut-sync-start', 'POST', payload, headers)
            self.assertEqual(code, 409)
            self.assertIn('already synced', answer['error'])

    def test_failed_render_retains_original_review_and_source(self):
        old_manifest = (self.bundle / 'data.json').read_bytes()
        source_hashes = {str(path): file_hash(path) for path in self.project.rglob('*.json')}
        _, state = self.request('/capcut-sync-state')
        payload = json.dumps({'expectedSourceHash': state['sourceHash'], 'allowLossy': False})
        headers = {'Content-Type': 'application/json', 'X-Madison-Edit-Token': state['csrfToken'],
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        with patch('timeline_reviewer.sync.import_project', side_effect=ValueError('Synthetic render failed')):
            self.assertEqual(self.request('/capcut-sync-start', 'POST', payload, headers)[0], 202)
            for _ in range(100):
                result = self.request('/capcut-sync-status')[1]
                if result['state'] != 'running':
                    break
                time.sleep(.02)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual((self.bundle / 'data.json').read_bytes(), old_manifest)
        self.assertEqual((self.bundle / 'media/movie.mp4').read_bytes(), b'old movie')
        self.assertEqual(source_hashes, {str(path): file_hash(path) for path in self.project.rglob('*.json')})

    def test_pending_madison_edit_blocks_capcut_sync(self):
        class PendingEditor:
            def state(self):
                return {'operations': [{'type': 'trim'}]}

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.server = make_server(self.bundle, 0, edit_session=PendingEditor(), capcut_sync=self.sync)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, state = self.request('/capcut-sync-state')
        payload = json.dumps({'expectedSourceHash': state['sourceHash'], 'allowLossy': False})
        headers = {'Content-Type': 'application/json', 'X-Madison-Edit-Token': state['csrfToken'],
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        code, result = self.request('/capcut-sync-start', 'POST', payload, headers)
        self.assertEqual(code, 409)
        self.assertIn('pending Madison edits', result['error'])
        self.assertEqual(self.sync.status()['state'], 'idle')

    def test_native_export_removes_conflicting_dxc_folder_only_for_child(self):
        app_data = 'sample-runtime'
        conflict = str(Path(app_data) / 'Programs' / 'Obsidian')
        original = f'system;{conflict};tools'
        completed = Mock(returncode=0, stdout='ok', stderr='')
        with patch.object(tesseract.platform, 'system', return_value='Windows'), \
             patch.dict(tesseract.os.environ, {'LOCALAPPDATA': app_data, 'PATH': original}), \
             patch.object(tesseract.subprocess, 'run', return_value=completed) as called:
            self.assertEqual(tesseract._run(['tsrct.exe', 'export']), 'ok')
            self.assertEqual(called.call_args.kwargs['env']['PATH'], 'system;tools')
            self.assertEqual(tesseract.os.environ['PATH'], original)


if __name__ == '__main__':
    unittest.main()
