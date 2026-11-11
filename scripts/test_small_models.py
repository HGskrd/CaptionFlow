"""Real pinned checkpoints: offline inference, then the production worker protocol."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
from common import WORKER_DIR
from small_models import CompactCPU, install_model
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--backend", choices=["cpu", "mlx"], required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    names = ["phonon"] if args.backend == "cpu" else ["redux", "phonon"]
    paths = {name: install_model(name, args.cache, allow_network=args.download) for name in names}
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      MLX_AUDIO_CACHE_DIR=str(Path(args.cache) / "converted"))
    with wave.open(str(ROOT / "docs/fixtures/fr-synthetic.wav"), "rb") as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    original_connect = socket.socket.connect
    def blocked(*args, **kwargs):
        raise AssertionError("Inference attempted an outbound connection")
    socket.socket.connect = blocked
    results = []
    from benchmark import trial, word_error
    reference = json.loads((ROOT / "docs/fixtures/fr-synthetic.json").read_text(encoding="utf-8"))["reference"]
    try:
        for name in names:
            started = time.monotonic()
            if args.backend == "cpu":
                model = CompactCPU(name, paths[name])
            else:
                import mlx.core as mx
                from mlx_audio.stt.utils import load_model
                model = load_model(paths[name], model_type="parakeet" if name == "redux" else "phonon2")
            loaded = time.monotonic() - started
            timings = []
            for _ in range(2):
                started = time.monotonic()
                text = model.transcribe(samples) if args.backend == "cpu" else model.generate(mx.array(samples)).text
                timings.append(time.monotonic() - started)
                if word_error(reference, text) > .3:
                    raise AssertionError(f"{name}: unexpected French transcription: {text!r}")
            del model
            config = {"speech_model": paths[name], "cache_dir": args.cache, "allow_downloads": False,
                      "source_language": "fr", "targets": [], "silence_threshold": .008}
            if args.backend == "cpu":
                config.update(speech_backend="phonon_cpu", native_backend="cpu", gpu=-1)
            result = trial(config, ROOT / "docs/fixtures/fr-synthetic.json", 6, "realtime")
            results.append({"model": name, "backend": args.backend, "load_seconds": loaded,
                            "direct_inference_seconds": timings, "worker": result})
            if result["status"] != "ok" or result.get("missing_final_captions") != 0 or result.get("word_error_rate", 1) > .3:
                raise AssertionError(json.dumps(result, ensure_ascii=False))
    finally:
        socket.socket.connect = original_connect
        Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
