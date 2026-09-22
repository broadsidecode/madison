import copy
import json
from pathlib import Path
import tempfile
import unittest

from timeline_reviewer.manifest import ManifestError, asset_files, asset_path, load_manifest, validate_manifest


def manifest():
    return {'schemaVersion': 1, 'title': 'Sample review', 'duration': 4, 'fps': 24,
            'revision': 'sample', 'videoUrl': 'media/movie.mp4', 'tracks': [
                {'id': 'picture', 'name': 'Picture', 'kind': 'video', 'clips': [
                    {'id': 'clip-one', 'label': 'First clip', 'start': 0, 'end': 4,
                     'duration': 4, 'sourceStart': 2, 'sourceEnd': 6}]}]}


class ManifestTests(unittest.TestCase):
    def test_valid_optional_fields_and_no_leaked_unknown_metadata(self):
        value = manifest()
        value['unrelated_private_metadata'] = 'do not serve this'
        checked = validate_manifest(value)
        self.assertNotIn('unrelated_private_metadata', checked)
        self.assertIsNone(checked['waveform'])
        self.assertEqual(checked['tracks'][0]['clips'][0]['speed'], 1)

    def test_duplicate_lane_and_clip_ids(self):
        for key in ('lane', 'clip'):
            with self.subTest(key=key):
                value = manifest()
                if key == 'lane':
                    value['tracks'].append(copy.deepcopy(value['tracks'][0]))
                else:
                    value['tracks'][0]['clips'].append(copy.deepcopy(value['tracks'][0]['clips'][0]))
                with self.assertRaisesRegex(ManifestError, 'Duplicate'):
                    validate_manifest(value)

    def test_nonfinite_boolean_and_invalid_ranges(self):
        for key, number in [('duration', float('nan')), ('duration', float('inf')), ('duration', 0), ('duration', 90000), ('fps', True), ('fps', 0), ('fps', 241)]:
            with self.subTest(key=key, number=number):
                value = manifest(); value[key] = number
                with self.assertRaises(ManifestError):
                    validate_manifest(value)
        for key, number in [('start', -1), ('end', 3), ('duration', 0), ('sourceEnd', 1), ('speed', 0), ('hidden', 'false')]:
            value = manifest(); value['tracks'][0]['clips'][0][key] = number
            with self.subTest(key=key), self.assertRaises(ManifestError):
                validate_manifest(value)

    def test_local_asset_paths(self):
        self.assertEqual(asset_path('media/a%20b.mp4', 'test'), 'media/a b.mp4')
        for value in ['../secret.mp4', '/secret.mp4', 'C:/secret.mp4', 'file:///secret', 'https://example.com/a.mp4', '//host/a.mp4', 'media/%2e%2e/secret.mp4', 'media/%252e%252e/a.mp4', 'media/a%3fsecret.mp4', 'media/a%23b.mp4', 'media/a\\b.mp4', 'media/%00.mp4']:
            with self.subTest(value=value), self.assertRaises(ManifestError):
                asset_path(value, 'test')

    def test_missing_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'bundle'; (root / 'media').mkdir(parents=True)
            data = validate_manifest(manifest())
            with self.assertRaisesRegex(ManifestError, 'Missing'):
                asset_files(root, data)

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'bundle'; (root / 'media').mkdir(parents=True)
            data = validate_manifest(manifest())
            outside = Path(tmp) / 'outside.mp4'; outside.write_bytes(b'outside')
            try:
                (root / 'media/movie.mp4').symlink_to(outside)
            except OSError:
                self.skipTest('Symlink creation unavailable on this host')
            with self.assertRaisesRegex(ManifestError, 'escapes'):
                asset_files(root, data)

    def test_script_cannot_be_served_as_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'payload.js').write_text('alert(1)')
            value = manifest(); value['videoUrl'] = 'payload.js'
            with self.assertRaisesRegex(ManifestError, 'extension'):
                asset_files(root, validate_manifest(value))

    def test_json_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'data.json'; p.write_text('{"schemaVersion":1,"schemaVersion":1}')
            with self.assertRaisesRegex(ManifestError, 'Duplicate JSON'):
                load_manifest(p)

    def test_waveform_limits(self):
        value = manifest(); value['waveform'] = {'step': .1, 'peaks': [0, .5, 1]}
        self.assertEqual(len(validate_manifest(value)['waveform']['peaks']), 3)
        for waveform in [{'step': 0, 'peaks': []}, {'step': .1, 'peaks': [2]}, {'step': .1, 'peaks': [float('nan')]}, {'step': .1, 'peaks': [0] * 50001}]:
            value['waveform'] = waveform
            with self.subTest(waveform=str(waveform)[:80]), self.assertRaises(ManifestError):
                validate_manifest(value)


if __name__ == '__main__':
    unittest.main()
