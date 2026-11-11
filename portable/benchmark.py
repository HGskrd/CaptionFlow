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
import os
import platform

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


def trial(config, fixture_path, duration, mode, cancelled=None, checkpoint=None):
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
    result = {"status": "error", "mode": mode, "audio_seconds": 0, "planned_audio_seconds": seconds, "repetitions": repetitions,
              "fixture_sha256": fixture["sha256"], "fixture_kind": fixture.get("kind", "unspecified"),
              "quality_scope": "overlap_stress_no_ordered_wer" if fixture.get("overlapping_speech") else "fixture_only", "power_watts": None, "gpu_utilization": None,
              "vram_peak_bytes": None, "temperature_c": None}
    sent_frames = 0
    last_source = [time.monotonic()]
    fault = []
    last_backlog = [0]
    began = None
    stage = "probe"
    def receive(timestamp, event):
        if event["type"] == "source":
            last_source[0] = timestamp
            last_backlog[0] = event.get("backlog_seconds", 0)
        if event["type"] == "error":
            fault.append(event.get("message", "Worker error"))
        if event["type"] not in ("status",):
            events.append((timestamp, event))
    try:
        result["runtime"] = doctor(config)
        stage = "load"
        session = WorkerSession(config, receive)
        result["ready"] = session.ready(cancelled=cancelled)
        result["load_seconds"] = time.monotonic() - started
        process = psutil.Process(session.process.pid)
        def snapshot(status="running"):
            sources = [(t, e) for t, e in events if e["type"] == "source"]
            finals = {e["caption_id"]: (t, e) for t, e in sources if e.get("final")}
            lag = [t - boundaries[k] for k, (t, _) in finals.items() if k in boundaries]
            return {**result, "status": status, "planned_audio_seconds": seconds,
                    "audio_seconds": sent_frames / 16000, "partial": True,
                    "final_lag_seconds": distribution(lag) if mode == "realtime" else None,
                    "source_events": len(sources), "completed_final_captions": len(finals),
                    "worker_tree_peak_rss_bytes": max((v for _, v in memory), default=None),
                    "checkpoint_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        def monitor():
            while not finished.wait(.5):
                try:
                    tree = [process] + process.children(recursive=True)
                    memory.append((time.monotonic(), sum(p.memory_info().rss for p in tree)))
                    cpu.append(process.cpu_percent())
                    reason = health_failure(began, last_source[0], time.monotonic(), sent_frames / 16000,
                                            last_backlog[0] if mode == "realtime" else 0)
                    if reason and not fault:
                        fault.append(reason)
                    if fault:
                        session.process.kill()
                        return
                except (psutil.Error, OSError):
                    return
        monitor_thread = threading.Thread(target=monitor, daemon=True); monitor_thread.start()
        tracker = AudioInbox(float(config.get("silence_threshold", .008)))
        boundaries = {}
        began = time.monotonic()
        last_source[0] = began
        last_checkpoint = began
        # Hard timeout also breaks blocked pipe writes / wedged native inference.
        watchdog = threading.Timer(seconds + 120 if mode == "realtime" else 120, session.process.kill)
        watchdog.daemon = True; watchdog.start()
        try:
            for _ in range(repetitions):
                if cancelled and cancelled.is_set(): break
                for offset in range(0, len(samples), 3200):
                    chunk = samples[offset:offset + 3200].astype("<f4").tobytes()
                    if cancelled and cancelled.is_set(): break
                    if fault: raise RuntimeError(fault[0])
                    due = began + (sent_frames + len(chunk) // 4) / 16000
                    if mode == "realtime":
                        delay = max(0, due - time.monotonic())
                        if cancelled:
                            if cancelled.wait(delay): break
                        else: time.sleep(delay)
                    sent_frames += len(chunk) // 4
                    utterance = tracker.utterance
                    tracker.feed(chunk)
                    if tracker.utterance != utterance:
                        boundaries[str(utterance)] = due
                    session.send({"type": "audio", "pcm": base64.b64encode(chunk).decode()})
                    if checkpoint and time.monotonic() - last_checkpoint >= 5:
                        checkpoint(snapshot())
                        last_checkpoint = time.monotonic()
            if tracker.frames:
                boundaries[str(tracker.utterance)] = began + sent_frames / 16000
            fed = time.monotonic()
            if cancelled and cancelled.is_set():
                watchdog.cancel()
                watchdog = threading.Timer(5, session.process.kill)
                watchdog.daemon = True; watchdog.start()
            session.stop(timeout=100)
        finally:
            watchdog.cancel()
        ended = time.monotonic()
        failures = [e.get("message") for _, e in events if e["type"] == "error"]
        partial = bool(cancelled and cancelled.is_set())
        if not partial and (session.process.returncode != 0 or failures):
            raise RuntimeError(str(fault or failures or "Worker exited with code " + str(session.process.returncode)))
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
        partial = partial or sent_frames < len(samples) * repetitions
        result.update(status="partial" if partial else "ok", partial=partial,
                      planned_audio_seconds=seconds, audio_seconds=sent_frames / 16000,
                      elapsed_seconds=ended - began, drain_seconds=ended - fed,
                      realtime_factor=(ended - began) / seconds if mode == "throughput" and not partial else None,
                      first_text_seconds=sources[0][0] - began if sources else None,
                      final_lag_seconds=distribution(lags) if mode == "realtime" else None,
                      translation_lag_seconds=distribution(translation_lags) if mode == "realtime" else None,
                      missing_final_captions=len(set(boundaries) - set(finals)),
                      missing_final_translations=missing_translations,
                      word_error_rate=word_error(expected, text) if not partial and not fixture.get("overlapping_speech") else None,
                      revision_erasure_ratio=erased / max(1, emitted),
                      backlog_seconds=distribution([y for _, y in backlog]),
                      backlog_growth_seconds_per_second=slope(backlog),
                      worker_tree_peak_rss_bytes=max((v for _, v in memory), default=None),
                      rss_growth_bytes_per_second=slope(memory), cpu_percent=distribution(cpu),
                      capture_drops=None, transport_drops=0, source_events=len(sources),
                      translation_quality=None)
        # No blanket hardware recommendation from one synthetic phrase.
        if partial:
            return result
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
        if began is not None:
            result.update(snapshot("partial" if cancelled and cancelled.is_set() else "error"))
        result.update(error=redact(fault[0] if fault else exc),
                      status="partial" if cancelled and cancelled.is_set() and not fault else "error",
                      failure_stage=stage if began is None else "inference")
        if session:
            result["diagnostic_tail"] = list(session.logs)[-30:]
    finally:
        finished.set()
        if session:
            session.close()
    return result


def profile_identity(config, hashes=None):
    hashes = {} if hashes is None else hashes
    def digest(path):
        key = (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
        if key not in hashes: hashes[key] = sha256(path)
        return hashes[key]
    fields = ("speech_backend", "native_backend", "gpu", "nemotron_right_context", "source_language",
              "targets", "translation_backend", "translation_model_type", "silence_threshold",
              "translation_partials", "translation_threads")
    identity = {key: config.get(key) for key in fields}
    for key in ("speech_model", "translation_model"):
        path = Path(config.get(key, ""))
        identity[key] = {"name": path.name, "sha256": digest(path) if path.is_file() else None}
    for key in ("native_sdk", "llama_server"):
        path = Path(config.get(key, ""))
        if config.get(key) and path.exists():
            files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in (".dll", ".dylib", ".so", ".exe")) if path.is_dir() else [path]
            identity[key + "_sha256"] = hashlib.sha256("".join(digest(p) for p in files).encode()).hexdigest()
    identity["worker_sha256"] = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(WORKER_DIR.glob("*.py")))).hexdigest()
    return identity


