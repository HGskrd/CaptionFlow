"""Exercise native Mac backend selection without models or network access."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sources/CaptionFlow/Resources/worker"))
from small_models import NEMOTRON
from worker import configure_managed_runtime
from runtime_assets import LLAMA_ASSETS, NEMO_HASHES


class MacRuntimeTests(unittest.TestCase):
    def configuration(self, targets):
        return {"speech_model": NEMOTRON, "translation_model": "saved-mlx-model",
                "targets": targets, "source_language": "en", "cache_dir": "/tmp/captionflow/models",
                "allow_downloads": False}

    def managed(self):
        return dict(speech_backend="nemo", native_backend="cpu", native_sdk="/nemo", speech_model="/speech.gguf",
                    gpu=-1, nemotron_right_context=3, translation_backend="llama",
                    translation_model_type="hunyuan_v1_dense", translation_model="/translation.gguf",
                    llama_server="/llama-server")

    def test_intel_uses_cpu_and_gguf_translation_preserving_user_settings(self):
        config = self.configuration(["German"])
        with patch("runtime_assets.platform_key", return_value="macos-x86_64"), \
             patch("runtime_assets.install", return_value=self.managed()) as install:
            configure_managed_runtime(config, lambda _: None)
        self.assertEqual(install.call_args.args, (Path("/tmp/captionflow"), "cpu"))
        self.assertTrue(install.call_args.kwargs["translation"])
        self.assertFalse(install.call_args.kwargs["allow_network"])
        self.assertEqual(config["translation_backend"], "llama")
        self.assertEqual(config["translation_model"], "/translation.gguf")
        self.assertEqual(config["targets"], ["German"])
        self.assertEqual(config["source_language"], "en")
        self.assertEqual(config["gpu"], -1)
        self.assertIn("macos-x86_64-cpu", NEMO_HASHES)
        self.assertEqual(LLAMA_ASSETS["macos-x86_64-cpu"][0], "macos-x64.tar.gz")

    def test_intel_original_only_does_not_install_translation(self):
        config = self.configuration([])
        with patch("runtime_assets.platform_key", return_value="macos-x86_64"), \
             patch("runtime_assets.install", return_value=self.managed()) as install:
            configure_managed_runtime(config, lambda _: None)
        self.assertFalse(install.call_args.kwargs["translation"])
        self.assertNotIn("translation_backend", config)

    def test_apple_silicon_retains_mlx_translation(self):
        config = self.configuration(["French"])
        managed = self.managed() | {"native_backend": "metal", "gpu": 0}
        with patch("runtime_assets.platform_key", return_value="macos-aarch64"), \
             patch("runtime_assets.install", return_value=managed) as install:
            configure_managed_runtime(config, lambda _: None)
        self.assertEqual(install.call_args.args[1], "metal")
        self.assertFalse(install.call_args.kwargs["translation"])
        self.assertEqual(config["translation_model"], "saved-mlx-model")
        self.assertNotIn("translation_backend", config)


if __name__ == "__main__":
    unittest.main()
