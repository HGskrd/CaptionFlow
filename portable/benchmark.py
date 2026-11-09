"""Resumable, process-isolated local benchmark. No automatic upload or private audio."""
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import sqlite3
import statistics
import sys
import threading
import time
import uuid
import wave

import numpy as np
import psutil
from common import ROOT, WORKER_DIR, WorkerSession, doctor, hardware, redact
from runtime_assets import sha256
# Audio boundaries must be identical to the production worker's segmentation.
_saved_stdout = sys.stdout
from worker import AudioInbox
sys.stdout = _saved_stdout

SCHEMA = 1


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)


def distribution(values):
    return {"count": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95),
            "p99": percentile(values, .99), "max": max(values) if values else None}


def slope(points):
    if len(points) < 2:
        return None
    x, y = zip(*points)
    center = statistics.mean(x)
    denominator = sum((v - center) ** 2 for v in x)
    return sum((a - center) * (b - statistics.mean(y)) for a, b in points) / denominator if denominator else None


def word_error(reference, hypothesis):
    from rapidfuzz.distance import Levenshtein
    normalize = lambda text: re.findall(r"\w+", text.casefold(), re.UNICODE)
    expected, actual = normalize(reference), normalize(hypothesis)
    return Levenshtein.distance(expected, actual) / len(expected) if expected else None


