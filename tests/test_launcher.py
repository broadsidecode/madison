import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from timeline_reviewer import launcher
from timeline_reviewer.identity import source_fingerprint
from timeline_reviewer.__main__ import parser as main_parser
from timeline_reviewer.__main__ import main as main_cli


class FakeProcess:
    def __init__(self, pid=4321):
        self.pid = pid


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_config_is_portable_demo_on_reserved_address(self):
        config = launcher.load_config(self.state)
        self.assertEqual('demo', config['mode'])
        self.assertEqual('127.0.0.1', config['host'])
        self.assertEqual(8464, config['port'])
        self.assertNotIn('bundle', config)

    def test_launcher_build_identity_matches_runtime_identity(self):
        package = Path(__file__).resolve().parents[1] / 'timeline_reviewer'
        self.assertEqual(source_fingerprint(package), launcher.current_build_id(package))

    def test_main_cli_exposes_configure_launch_and_rollback(self):
        self.assertEqual('launch', main_parser().parse_args(['launch', '--no-open']).command)
        self.assertEqual('stop', main_parser().parse_args(['stop']).command)
        self.assertEqual('configure', main_parser().parse_args(['configure']).command)
        self.assertEqual('rollback', main_parser().parse_args(['rollback']).command)
        self.assertEqual('select-app', main_parser().parse_args(['select-app', '.']).command)

    def test_internal_run_preserves_launcher_start_identity(self):
        identity = self._captured_identity('build-a')
        identity['startIdentity'] = 'start-a'
        arguments = ['_run', 'demo', '--port', '8464', '--state-dir', str(self.state),
                     '--instance-id', 'instance-a', '--start-identity', 'start-a',
                     '--build-id', 'build-a', '--config-fingerprint', 'config-a',
                     '--shutdown-token', 'token-a', '--app-root', str(Path(__file__).resolve().parents[1])]
        with patch('timeline_reviewer.identity.capture_runtime_identity', return_value=identity) as capture, \
             patch('timeline_reviewer.__main__._serve'):
            self.assertEqual(0, main_cli(arguments))
        self.assertEqual('start-a', capture.call_args.kwargs['start_identity'])

    def test_portable_start_scripts_use_single_instance_launcher(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('-m timeline_reviewer launch',
                      (root / 'start-review.cmd').read_text(encoding='utf-8'))
        self.assertIn('-m timeline_reviewer launch',
                      (root / 'start-review.sh').read_text(encoding='utf-8'))

    def test_configure_persists_selected_project_outside_checkout(self):
        bundle = self.state / 'review'
        config = launcher.configure(self.state, mode='serve', bundle=bundle,
                                    capcut_project=self.state / 'capcut',
                                    capcut_timeline='Timeline 01',
                                    sync_root=self.state / 'sync')
        restored = launcher.load_config(self.state)
        self.assertEqual(config, restored)
        self.assertEqual(str(bundle.resolve()), restored['bundle'])
        self.assertTrue((self.state / 'config.json').is_file())

    def test_configure_refuses_partial_capcut_selection(self):
        with self.assertRaisesRegex(ValueError, 'CapCut'):
            launcher.configure(self.state, mode='serve', bundle=self.state / 'review',
                               capcut_project=self.state / 'capcut')

    def test_concurrent_lock_is_atomic_and_stale_lock_is_recovered(self):
        first = launcher.LaunchLock(self.state, alive=lambda pid, identity: True)
        first.acquire()
        with self.assertRaisesRegex(RuntimeError, 'already in progress'):
            launcher.LaunchLock(self.state, alive=lambda pid, identity: True).acquire()
        first.release()

        stale = {'pid': 9999, 'startIdentity': 'old', 'createdAt': '2000-01-01T00:00:00Z'}
        (self.state / 'launch.lock').write_text(json.dumps(stale), encoding='utf-8')
        recovered = launcher.LaunchLock(self.state, alive=lambda pid, identity: False)
        recovered.acquire()
        self.assertNotEqual(stale, json.loads((self.state / 'launch.lock').read_text(encoding='utf-8')))
        recovered.release()

    def test_matching_healthy_instance_is_reused(self):
        config = launcher.load_config(self.state)
        fingerprint = launcher.config_fingerprint(config)
        record = self._record(configFingerprint=fingerprint, buildId='build-a')
        launcher.write_process_record(self.state, record)
        health = self._health(record)
        with patch.object(launcher, 'current_build_id', return_value='build-a'), \
             patch.object(launcher, 'probe_json', side_effect=lambda port, route, **kwargs: health if route == '/health' else {'activity': {'restartSafe': True}}), \
             patch.object(launcher, 'process_matches', return_value=True), \
             patch.object(launcher, 'spawn_server') as spawn:
            result = launcher.launch(self.state, open_browser=False)
        self.assertEqual('reused', result.action)
        self.assertEqual(4321, result.pid)
        spawn.assert_not_called()

    def test_browser_open_uses_frozen_instance_cache_buster_but_returns_canonical_url(self):
        config = launcher.load_config(self.state)
        fingerprint = launcher.config_fingerprint(config)
        record = self._record(configFingerprint=fingerprint, buildId='build-a')
        launcher.write_process_record(self.state, record)
        with patch.object(launcher, 'current_build_id', return_value='build-a'), \
             patch.object(launcher, 'probe_json', return_value=self._health(record)), \
             patch.object(launcher, 'process_matches', return_value=True), \
             patch.object(launcher.webbrowser, 'open') as browser:
            result = launcher.launch(self.state, open_browser=True)
        self.assertEqual('http://127.0.0.1:8464/', result.url)
        opened = browser.call_args.args[0]
        self.assertIn('buildId=build-a', opened)
        self.assertIn('instanceId=instance-a', opened)

    def test_unrelated_owner_on_port_is_reported_and_untouched(self):
        config = launcher.load_config(self.state)
        unrelated = {'service': 'some-other-app'}
        with patch.object(launcher, 'probe_json', return_value=unrelated), \
             patch.object(launcher, 'find_port_owner_pid', return_value=7654), \
             patch.object(launcher, 'spawn_server') as spawn, \
             patch.object(launcher, 'terminate_process') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'unrelated application'):
                launcher.launch(self.state, open_browser=False)
        spawn.assert_not_called()
        terminate.assert_not_called()

    def test_stop_requires_owned_safe_instance_and_uses_launcher_token(self):
        config = launcher.load_config(self.state)
        record = self._record(configFingerprint=launcher.config_fingerprint(config), port=8490)
        launcher.write_process_record(self.state, record)
        with patch.object(launcher, 'probe_json', side_effect=lambda port, route, **kwargs:
                          self._health(record) if route == '/health' else {'activity': {'restartSafe': True}}), \
             patch.object(launcher, 'process_matches', return_value=True), \
             patch.object(launcher, 'request_shutdown') as shutdown, \
             patch.object(launcher, 'wait_for_exit', return_value=True):
            self.assertTrue(launcher.stop(self.state))
        shutdown.assert_called_once_with(8490, record['shutdownToken'])

    def test_stale_managed_server_restarts_only_when_runtime_is_safe(self):
        config = launcher.load_config(self.state)
        record = self._record(configFingerprint='old-config', buildId='old-build')
        launcher.write_process_record(self.state, record)
        health = self._health(record)
        unsafe = {'activity': {'restartSafe': False}, 'reasons': ['unapplied edits']}
        with patch.object(launcher, 'current_build_id', return_value='new-build'), \
             patch.object(launcher, 'probe_json', side_effect=lambda port, route, **kwargs: health if route == '/health' else unsafe), \
             patch.object(launcher, 'process_matches', return_value=True), \
             patch.object(launcher, 'request_shutdown') as shutdown, \
             patch.object(launcher, 'spawn_server') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'active work'):
                launcher.launch(self.state, open_browser=False)
        shutdown.assert_not_called()
        spawn.assert_not_called()

    def test_safe_stale_managed_server_uses_token_shutdown_then_replaces(self):
        config = launcher.load_config(self.state)
        record = self._record(configFingerprint='old-config', buildId='old-build')
        launcher.write_process_record(self.state, record)
        old_health = self._health(record)
        new_health = dict(old_health, instanceId='new-instance', pid=9876,
                          buildId='new-build', configFingerprint=launcher.config_fingerprint(config),
                          startIdentity='new-start')
        probes = iter([old_health, {'activity': {'restartSafe': True}}, new_health])
        with patch.object(launcher, 'current_build_id', return_value='new-build'), \
             patch('timeline_reviewer.identity.capture_runtime_identity',
                   return_value=self._captured_identity('new-build')), \
             patch.object(launcher, 'probe_json', side_effect=lambda *args, **kwargs: next(probes)), \
             patch.object(launcher, 'process_matches', return_value=True), \
             patch.object(launcher, 'request_shutdown', return_value=None) as shutdown, \
             patch.object(launcher, 'wait_for_exit', return_value=True), \
             patch.object(launcher, 'spawn_server', return_value=FakeProcess(9876)), \
             patch.object(launcher, 'process_start_identity', return_value='new-start'), \
             patch.object(launcher, '_wait_for_handshake', return_value=new_health):
            result = launcher.launch(self.state, open_browser=False)
        self.assertEqual('started', result.action)
        shutdown.assert_called_once_with(config['port'], record['shutdownToken'])
        rollback = json.loads((self.state / 'rollback.json').read_text(encoding='utf-8'))
        self.assertEqual('old-build', rollback['buildId'])
        self.assertEqual(record['appRoot'], rollback['appRoot'])

    def test_legacy_server_is_not_replaced_without_verified_command_and_idle_signals(self):
        config = launcher.load_config(self.state)
        legacy = {'service': 'timeline-reviewer', 'readOnly': False}
        with patch.object(launcher, 'probe_json', side_effect=lambda port, route, **kwargs: legacy if route == '/health' else {'enabled': True, 'operations': [{'type': 'trim'}]}), \
             patch.object(launcher, 'find_port_owner_pid', return_value=2222), \
             patch.object(launcher, 'verified_madison_command', return_value=True), \
             patch.object(launcher, 'terminate_process') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'active work'):
                launcher.launch(self.state, open_browser=False)
        terminate.assert_not_called()

    def test_legacy_idle_server_can_be_replaced_after_command_verification(self):
        config = launcher.load_config(self.state)
        legacy = {'service': 'timeline-reviewer', 'readOnly': True}
        new_health = {'service': 'madison', 'protocolVersion': 1, 'instanceId': 'new',
                      'pid': 3333, 'version': '0.4.0', 'buildId': 'new-build',
                      'configFingerprint': launcher.config_fingerprint(config),
                      'startIdentity': 'new-start'}
        def probe(port, route, **kwargs):
            if probe.calls == 0:
                probe.calls += 1
                return legacy
            if route == '/edit-state':
                return {'enabled': False}
            if route == '/capcut-sync-status':
                return {'enabled': False, 'state': 'idle'}
            return new_health
        probe.calls = 0
        with patch.object(launcher, 'current_build_id', return_value='new-build'), \
             patch('timeline_reviewer.identity.capture_runtime_identity',
                   return_value=self._captured_identity('new-build')), \
             patch.object(launcher, 'probe_json', side_effect=probe), \
             patch.object(launcher, 'find_port_owner_pid', return_value=2222), \
             patch.object(launcher, 'verified_madison_command', return_value=True), \
             patch.object(launcher, 'terminate_process') as terminate, \
             patch.object(launcher, 'wait_for_exit', return_value=True), \
             patch.object(launcher, 'spawn_server', return_value=FakeProcess(3333)), \
             patch.object(launcher, 'process_start_identity', return_value='new-start'), \
             patch.object(launcher, '_wait_for_handshake', return_value=new_health):
            result = launcher.launch(self.state, open_browser=False)
        self.assertEqual('started', result.action)
        terminate.assert_called_once_with(2222)

    def test_legacy_pid_reuse_is_treated_as_unrelated_and_not_killed(self):
        legacy = {'service': 'timeline-reviewer', 'readOnly': True}
        identities = iter(['launcher-start', 'first-start', 'reused-pid'])
        with patch.object(launcher, 'probe_json', return_value=legacy), \
             patch.object(launcher, 'find_port_owner_pid', return_value=2222), \
             patch.object(launcher, 'verified_madison_command', return_value=True), \
             patch.object(launcher, 'process_start_identity', side_effect=lambda pid: next(identities)), \
             patch.object(launcher, 'terminate_process') as terminate, \
             patch.object(launcher, 'wait_for_exit', return_value=True), \
             patch.object(launcher, 'spawn_server', return_value=FakeProcess(3333)), \
             patch.object(launcher, '_wait_for_handshake', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'unrelated application'):
                launcher.launch(self.state, open_browser=False)
        terminate.assert_not_called()

    def test_config_change_records_rollback_and_restores_without_touching_project_data(self):
        project = self.state / 'review' / 'data.json'
        project.parent.mkdir()
        project.write_text('{"revision":"keep"}', encoding='utf-8')
        original = project.read_bytes()
        launcher.configure(self.state, mode='serve', bundle=project.parent)
        result = launcher.rollback(self.state)
        self.assertEqual(str(Path(launcher.__file__).resolve().parents[1]), result['appRoot'])
        self.assertEqual(original, project.read_bytes())
        self.assertEqual('demo', launcher.load_config(self.state)['mode'])

    def test_selecting_new_app_records_prior_app_config_and_build(self):
        old_root = self.state / 'old-app'
        new_root = self.state / 'new-app'
        (old_root / 'timeline_reviewer').mkdir(parents=True)
        (new_root / 'timeline_reviewer').mkdir(parents=True)
        launcher.select_app(self.state, old_root, build_id='old-build')
        launcher.select_app(self.state, new_root, build_id='new-build')
        restored = launcher.rollback(self.state)
        self.assertEqual(str(old_root.resolve()), restored['appRoot'])
        self.assertEqual('old-build', restored['buildId'])

    def test_select_app_rejects_non_madison_folder(self):
        unrelated = self.state / 'other'
        unrelated.mkdir()
        with self.assertRaisesRegex(ValueError, 'Madison'):
            launcher.select_app(self.state, unrelated)

    def _record(self, **overrides):
        record = {'pid': 4321, 'processStartIdentity': 'pid-start', 'startIdentity': 'start-a',
                  'instanceId': 'instance-a', 'buildId': 'build-a',
                  'configFingerprint': 'config-a', 'shutdownToken': 'secret',
                  'appRoot': str(Path(__file__).resolve().parents[1])}
        record.update(overrides)
        return record

    @staticmethod
    def _health(record):
        return {'service': 'madison', 'protocolVersion': 1,
                'instanceId': record['instanceId'], 'pid': record['pid'],
                'version': '0.4.0', 'buildId': record['buildId'],
                'configFingerprint': record['configFingerprint'],
                'startIdentity': record['startIdentity']}

    @staticmethod
    def _captured_identity(build):
        return {'protocolVersion': 1, 'version': '0.4.0', 'buildId': build,
                'startIdentity': 'captured-start', 'instanceId': 'captured-instance',
                'startedAt': '2026-09-23T00:00:00Z', 'pid': 1,
                'configFingerprint': 'captured-config', 'installType': 'checkout',
                'source': {}, 'github': {'status': 'not_verified'}}


if __name__ == '__main__':
    unittest.main()
