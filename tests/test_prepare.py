import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from timeline_reviewer.prepare import prepare_bundle
from timeline_reviewer.manifest import asset_files, load_manifest


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Optional FFmpeg tools are not installed')
class PrepareTests(unittest.TestCase):
    def test_new_bundle_is_valid_and_input_is_unchanged(self):
        source = Path(__file__).resolve().parents[1] / 'timeline_reviewer/demo/media/demo.mp4'
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='review example ') as tmp:
            out = Path(tmp) / 'new bundle'
            report = prepare_bundle(source, out, title='My review')
            self.assertEqual(report['clips'], 1)
            data = load_manifest(out / 'data.json')
            self.assertEqual(data['title'], 'My review')
            self.assertEqual(len(asset_files(out, data)), 2)
            self.assertGreater(len(data['waveform']['peaks']), 0)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                prepare_bundle(source, out)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