def load_fixture(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    audio = Path(path).parent / manifest["audio"]
    if sha256(audio) != manifest["sha256"]:
        raise ValueError("Fixture hash mismatch")
    with wave.open(str(audio), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError("Fixture must be mono PCM16 WAV at 16 kHz")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if not len(samples) or len(samples) > 16000 * 20:
        raise ValueError("Use a fixture between 0 and 20 seconds; the suite repeats it for endurance")
    return manifest, samples


def trial(config, fixture_path, duration, mode, cancelled=None):
    fixture, samples = load_fixture(fixture_path)
    # Silence preserves phrase boundaries without losing end-of-speech tails.
    samples = np.concatenate([samples, np.zeros(16000, dtype=np.float32)])
    repetitions = max(1, math.ceil(duration * 16000 / len(samples))) if mode == "realtime" else 1
    seconds = len(samples) * repetitions / 16000
    events = []
    memory = []
    cpu = []
    finished = threading.Event()
    session = None
    started = time.monotonic()
    result = {"status": "error", "mode": mode, "audio_seconds": seconds, "repetitions": repetitions,
              "fixture_sha256": fixture["sha256"], "fixture_kind": fixture.get("kind", "unspecified"),
              "quality_scope": "fixture_only", "power_watts": None, "gpu_utilization": None,
              "vram_peak_bytes": None, "temperature_c": None}
    def receive(timestamp, event):
        if event["type"] not in ("status",):
            events.append((timestamp, event))
    try:
        result["runtime"] = doctor(config)
        session = WorkerSession(config, receive)
        result["ready"] = session.ready(cancelled=cancelled)
        result["load_seconds"] = time.monotonic() - started
        process = psutil.Process(session.process.pid)
        def monitor():
            while not finished.wait(.5):
                try:
                    tree = [process] + process.children(recursive=True)
                    memory.append((time.monotonic(), sum(p.memory_info().rss for p in tree)))
                    cpu.append(process.cpu_percent())
                    if cancelled and cancelled.is_set():
                        session.process.terminate()
                        return
                except (psutil.Error, OSError):
                    return
        monitor_thread = threading.Thread(target=monitor, daemon=True); monitor_thread.start()
        tracker = AudioInbox(float(config.get("silence_threshold", .008)))
        boundaries = {}
        began = time.monotonic()
        sent_frames = 0
        # Hard timeout also breaks blocked pipe writes / wedged native inference.
        watchdog = threading.Timer(seconds + 120 if mode == "realtime" else 120, session.process.kill)
        watchdog.daemon = True; watchdog.start()
        try:
            for _ in range(repetitions):
                for offset in range(0, len(samples), 3200):
                    chunk = samples[offset:offset + 3200].astype("<f4").tobytes()
                    sent_frames += len(chunk) // 4
                    if cancelled and cancelled.is_set():
                        raise InterruptedError("Cancelled")
                    due = began + sent_frames / 16000
                    if mode == "realtime":
                        time.sleep(max(0, due - time.monotonic()))
                    utterance = tracker.utterance
                    tracker.feed(chunk)
                    if tracker.utterance != utterance:
                        boundaries[str(utterance)] = due
                    session.send({"type": "audio", "pcm": base64.b64encode(chunk).decode()})
            if tracker.frames:
                boundaries[str(tracker.utterance)] = began + seconds
            fed = time.monotonic()
            session.stop(timeout=100)
        finally:
            watchdog.cancel()
        ended = time.monotonic()
        failures = [e.get("message") for _, e in events if e["type"] == "error"]
        if session.process.returncode != 0 or failures:
            raise RuntimeError(str(failures or "Worker exited with code " + str(session.process.returncode)))
        sources = [(t, e) for t, e in events if e["type"] == "source"]
        finals = {e["caption_id"]: (t, e) for t, e in sources if e.get("final")}
        lags = [t - boundaries[key] for key, (t, _) in finals.items() if key in boundaries]
        translated = {(e["revision"], e["language"]): (t, e) for t, e in events if e["type"] == "translation" and e.get("final")}
        translation_lags = [translated[(e["revision"], lang)][0] - boundaries[key]
                            for key, (_, e) in finals.items() for lang in config.get("targets", [])
                            if key in boundaries and (e["revision"], lang) in translated]
        missing_translations = sum((e["revision"], lang) not in translated for _, e in finals.values()
                                   for lang in config.get("targets", []))
        text = " ".join(e["text"] for _, e in sorted(finals.values(), key=lambda pair: int(pair[1]["caption_id"])))
        expected = " ".join([fixture.get("reference", "")] * repetitions)
        backlog = [(t - began, e.get("backlog_seconds", 0)) for t, e in sources]
        revisions = {}; erased = 0; emitted = 0
        for _, e in sources:
            current = e["text"].split(); previous = revisions.get(e["caption_id"], [])
            common = 0
            for a, b in zip(current, previous):
                if a != b: break
                common += 1
            erased += len(previous) - common; emitted += max(0, len(current) - common)
            revisions[e["caption_id"]] = current
        result.update(status="ok", elapsed_seconds=ended - began, drain_seconds=ended - fed,
                      realtime_factor=(ended - began) / seconds if mode == "throughput" else None,
                      first_text_seconds=sources[0][0] - began if sources else None,
                      final_lag_seconds=distribution(lags) if mode == "realtime" else None,
                      translation_lag_seconds=distribution(translation_lags) if mode == "realtime" else None,
                      missing_final_captions=len(set(boundaries) - set(finals)),
                      missing_final_translations=missing_translations,
                      word_error_rate=word_error(expected, text),
                      revision_erasure_ratio=erased / max(1, emitted),
                      backlog_seconds=distribution([y for _, y in backlog]),
                      backlog_growth_seconds_per_second=slope(backlog),
                      worker_tree_peak_rss_bytes=max((v for _, v in memory), default=None),
                      rss_growth_bytes_per_second=slope(memory), cpu_percent=distribution(cpu),
                      capture_drops=None, transport_drops=0, source_events=len(sources),
                      translation_quality=None)
        # No blanket hardware recommendation from one synthetic phrase.
        if mode == "realtime":
            result["realtime_gate"] = bool(sources and result["missing_final_captions"] == 0
                and missing_translations == 0 and lags and max(lags) <= 5 and percentile(lags, .95) <= 2
                and (not config.get("targets") or (translation_lags and percentile(translation_lags, .95) <= 3))
                and (slope(backlog) or 0) <= .01 and result["word_error_rate"] is not None
                and result["word_error_rate"] <= .25)
        else:
            result["headroom_gate"] = bool(sources and result["missing_final_captions"] == 0
                                          and result["realtime_factor"] <= .7)
    except Exception as exc:
        result.update(error=redact(exc), status="cancelled" if cancelled and cancelled.is_set() else "error")
        if session:
            result["diagnostic_tail"] = list(session.logs)[-30:]
    finally:
        finished.set()
        if session:
            session.close()
    return result


def profile_identity(config):
    fields = ("speech_backend", "native_backend", "gpu", "nemotron_right_context", "source_language",
              "targets", "translation_backend", "translation_model_type", "silence_threshold",
              "translation_partials", "translation_threads")
    identity = {key: config.get(key) for key in fields}
    for key in ("speech_model", "translation_model"):
        path = Path(config.get(key, ""))
        identity[key] = {"name": path.name, "sha256": sha256(path) if path.is_file() else None}
    identity["worker_sha256"] = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(WORKER_DIR.glob("*.py")))).hexdigest()
    return identity


def save_report(connection, output, run_id, machine):
    rows = [json.loads(row[0]) for row in connection.execute("SELECT result FROM trials ORDER BY rowid")]
    report = {"schema": SCHEMA, "run_id": run_id, "hardware": machine, "trials": rows,
              "privacy": "No audio, transcript, hostname or serial; review diagnostics before sharing.",
              "coverage": "Worker-protocol replay; microphone-to-display latency and translation accuracy are not measured."}
    path = Path(output) / "report.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return report


