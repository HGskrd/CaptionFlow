"""Offline-model acceptance check; synthesized audio is paced like live capture.

Usage: .venv/bin/python scripts/dual_delay_check.py MODEL_PATH [--translation MODEL_PATH]
Add --worker dist/CaptionFlow.app/Contents/Resources/worker/worker.py to test the bundle.
No model downloads, microphone recording, or external transcription services.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from asr_integration_check import fixture_audio, worker_path


def run(args, samples, directory, dual, delay):
    import numpy as np
    events = []
    lines = queue.Queue()
    errors = []
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-u", args.worker], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    def read_output():
        try:
            for line in process.stdout:
                event = json.loads(line)
                events.append((time.monotonic(), event))
                lines.put(event)
        finally:
            lines.put(None)
    output_thread = threading.Thread(target=read_output, daemon=True)
    error_thread = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    output_thread.start(); error_thread.start()
    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
    try:
        send({"type": "configure", "speech_model": args.model,
              "translation_model": args.translation or "unused", "targets": ["French"] if args.translation and dual else [],
              "source_language": "en", "allow_downloads": False, "cache_dir": directory,
              "voxtral_dual_delay": dual, "voxtral_delay_ms": delay,
              "voxtral_correction_delay_ms": 1200})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            event = lines.get(timeout=max(0.01, deadline - time.monotonic()))
            assert event is not None and event["type"] != "error", (event, "".join(errors)[-2000:])
            if event["type"] == "ready":
                break
        else:
            raise AssertionError("Worker never became ready")
        started = time.monotonic()
        pcm = np.concatenate([samples, np.zeros(12800, dtype=np.float32)])
        for offset in range(0, len(pcm), 3200):
            if dual:
                remaining = started + offset / 16000 - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
            send({"type": "audio", "pcm": base64.b64encode(pcm[offset:offset + 3200].astype("<f4").tobytes()).decode()})
        send({"type": "stop"})
        process.stdin.close()
        process.wait(timeout=120)
        output_thread.join(timeout=5); error_thread.join(timeout=5)
        assert process.returncode == 0, "".join(errors)[-4000:]
        sources = [(stamp, event) for stamp, event in events if event["type"] == "source"]
        assert sources and sources[-1][1]["final"], sources
        final = sources[-1][1]
        assert "train" in final["text"].lower() and "morning" in final["text"].lower(), final
        if dual:
            assert all(event.get("revisable") for _, event in sources)
            assert sources[0][0] - started < len(samples) / 16000, "No text while speech was flowing"
            assert any(event.get("confirmed_text") and not event["final"] for _, event in sources), sources
            if args.translation:
                translated = [event for _, event in events if event["type"] == "translation" and event["final"]]
                assert translated and translated[-1]["revision"] == final["revision"], translated
        return {
            "mode": "dual" if dual else "single", "delay_ms": delay,
            "text": final["text"], "source_events": len(sources),
            "first_text_seconds": round(sources[0][0] - started, 3),
            "finish_seconds": round(sources[-1][0] - started, 3),
            "updates": [event["text"] for _, event in sources],
        }
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        output_thread.join(timeout=5); error_thread.join(timeout=5)
        for stream in [process.stdin, process.stdout, process.stderr]:
            stream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--translation")
    parser.add_argument("--worker", default=str(worker_path / "worker.py"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        samples = fixture_audio(directory)
        slow = run(args, samples, directory, False, 1200)
        dual = run(args, samples, directory, True, 240)
        assert dual["text"] == slow["text"], (dual, slow)
        print(json.dumps({"check": "dual_delay", "slow_reference": slow, "paced_dual": dual}, ensure_ascii=False))


if __name__ == "__main__":
    main()
