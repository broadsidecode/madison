import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from timeline_reviewer.identity import capture_runtime_identity, _git_details, _github_comparison


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        package = self.root / 'timeline_reviewer'
        (package / 'web').mkdir(parents=True)
        (package / '__init__.py').write_text('__version__ = "9.8.7"\n', encoding='utf-8')
        (package / 'server.py').write_text('VALUE = 1\n', encoding='utf-8')
        (package / 'web/index.html').write_text('<!doctype html>', encoding='utf-8')
        (package / 'web/app.js').write_text("'use strict';", encoding='utf-8')
        (package / 'web/styles.css').write_text('body{}', encoding='utf-8')
        self.package = package

    def capture(self, **kwargs):
        kwargs.setdefault('github_checker', lambda: (_ for _ in ()).throw(OSError('offline')))
        return capture_runtime_identity(package_root=self.package, version='9.8.7',
                                        instance_id='instance-test',
                                        started_at='2026-09-23T12:00:00Z', pid=321,
                                        **kwargs)

    def test_build_id_is_deterministic_and_tracks_only_loaded_source(self):
        first = self.capture()
        second = self.capture()
        self.assertEqual(first['buildId'], second['buildId'])
        self.assertEqual(first['startIdentity'], second['startIdentity'])
        (self.root / 'private-notes.txt').write_text('ignore me', encoding='utf-8')
        self.assertEqual(first['buildId'], self.capture()['buildId'])
        (self.package / 'server.py').write_text('VALUE = 2\n', encoding='utf-8')
        self.assertNotEqual(first['buildId'], self.capture()['buildId'])

    def test_release_manifest_supports_zip_install_without_git(self):
        initial = self.capture()
        manifest = {'schemaVersion': 1, 'version': '9.8.7',
                    'buildId': initial['buildId'], 'commit': 'a' * 40}
        (self.root / 'madison-release.json').write_text(json.dumps(manifest), encoding='utf-8')
        with patch('timeline_reviewer.identity._git_details', return_value=None):
            identity = self.capture()
        self.assertEqual(identity['installType'], 'release-zip')
        self.assertTrue(identity['source']['manifestVerified'])
        self.assertEqual(identity['source']['commit'], 'a' * 40)
        self.assertFalse(identity['source']['localChanges'])

    def test_git_and_github_details_are_fixed_at_capture_and_fail_closed(self):
        git = {'commit': 'b' * 40, 'branch': 'main', 'localChanges': False}
        with patch('timeline_reviewer.identity._git_details', return_value=git):
            identity = self.capture(github_checker=lambda: {'status': 'current',
                                                             'checkedAt': '2026-09-23T12:00:01Z'})
        self.assertEqual(identity['source']['commit'], 'b' * 40)
        self.assertEqual(identity['github']['status'], 'current')
        with patch('timeline_reviewer.identity._git_details', return_value=git):
            unknown = self.capture(github_checker=lambda: (_ for _ in ()).throw(OSError('offline')))
        self.assertEqual(unknown['github']['status'], 'not_verified')
        self.assertIsInstance(unknown['github']['checkedAt'], str)
        self.assertNotIn(str(self.root), json.dumps(identity))

    def test_launcher_may_supply_a_start_nonce_without_changing_the_build(self):
        normal = self.capture()
        supplied = self.capture(start_identity='launcher-start-nonce')
        self.assertEqual(normal['buildId'], supplied['buildId'])
        self.assertEqual(supplied['startIdentity'], 'launcher-start-nonce')

    def test_portable_github_check_compares_main_and_latest_release(self):
        class Response:
            def __init__(self, value):
                self.value = value
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self):
                return json.dumps(self.value).encode('utf-8')

        def opener(request, timeout):
            self.assertLessEqual(timeout, 2)
            if request.full_url.endswith('/commits/main'):
                return Response({'sha': 'c' * 40})
            return Response({'tag_name': 'v9.8.7'})

        result = _github_comparison('c' * 40, '9.8.7', opener=opener,
                                    checked_at='2026-09-23T12:00:02Z')
        self.assertEqual(result, {'status': 'current', 'checkedAt': '2026-09-23T12:00:02Z',
                                  'mainMatches': True, 'releaseMatches': True,
                                  'releaseTag': 'v9.8.7'})

    def test_portable_github_check_is_honest_when_offline(self):
        result = _github_comparison('c' * 40, '9.8.7',
                                    opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('offline')),
                                    checked_at='2026-09-23T12:00:03Z')
        self.assertEqual(result['status'], 'not_verified')
        self.assertEqual(result['checkedAt'], '2026-09-23T12:00:03Z')

    def test_github_mismatch_is_not_mislabeled_ahead_or_behind(self):
        class Response:
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps(self.value).encode('utf-8')
        def opener(request, timeout):
            return Response({'sha': 'd' * 40} if request.full_url.endswith('/commits/main')
                            else {'tag_name': 'v9.8.6'})
        result = _github_comparison('c' * 40, '9.8.7', opener=opener,
                                    checked_at='2026-09-23T12:00:04Z')
        self.assertEqual(result['status'], 'different')

    def test_repo_changes_are_local_but_explicitly_excluded_logo_is_not(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.email', 'test@example.invalid'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.name', 'Madison Test'], check=True)
        (self.root / 'README.md').write_text('start\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'fixture'], check=True)
        (self.root / 'images').mkdir()
        (self.root / 'images/madison-logo.png').write_bytes(b'private local logo')
        self.assertFalse(_git_details(self.package)['localChanges'])
        (self.root / 'README.md').write_text('changed\n', encoding='utf-8')
        self.assertTrue(_git_details(self.package)['localChanges'])


if __name__ == '__main__':
    unittest.main()