def save_report(connection, output, run_id, machine):
    rows = [json.loads(row[0]) for row in connection.execute("SELECT result FROM trials ORDER BY rowid")]
    report = {"schema": SCHEMA, "run_id": run_id, "hardware": machine, "trials": rows,
              "disabled_profiles": [{"profile_index": index, "status": "disabled"} for index, reason in connection.execute("SELECT profile,reason FROM disabled")],
              "privacy": "No audio, transcript, hostname or serial; review diagnostics before sharing.",
              "coverage": "Worker-protocol replay; microphone-to-display latency and translation accuracy are not measured."}
    path = Path(output) / "report.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return report


def health_failure(began, last_source, now, audio_seconds, backlog):
    if began is None or audio_seconds < 5: return None
    if backlog > 10: return "Speech backlog exceeded 10 seconds"
    if now - began > 20 and now - last_source > 15:
        return "No recognition progress for 15 seconds"
    return None


def benchmark_signature(identities, fixture, trial_seconds, machine):
    # Include the benchmark, client, dependencies and runtime binaries, not only worker.py.
    implementation = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")) + sorted(Path(__file__).parent.glob("caption_audio.*")) + [Path(__file__).parent / "requirements.txt"]:
        implementation.update(path.name.encode()); implementation.update(path.read_bytes())
    spec = {"profiles": identities, "fixture": fixture, "trial_seconds": trial_seconds,
            "hardware": machine, "runner": implementation.hexdigest(), "python": platform.python_version()}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def _run_suite(configs, fixture, output, hours=None, trial_seconds=60, cancelled=None, progress=print):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    fixture_manifest, _ = load_fixture(fixture)
    profiles = [json.loads(Path(p).read_text(encoding="utf-8")) for p in configs]
    if not profiles: raise ValueError("No benchmark profiles")
    hashes = {}
    identities = [profile_identity(config, hashes) for config in profiles]
    machine = hardware()
    signature = benchmark_signature(identities, fixture_manifest, trial_seconds, machine)
    # An update gets its own history automatically. Older results stay exportable.
    output = output / signature[:16]
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output / "journal.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS trials (id TEXT PRIMARY KEY, result TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS slots (id TEXT PRIMARY KEY, remaining REAL, state TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS disabled (profile INTEGER PRIMARY KEY, reason TEXT)")
    stored = dict(connection.execute("SELECT key,value FROM metadata"))
    run_id = stored.get("run_id", str(uuid.uuid4()))
    with connection:
        for key, value in {"signature": signature, "run_id": run_id, "hardware": json.dumps(machine)}.items():
            connection.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
        # A process/OS crash leaves its last five-second checkpoint. Preserve it,
        # quarantine that profile, and move on rather than crash-looping on resume.
        for slot, in connection.execute("SELECT id FROM slots WHERE state='running'").fetchall():
            index = int(slot.split(":")[1])
            connection.execute("INSERT OR REPLACE INTO disabled VALUES (?,?)", (index, "Previous run was interrupted unexpectedly"))
            connection.execute("UPDATE slots SET state='failed' WHERE id=?", (slot,))
            for rid, raw in connection.execute("SELECT id,result FROM trials WHERE id LIKE ?", (slot + ":%",)).fetchall():
                result = json.loads(raw)
                if result["status"] == "running":
                    result.update(status="interrupted", partial=True)
                    connection.execute("UPDATE trials SET result=? WHERE id=?", (json.dumps(result), rid))
    progress(f"Results: {output}. Matching runs resume; updates start a separate history.")
    deadline = time.monotonic() + hours * 3600 if hours is not None else float("inf")
    stop = cancelled or threading.Event()
    timer = None
    if hours is not None:
        timer = threading.Timer(max(0, hours * 3600), stop.set); timer.daemon = True; timer.start()
    cycle = 0
    try:
        while time.monotonic() < deadline and not stop.is_set():
            disabled = {row[0] for row in connection.execute("SELECT profile FROM disabled")}
            if len(disabled) == len(profiles):
                progress("All profiles failed or stalled. Results saved; nothing will be retried until the configuration or app changes.")
                break
            for index, config in enumerate(profiles):
                if index in disabled: continue
                for mode in ("throughput", "realtime"):
                    slot = f"{cycle}:{index}:{mode}"
                    previous = connection.execute("SELECT remaining,state FROM slots WHERE id=?", (slot,)).fetchone()
                    if previous and previous[1] in ("done", "failed"): continue
                    if stop.is_set(): return save_report(connection, output, run_id, machine)
                    remaining = previous[0] if previous else trial_seconds
                    identity = slot + ":" + uuid.uuid4().hex[:12]
                    def persist(result):
                        value = {**result, "id": identity, "profile": identities[index],
                                 "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                        with connection:
                            connection.execute("INSERT OR REPLACE INTO trials VALUES (?,?)", (identity, json.dumps(value)))
                        save_report(connection, output, run_id, machine)
                    with connection:
                        connection.execute("INSERT OR REPLACE INTO slots VALUES (?,?,?)", (slot, remaining, "running"))
                    persist({"status": "running", "partial": True, "mode": mode, "audio_seconds": 0,
                             "fixture_sha256": fixture_manifest["sha256"]})
                    progress(f"Cycle {cycle + 1} · profile {index + 1}/{len(profiles)} · {mode} · {remaining:.0f}s remaining")
                    result = trial(config, fixture, remaining, mode, stop, checkpoint=persist)
                    failed = result["status"] == "error" or (result["status"] == "ok" and
                             result.get("realtime_gate") is False)
                    if failed:
                        result["failure_detected"] = True
                    persist(result)
                    with connection:
                        if result["status"] in ("partial", "cancelled"):
                            # Restart the decoder, retaining measured exposure and the unfinished slot.
                            left = max(0, remaining - result.get("audio_seconds", 0)) if mode == "realtime" else remaining
                            connection.execute("UPDATE slots SET remaining=?,state=? WHERE id=?", (left, "paused" if left > 0 else "done", slot))
                        else:
                            connection.execute("UPDATE slots SET state=? WHERE id=?", ("failed" if failed else "done", slot))
                        if failed:
                            reason = result.get("error", "Realtime/accuracy gate failed")
                            affected = [i for i, candidate in enumerate(profiles) if candidate.get("native_backend") == config.get("native_backend")] if result.get("failure_stage") == "probe" else [index]
                            for failed_index in affected:
                                connection.execute("INSERT OR REPLACE INTO disabled VALUES (?,?)", (failed_index, reason))
                                disabled.add(failed_index)
                    progress(f"{mode}: {result['status']}" + (" — profile skipped; trying the next configuration" if failed else " — saved"))
                    if failed: break
            cycle += 1
        return save_report(connection, output, run_id, machine)
    finally:
        if timer: timer.cancel()
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
            group["trials"] += 1; group["errors"] += result["status"] in ("error", "interrupted")
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
    run.add_argument("--hours", type=float, default=None, help="Omit to run until stopped"); run.add_argument("--trial-seconds", type=float, default=60)
    combine = sub.add_parser("aggregate"); combine.add_argument("reports", nargs="+"); combine.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "aggregate":
        aggregate(args.reports, args.output)
    else:
        if (args.hours is not None and args.hours <= 0) or args.trial_seconds <= 0:
            parser.error("Durations must be positive")
        import signal
        cancelled = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: cancelled.set())
        signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
        run_suite(args.config, args.fixture, args.output, args.hours, args.trial_seconds, cancelled)


if __name__ == "__main__":
    main()
