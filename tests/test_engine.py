import io
import unittest
from unittest.mock import patch

from timeline_reviewer import engine
from timeline_reviewer.tesseract import SUPPORTED_CLI_VERSIONS


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(body=None, error=None):
    def opener(request, timeout):
        if error:
            raise error
        return FakeResponse(body)
    return opener


class EngineStatusTests(unittest.TestCase):
    newest = SUPPORTED_CLI_VERSIONS[0]
    oldest = SUPPORTED_CLI_VERSIONS[-1]

    def test_classify_every_state(self):
        self.assertEqual(engine.classify(self.newest, self.newest), 'current')
        self.assertEqual(engine.classify(self.newest, '99.0.0'), 'madison_update')
        self.assertEqual(engine.classify(None, '99.0.0'), 'madison_update')
        self.assertEqual(engine.classify('0.0.1', self.newest), 'engine_unsupported')
        self.assertEqual(engine.classify(None, self.newest), 'not_found')
        self.assertEqual(engine.classify(self.newest, None), 'not_verified')
        if self.oldest != self.newest:
            self.assertEqual(engine.classify(self.oldest, self.newest), 'engine_update')

    def test_official_pin_read_and_rejected(self):
        self.assertEqual(engine.official_version(opener_for(b'0.2.0\n')), '0.2.0')
        self.assertIsNone(engine.official_version(opener_for(b'<html>not found</html>')))
        self.assertIsNone(engine.official_version(opener_for(error=OSError('offline'))))

    def test_status_never_raises_and_offline_is_not_current(self):
        def broken(cli):
            raise RuntimeError('engine crashed')
        status = engine.engine_status(opener=opener_for(error=OSError('offline')),
                                      installed_reader=broken)
        self.assertEqual(status['state'], 'not_found')
        status = engine.engine_status(opener=opener_for(error=OSError('offline')),
                                      installed_reader=lambda cli: self.newest)
        self.assertEqual(status['state'], 'not_verified')
        self.assertFalse(status['attention'])

    def test_newer_official_release_needs_madison_update(self):
        status = engine.engine_status(opener=opener_for(b'99.0.0'),
                                      installed_reader=lambda cli: self.newest)
        self.assertEqual(status['state'], 'madison_update')
        self.assertTrue(status['attention'])
        self.assertEqual(status['supported'], list(SUPPORTED_CLI_VERSIONS))

    def test_installed_version_is_read_without_the_support_gate(self):
        with patch.object(engine, 'locate_cli', return_value='tsrct'), \
                patch.object(engine, '_run', return_value='tsrct 0.0.1 (abc)'):
            self.assertEqual(engine.installed_version(), '0.0.1')
        with patch.object(engine, 'locate_cli', side_effect=engine.ImportError('missing')):
            self.assertIsNone(engine.installed_version())

    def test_public_status_sanitizes(self):
        self.assertEqual(engine.public_status(None)['state'], 'checking')
        self.assertEqual(engine.public_status({'state': 'bogus'})['state'], 'checking')
        value = engine.public_status({'state': 'current', 'installed': '0.2.0<script>',
                                      'official': '0.2.0', 'attention': True, 'extra': 1})
        self.assertIsNone(value['installed'])
        self.assertFalse(value['attention'])
        self.assertNotIn('extra', value)


if __name__ == '__main__':
    unittest.main()
