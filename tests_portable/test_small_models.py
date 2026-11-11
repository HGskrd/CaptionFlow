import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import benchmark
from small_models import install_model
from worker import run_snapshot_speech


class SmallModelTests(unittest.TestCase):
    def test_download_identifies_the_app_and_preserves_resume_header(self):
        import hashlib
        import io
        from runtime_assets import download
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "update.zip"
            path.with_suffix(".zip.partial").write_bytes(b"first")
            response = io.BytesIO(b"second")
            response.status = 206
            with patch("urllib.request.urlopen", return_value=response) as network:
                download("https://example.org/update.zip", path,
                         hashlib.sha256(b"firstsecond").hexdigest(), progress=lambda _: None)
            headers = dict(network.call_args.args[0].header_items())
            self.assertEqual(headers["User-agent"], "CaptionFlow")
            self.assertEqual(headers["Range"], "bytes=5-")
            self.assertEqual(path.read_bytes(), b"firstsecond")

    def test_worker_protocol_is_utf8_under_a_legacy_console_encoding(self):
        import common
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "worker.py").write_text(
                'import json,sys\n'
                'config=json.loads(sys.stdin.readline())\n'
                'print(json.dumps({"type":"ready"}),flush=True)\n'
                'print(json.dumps({"type":"source","text":config["sample"]},ensure_ascii=False),flush=True)\n'
                'sys.stdin.readline()\n', encoding="utf-8")
            received = []
            ready = threading.Event()
            def event(timestamp, value):
                if value["type"] == "source":
                    received.append(value["text"])
                    ready.set()
            with patch.object(common, "WORKER_DIR", path), patch.dict("os.environ", {"PYTHONIOENCODING": "ascii"}):
                session = common.WorkerSession({"sample": "Français · 日本語 · العربية"}, event)
                try:
                    session.ready(timeout=5)
                    self.assertTrue(ready.wait(2))
                    self.assertEqual(received, ["Français · 日本語 · العربية"])
                    session.stop()
                finally:
                    session.close()

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
