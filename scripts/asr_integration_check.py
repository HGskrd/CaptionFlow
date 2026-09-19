"""Offline acceptance check for CaptionFlow's supported MLX speech backends.

Usage: .venv/bin/python scripts/asr_integration_check.py MODEL_PATH [MODEL_PATH ...] [--translation MODEL_PATH]
Creates a deterministic spoken fixture with macOS say; it never records the user.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
root = Path(__file__).resolve().parents[1]
worker_path = root / "Sources/CaptionFlow/Resources/worker"
sys.path.insert(0, str(worker_path))
import worker
sys.stdout = worker.PROTOCOL


def fixture_audio(directory):
    import numpy as np
    from scipy.io import wavfile
    path = str(Path(directory) / "speech.wav")
    subprocess.run([
        "/usr/bin/say", "-v", "Samantha", "-r", "145", "-o", path,
        "--file-format=WAVE", "--data-format=LEI16@16000",
        "The train leaves at nine tomorrow morning.",
    ], check=True)
    rate, samples = wavfile.read(path)
    assert rate == 16000
    return samples.astype(np.float32) / 32768.0


def check_model(model_path, samples, directory, translation=None):
    import numpy as np
    engine = worker.speech_engine(model_path)
    command = [sys.executable, str(worker_path / "worker.py")]
    config = {
        "type": "configure", "speech_model": model_path,
        "translation_model": translation or "unused",
        "targets": ["French"] if translation else [], "source_language": "en",
        "allow_downloads": False, "cache_dir": directory, "silence_threshold": 0.008,
    }
    messages = [json.dumps(config)]
    for offset in range(0, len(samples), 3200):
        pcm = samples[offset:offset + 3200].astype("<f4").tobytes()
        messages.append(json.dumps({"type": "audio", "pcm": base64.b64encode(pcm).decode()}))
    silence = base64.b64encode(np.zeros(3200, dtype="<f4").tobytes()).decode()
    messages.extend(json.dumps({"type": "audio", "pcm": silence}) for _ in range(4))
    messages.append(json.dumps({"type": "stop"}))
    started = time.monotonic()
    completed = subprocess.run(
        command, input="\n".join(messages) + "\n", text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    sources = [event for event in events if event["type"] == "source"]
    assert sources, events
    recognized = sources[-1]["text"]
    assert "train" in recognized.lower() and "morning" in recognized.lower(), recognized
    if translation:
        assert any(event["type"] == "translation" and event["final"] for event in events), events
    print(json.dumps({
        "check": "speech_backend", "engine": engine, "text": recognized,
        "seconds": round(time.monotonic() - started, 2), "source_events": len(sources),
    }, ensure_ascii=False))


def main():
    arguments = sys.argv[1:]
    translation = None
    if "--translation" in arguments:
        index = arguments.index("--translation")
        translation = arguments[index + 1]
        del arguments[index:index + 2]
    with tempfile.TemporaryDirectory() as directory:
        samples = fixture_audio(directory)
        for model_path in arguments:
            check_model(model_path, samples, directory, translation)


if __name__ == "__main__":
    main()
