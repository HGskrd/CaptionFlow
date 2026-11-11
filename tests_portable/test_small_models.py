import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import benchmark
from small_models import install_model
from worker import run_snapshot_speech


class SmallModelTests(unittest.TestCase):
    def test_missing_checkpoint_never_connects_without_consent(self):
        with tempfile.TemporaryDirectory() as root, patch("urllib.request.urlopen") as network:
            for model in ("redux", "phonon"):
                with self.assertRaises(PermissionError):
                    install_model(model, root, allow_network=False)
            network.assert_not_called()

    def test_unchanged_partial_still_publishes_final(self):
        from unittest.mock import Mock
        inbox = Mock()
        inbox.next.side_effect = [(0, [0], False), (0, [0], True), None]
        model = Mock()
        model.transcribe.return_value = "Bonjour"
        publish = Mock()
        run_snapshot_speech(inbox, "phonon_cpu", model, "unused", "fr", publish)
        self.assertEqual([call.args[3] for call in publish.call_args_list], [False, True])

    def test_checkpoint_changes_invalidate_resume_but_conversion_cache_does_not(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "config.json").write_text("{}")
            weights = path / "model.safetensors"
            weights.write_bytes(b"one")
            config = {"speech_model": str(path)}
            before = benchmark.profile_identity(config)
            (path / "cpu").mkdir()
            (path / "cpu/model.fermion").write_bytes(b"derived")
            self.assertEqual(before, benchmark.profile_identity(config))
            weights.write_bytes(b"two")
            self.assertNotEqual(before, benchmark.profile_identity(config))
