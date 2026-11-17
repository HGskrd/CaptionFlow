"""Linux platform contracts, including offline native-runtime provisioning."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import common
import linux_launcher
import runtime_assets
import feedback

spec = importlib.util.spec_from_file_location("setup_linux", ROOT / "scripts/setup-linux.py")
setup_linux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_linux)


@unittest.skipIf(os.name == "nt", "POSIX Linux packaging contracts")
class LinuxTests(unittest.TestCase):
    def test_linux_controls_and_xdg_storage(self):
        with patch.object(sys, "platform", "linux"), patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/captionflow-test"}):
            self.assertEqual(common.compute_backends(), ["Vulkan", "CPU"])
            self.assertIn("PipeWire", common.system_audio_label())
            self.assertEqual(common.default_data_directory(), Path("/tmp/captionflow-test/captionflow"))
            self.assertTrue(linux_launcher.environment_directory().is_relative_to(common.default_data_directory()))
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(common.compute_backends(), ["Metal", "CPU"])
            self.assertIsNone(common.system_audio_label())

    def test_linux_system_audio_selects_exact_monitor(self):
        soundcard = Mock()
        soundcard.default_speaker.return_value = SimpleNamespace(id="alsa_output.pci.stereo")
        with patch.object(sys, "platform", "linux"):
            self.assertIs(common.audio_device(soundcard, True), soundcard.get_microphone.return_value)
        soundcard.get_microphone.assert_called_once_with("alsa_output.pci.stereo.monitor", include_loopback=True)
        self.assertIs(common.audio_device(soundcard, False), soundcard.default_microphone.return_value)
        soundcard.default_speaker.return_value = None
        with self.assertRaisesRegex(RuntimeError, "No default audio output"):
            common.audio_device(soundcard, True)

    def test_linux_capture_reaches_mixer_for_system_and_mixed_inputs(self):
        import audio_capture
        for mode in ("microphone", "system", "mixed"):
            with self.subTest(mode=mode), patch.object(sys, "platform", "linux"), \
                 patch.dict(sys.modules, {"soundcard": Mock()}), \
                 patch.object(audio_capture, "Mixer", side_effect=RuntimeError("mixer reached")):
                with self.assertRaisesRegex(RuntimeError, "mixer reached"):
                    audio_capture.capture(mode, "unused.json")

    def test_bundled_data_preserves_existing_user_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "app"
            bundled = root / "portable/data/downloads"
            bundled.mkdir(parents=True)
            (bundled / "runtime.tar.gz").write_bytes(b"bundled")
            (bundled / "unfinished.partial").write_bytes(b"incomplete")
            destination = Path(d) / "user"
            with patch.object(common, "ROOT", root):
                common.seed_bundled_data(destination)
                installed = destination / "downloads/runtime.tar.gz"
                self.assertEqual(installed.read_bytes(), b"bundled")
                installed.write_bytes(b"existing")
                common.seed_bundled_data(destination)
                self.assertEqual(installed.read_bytes(), b"existing")
                self.assertFalse((destination / "downloads/unfinished.partial").exists())

    def test_offline_linux_install_keeps_executable_and_shared_library_links(self):
        for backend in ("cpu", "vulkan"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                downloads = root / "downloads"
                downloads.mkdir()
                key = "linux-x86_64-" + backend
                nemo = downloads / f"nemo-speech-{runtime_assets.NEMO_VERSION}-{key}.tar.gz"
                suffix = runtime_assets.LLAMA_ASSETS[key][0]
                llama = downloads / f"llama-{runtime_assets.LLAMA_TAG}-bin-{suffix}"
                for archive, executable in ((nemo, "nemo-speech"), (llama, "llama-server")):
                    with tarfile.open(archive, "w:gz") as stream:
                        info = tarfile.TarInfo("release/bin/" + executable)
                        info.mode = 0o755
                        data = b"#!/bin/sh\nexit 0\n"
                        info.size = len(data)
                        stream.addfile(info, io.BytesIO(data))
                        info = tarfile.TarInfo("release/lib/libggml.so.0")
                        info.size = 3
                        stream.addfile(info, io.BytesIO(b"ELF"))
                        info = tarfile.TarInfo("release/lib/libggml.so")
                        info.type = tarfile.SYMTYPE
                        info.linkname = "libggml.so.0"
                        stream.addfile(info)
                models = {}
                (root / "models").mkdir()
                for name in ("nemotron", "hy-mt2"):
                    path = root / "models" / (name + ".gguf")
                    path.write_bytes(name.encode())
                    models[name] = {"repo": "test", "revision": "test", "file": path.name,
                                    "sha256": runtime_assets.sha256(path)}
                with patch.object(runtime_assets, "platform_key", return_value="linux-x86_64"), \
                     patch.dict(runtime_assets.NEMO_HASHES, {key: runtime_assets.sha256(nemo)}), \
                     patch.dict(runtime_assets.LLAMA_ASSETS, {key: (suffix, runtime_assets.sha256(llama))}), \
                     patch.object(runtime_assets, "MODELS", models), patch("urllib.request.urlopen") as network:
                    config = runtime_assets.install(root, backend, progress=lambda _: None, allow_network=False)
                    network.assert_not_called()
                self.assertEqual(config["native_backend"], backend)
                self.assertEqual(config["gpu"], -1 if backend == "cpu" else 0)
                self.assertTrue(os.access(config["llama_server"], os.X_OK))
                sdk = Path(config["native_sdk"])
                self.assertTrue((sdk / "lib/libggml.so").is_symlink())
                self.assertEqual((sdk / "lib/libggml.so").read_bytes(), b"ELF")
                self.assertEqual(json.loads((root / "configuration.json").read_text()), config)

    def test_desktop_exec_escapes_paths_and_field_codes(self):
        self.assertEqual(setup_linux.desktop_quote('/home/a/My App/start.sh'), '"/home/a/My App/start.sh"')
        value = setup_linux.desktop_quote('/home/a/100%/$x"`/start.sh')
        self.assertIn("100%%", value)
        for escaped in ('\\\\$', '\\\\"', '\\\\`'):
            self.assertIn(escaped, value)

    def test_linux_window_and_declining_consent_make_no_requests(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        import app
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d, patch.object(sys, "platform", "linux"), \
             patch("urllib.request.urlopen") as network:
            window = app.Window(d, smoke_test=True)
            self.assertEqual([window.backend.itemText(i) for i in range(window.backend.count())], ["Vulkan", "CPU"])
            self.assertEqual(window.input.count(), 3)
            self.assertEqual(window.speech.count(), 1)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                window.change_consent()
            self.assertFalse(window.feedback.allowed)
            self.assertTrue(window.input.findData("system") >= 0)
            network.assert_not_called()
            window.close()
            application.processEvents()

    def test_offline_setup_installs_into_private_venv_and_writes_desktop_entry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "app with spaces"
            root.mkdir()
            environment = Path(d) / "private-python"
            environment.mkdir()
            with patch.object(setup_linux.platform, "system", return_value="Linux"), \
                 patch.object(setup_linux.platform, "machine", return_value="x86_64"), \
                 patch.object(os, "geteuid", return_value=1000), \
                 patch.object(setup_linux, "ROOT", root), \
                 patch.object(setup_linux, "environment_directory", return_value=environment), \
                 patch.object(setup_linux.venv, "EnvBuilder") as builder, \
                 patch.object(setup_linux.subprocess, "run") as run, \
                 patch.dict(os.environ, {"XDG_DATA_HOME": str(Path(d) / "user-data")}):
                setup_linux.setup(skip_system_packages=True, offline=True)
            command = run.call_args_list[0].args[0]
            self.assertEqual(command[:4], [str(environment / "bin/python"), "-m", "pip", "install"])
            self.assertIn("--no-index", command)
            builder.return_value.create.assert_called_once_with(environment)
            self.assertTrue((environment / ".ready").exists())
            desktop = (Path(d) / "user-data/applications/captionflow.desktop").read_text()
            self.assertIn(f'Exec="{root}/start-captionflow.sh"', desktop)


if __name__ == "__main__":
    unittest.main()
