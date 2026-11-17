"""Protect runtime assets and the package boundary while removing unused files."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('slim_runtime', Path(__file__).with_name('slim-macos-runtime.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SOURCE = '''from .timing import add_word_timestamps

def transcribe(word_timestamps=False):
                if word_timestamps:
                    add_word_timestamps()
                return 'captions'
'''


class RuntimeSlimmingTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root/'bin').mkdir(); (root/'bin/python3').touch()
        site = root/'lib/python3.11/site-packages'
        (site/'mlx_whisper').mkdir(parents=True)
        (site/'mlx_whisper/transcribe.py').write_text(SOURCE)
        for relative in ('torch/large.bin', 'numpy/tests/test_data.py', 'numpy/testing/__init__.py',
                         'mlx_whisper/assets/mel_filters.npz', 'certifi/cacert.pem'):
            path = site/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'asset')
        info = site/'torch-2.14.0.dist-info'; info.mkdir()
        (info/'METADATA').write_text('Name: torch\nVersion: 2.14.0\n')
        (info/'RECORD').write_text('torch/large.bin,,\ntorch-2.14.0.dist-info/METADATA,,\ntorch-2.14.0.dist-info/RECORD,,\n')
        return root, site

    def test_only_unused_files_removed_and_timestamp_import_is_lazy(self):
        with tempfile.TemporaryDirectory() as directory:
            root, site = self.fixture(directory)
            with patch.object(module, 'WHISPER_TRANSCRIBE_SHA256', hashlib.sha256(SOURCE.encode()).hexdigest()):
                report = module.slim(root, 'arm64')
            self.assertEqual(report['removed_distributions'], {'torch':'2.14.0'})
            self.assertFalse((site/'torch').exists())
            self.assertFalse((site/'numpy/tests').exists())
            for relative in ('numpy/testing/__init__.py', 'mlx_whisper/assets/mel_filters.npz', 'certifi/cacert.pem'):
                self.assertTrue((site/relative).exists(), relative)
            namespace = {}
            exec((site/'mlx_whisper/transcribe.py').read_text(), namespace)
            self.assertEqual(namespace['transcribe'](), 'captions')

    def test_unknown_upstream_source_stops_before_removing_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root, site = self.fixture(directory)
            with self.assertRaisesRegex(ValueError, 'mlx-whisper changed'):
                module.slim(root, 'arm64')
            self.assertTrue((site/'torch/large.bin').exists())

    def test_record_cannot_delete_outside_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root, site = self.fixture(directory)
            (site/'torch-2.14.0.dist-info/RECORD').write_text('../../../../outside,,\n')
            with patch.object(module, 'WHISPER_TRANSCRIBE_SHA256', hashlib.sha256(SOURCE.encode()).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'escapes private runtime'):
                    module.slim(root, 'arm64')


if __name__ == '__main__':
    unittest.main()
