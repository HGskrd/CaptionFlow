"""Paced offline French -> English stress check using the actual worker protocol.

Run with the app runtime's Python. Models must already exist locally. Audio is
synthesized with macOS say, never captured. JSON output includes every final's
lag relative to its actual input boundary, so growing backlog is visible.
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

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Sources/CaptionFlow/Resources/worker"))
from worker import AudioInbox, PROTOCOL
sys.stdout = PROTOCOL


def check(args):
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "fixture.wav")
        subprocess.run([
            "/usr/bin/say", "-v", "Thomas (French (France))", "-r", "165", "-o", path,
            "--file-format=WAVE", "--data-format=LEI16@16000",
            "Nous avons envoyé des négociateurs pour organiser la réunion. "
            "Le train part demain matin à neuf heures. Les voyageurs doivent arriver "
            "à la gare avec leurs billets. Nous allons maintenant examiner les résultats "
            "de cette expérience et répondre aux questions du public.",
        ], check=True)
        rate, speech = wavfile.read(path)
        assert rate == 16000
        speech = speech.astype(np.float32) / 32768
        samples = np.tile(speech, int(np.ceil(args.seconds * rate / len(speech))))[:int(args.seconds * rate)]
        samples = np.concatenate([samples, np.zeros(16000, dtype=np.float32)])
        events, stderr = [], []
        ready = queue.Queue()
        process = subprocess.Popen([sys.executable, "-u", args.worker], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                        "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
        def read_output():
            try:
                for line in process.stdout:
                    event = json.loads(line)
                    events.append((time.monotonic(), event))
                    if event["type"] in {"ready", "error"}:
                        ready.put(event)
            finally:
                ready.put(None)
        output = threading.Thread(target=read_output, daemon=True)
        errors = threading.Thread(target=lambda: stderr.append(process.stderr.read()), daemon=True)
        output.start(); errors.start()
        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()
        try:
            send({"type": "configure", "speech_model": args.model,
                  "translation_model": args.translation or "unused",
                  "targets": ["English"] if args.translation else [], "source_language": "fr",
                  "allow_downloads": False, "cache_dir": directory, "silence_threshold": 0.001,
                  "voxtral_dual_delay": True, "voxtral_delay_ms": 240,
                  "voxtral_correction_delay_ms": 1200})
            event = ready.get(timeout=120)
            assert event and event["type"] == "ready", event
            # Track the same segmentation as the input side, independently
            # from recognition timing and transcript content.
            tracker = AudioInbox(0.001)
            boundaries = {}
            started = time.monotonic()
            for offset in range(0, len(samples), 3200):
                remaining = started + (offset + 3200) / rate / args.rate - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                pcm = samples[offset:offset + 3200].astype("<f4").tobytes()
                utterance = tracker.utterance
                tracker.feed(pcm)
                if tracker.utterance != utterance:
                    boundaries[str(utterance)] = time.monotonic()
                send({"type": "audio", "pcm": base64.b64encode(pcm).decode()})
            if tracker.frames:
                boundaries[str(tracker.utterance)] = time.monotonic()
            sent = time.monotonic()
            send({"type": "stop"}); process.stdin.close()
            process.wait(timeout=180)
            output.join(timeout=5); errors.join(timeout=5)
            assert process.returncode == 0, "".join(stderr)[-4000:]
            failures = [e for _, e in events if e["type"] == "error"]
            assert not failures, failures
            sources = [(t, e) for t, e in events if e["type"] == "source"]
            finals = [(t, e) for t, e in sources if e["final"]]
            assert finals and sources[0][0] < sent, "No live captions"
            assert {e["caption_id"] for _, e in finals} == set(boundaries), "Lost utterance"
            lags = [round(t - boundaries[e["caption_id"]], 3) for t, e in finals]
            translated = [(t, e) for t, e in events if e["type"] == "translation" and e["final"]]
            if args.translation:
                assert translated and translated[-1][1]["revision"] == finals[-1][1]["revision"]
            result = {
                "audio_seconds": len(samples) / rate, "input_rate": args.rate,
                "first_text_seconds": round(sources[0][0] - started, 3),
                "source_events": len(sources), "final_lag_seconds": lags,
                "source_drain_seconds": round(finals[-1][0] - sent, 3),
                "translation_drain_seconds": round(translated[-1][0] - sent, 3) if translated else None,
                "completed_translations": len(translated),
                "final_texts": [e["text"] for _, e in finals],
            }
            print(json.dumps(result, ensure_ascii=False), flush=True)
            assert max(lags) <= args.max_final_lag, f"Recognition fell behind: {lags}"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            output.join(timeout=5); errors.join(timeout=5)
            for stream in [process.stdin, process.stdout, process.stderr]:
                stream.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--translation")
    parser.add_argument("--worker", default=str(ROOT / "Sources/CaptionFlow/Resources/worker/worker.py"))
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--rate", type=float, default=1)
    parser.add_argument("--max-final-lag", type=float, default=5)
    args = parser.parse_args()
    if args.seconds <= 0 or args.rate <= 0:
        parser.error("seconds and rate must be positive")
    check(args)