def _run_suite(configs, fixture, output, hours=8, trial_seconds=600, cancelled=None, progress=print):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    fixture_manifest, _ = load_fixture(fixture)
    profiles = [(json.loads(Path(p).read_text(encoding="utf-8")), str(index)) for index, p in enumerate(configs)]
    identities = [profile_identity(config) for config, _ in profiles]
    specification = {"profiles": identities, "fixture": fixture_manifest, "trial_seconds": trial_seconds}
    signature = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
    machine = hardware()
    connection = sqlite3.connect(output / "journal.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS trials (id TEXT PRIMARY KEY, result TEXT)")
    stored = dict(connection.execute("SELECT key,value FROM metadata"))
    if stored and stored["signature"] != signature:
        connection.close()
        raise ValueError("This result folder belongs to a different benchmark specification; choose a new folder")
    if stored and json.loads(stored["hardware"]) != machine:
        connection.close()
        raise ValueError("Hardware changed; choose a new results folder")
    run_id = stored.get("run_id", str(uuid.uuid4()))
    with connection:
        for key, value in {"signature": signature, "run_id": run_id, "hardware": json.dumps(machine)}.items():
            connection.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
    deadline = time.monotonic() + hours * 3600
    cycle = 0
    try:
        while time.monotonic() < deadline and not (cancelled and cancelled.is_set()):
            for index, (config, name) in enumerate(profiles):
                for mode in ("throughput", "realtime"):
                    identity = f"{cycle}:{name}:{mode}"
                    if connection.execute("SELECT 1 FROM trials WHERE id=?", (identity,)).fetchone():
                        continue
                    if time.monotonic() >= deadline or (cancelled and cancelled.is_set()):
                        return save_report(connection, output, run_id, machine)
                    progress(f"Cycle {cycle + 1} · profile {index + 1}/{len(profiles)} · {mode}")
                    result = trial(config, fixture, trial_seconds, mode, cancelled)
                    result.update(id=identity, profile=identities[index], completed_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                    if result["status"] != "cancelled":
                        with connection:
                            connection.execute("INSERT INTO trials VALUES (?,?)", (identity, json.dumps(result)))
                    save_report(connection, output, run_id, machine)
                    progress(f"{mode}: {result['status']} · {result.get('error', result.get('final_lag_seconds', result.get('realtime_factor')))}")
            cycle += 1
        return save_report(connection, output, run_id, machine)
    finally:
        connection.close()


def run_suite(*args, **kwargs):
    # Keep overnight trials running without changing the user's power settings.
    import os
    import subprocess
    awake = None
    if os.name == "nt":
        import ctypes
        awake = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        if not awake: raise OSError("Could not prevent sleep during benchmark")
    elif sys.platform == "darwin":
        awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    try:
        return _run_suite(*args, **kwargs)
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif awake is not None:
            awake.terminate(); awake.wait()


def aggregate(paths, destination):
    # Deduplicate exported runs, then group exact hardware / model / runtime / fixture matches.
    seen = set(); groups = {}
    for path in paths:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        if report.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported report schema: {path}")
        for result in report["trials"]:
            identity = (report["run_id"], result["id"])
            if identity in seen: continue
            seen.add(identity)
            key = json.dumps([report["hardware"], result["profile"], result["fixture_sha256"],
                              result.get("runtime", {}).get("version")], sort_keys=True)
            group = groups.setdefault(key, {"hardware": report["hardware"], "profile": result["profile"],
                "fixture_sha256": result["fixture_sha256"], "trials": 0, "errors": 0,
                "realtime_passes": 0, "headroom_passes": 0, "tested_audio_seconds": 0})
            group["trials"] += 1; group["errors"] += result["status"] != "ok"
            group["realtime_passes"] += result.get("realtime_gate", False)
            group["headroom_passes"] += result.get("headroom_gate", False)
            group["tested_audio_seconds"] += result["audio_seconds"] if result["status"] == "ok" else 0
    Path(destination).write_text(json.dumps({"schema": SCHEMA, "capabilities": list(groups.values())}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", action="append", required=True)
    run.add_argument("--fixture", required=True); run.add_argument("--output", required=True)
    run.add_argument("--hours", type=float, default=8); run.add_argument("--trial-seconds", type=float, default=600)
    combine = sub.add_parser("aggregate"); combine.add_argument("reports", nargs="+"); combine.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "aggregate":
        aggregate(args.reports, args.output)
    else:
        if args.hours <= 0 or args.trial_seconds <= 0:
            parser.error("Durations must be positive")
        import signal
        cancelled = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: cancelled.set())
        signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
        run_suite(args.config, args.fixture, args.output, args.hours, args.trial_seconds, cancelled)


if __name__ == "__main__":
    main()
