"""MLX numerical regression checks; uses tiny random weights, no downloads."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import mlx.core as mx
import mlx.nn as nn
from mlx_audio.stt.models.voxtral_realtime.config import DecoderConfig
from mlx_audio.stt.models.voxtral_realtime.decoder import Decoder

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sources/CaptionFlow/Resources/worker"))
from voxtral_stream import DelayConditioning, prepare_voxtral_model


class ConditioningTest(unittest.TestCase):
    def test_legacy_float32_scales_do_not_promote_half_precision_decoder(self):
        mx.random.seed(7)
        decoder = Decoder(DecoderConfig(dim=32, n_layers=2, n_heads=2, n_kv_heads=2,
                                        head_dim=16, hidden_dim=64, vocab_size=128,
                                        ada_rms_norm_t_cond_dim=8))
        decoder.set_dtype(mx.float16)
        for layer in decoder.layers:
            layer.ada_rms_norm_t_cond.set_dtype(mx.float32)
            layer.attention_norm.set_dtype(mx.float32)
            layer.ffn_norm.set_dtype(mx.float32)
        decoder.norm.set_dtype(mx.float32)
        class Model(nn.Module):
            _ada_scale_delay = None
            calls = 0
            encoder = SimpleNamespace(conv_layers_0_conv=SimpleNamespace(
                conv=SimpleNamespace(weight=mx.zeros((1,), dtype=mx.float16))))
            def _ensure_ada_scales(self, delay):
                self.calls += 1
                self.decoder.precompute_ada_scales(mx.full((32,), delay / 1200, dtype=mx.float32))
                self._ada_scale_delay = delay
        model = Model()
        model.decoder = decoder
        inputs = mx.random.normal((3, 32)).astype(mx.float16)
        model._ensure_ada_scales(240)
        reference, _ = decoder.forward(inputs)
        mx.eval(reference)
        self.assertEqual(reference.dtype, mx.float32)
        prepare_voxtral_model(model)
        conditioning = DelayConditioning(model)
        conditioning.activate(240)
        fast_scales = decoder._ada_scales
        actual, _ = decoder.forward(inputs)
        mx.eval(actual)
        self.assertEqual(actual.dtype, mx.float16)
        self.assertTrue(mx.allclose(actual.astype(mx.float32), reference, atol=0.005, rtol=0.005).item())
        conditioning.activate(1200)
        self.assertIsNot(decoder._ada_scales, fast_scales)
        conditioning.activate(240)
        self.assertIs(decoder._ada_scales, fast_scales)
        self.assertEqual(model.calls, 3)

    def test_preparation_keeps_quantized_weights_and_float32_models(self):
        model = nn.Module()
        model.encoder = nn.Module()
        model.encoder.conv_layers_0_conv = nn.Module()
        model.encoder.conv_layers_0_conv.conv = nn.Linear(32, 32)
        model.quantized = nn.QuantizedLinear(64, 32, group_size=64, bits=4)
        original = model.quantized.weight
        prepare_voxtral_model(model)
        self.assertEqual(model.encoder.conv_layers_0_conv.conv.weight.dtype, mx.float32)
        self.assertIs(model.quantized.weight, original)
        model.encoder.conv_layers_0_conv.conv.set_dtype(mx.float16)
        prepare_voxtral_model(model)
        self.assertIs(model.quantized.weight, original)
        self.assertEqual(model.quantized.scales.dtype, mx.float16)


if __name__ == "__main__":
    unittest.main()
