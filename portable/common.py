"""Portable paths, private worker transport, and bounded diagnostics."""
import collections
import json
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "Sources/CaptionFlow/Resources/worker"
sys.path.insert(0, str(WORKER_DIR))


def hidden_process():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def redact(text):
    text = str(text).replace(str(Path.home()), "<home>").replace(str(ROOT), "<app>")
    for name in ("USERNAME", "USER", "COMPUTERNAME"):
        value = os.environ.get(name)
        if value and len(value) > 2:
            text = text.replace(value, "<redacted>")
    return text


def hardware():
    import psutil
    info = {"os": platform.system(), "os_release": platform.release(), "arch": platform.machine(),
            "cpu": platform.processor(), "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total,
            "gpus": [], "power_watts": None, "temperature_c": None}
    try:
        if os.name == "nt":
            # Explicit property allowlist. No serials, hostnames, users, device IDs or MACs.
            command = "$c=Get-CimInstance Win32_Processor|Select-Object -ExpandProperty Name; $g=@(Get-CimInstance Win32_VideoController|Select-Object Name,DriverVersion); @{cpu=$c;gpus=$g}|ConvertTo-Json -Depth 4"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True,
                                    text=True, timeout=20, check=True, **hidden_process())
            info.update(json.loads(result.stdout))
        elif sys.platform == "darwin":
            info["cpu"] = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        info["hardware_probe_incomplete"] = True
    return info


def doctor(config):
    if config.get("speech_backend") in {"phonon_cpu"}:
        from small_models import cpu_runtime_check
        return {"requested": "cpu", "placement": "explicit_cpu", **cpu_runtime_check()}
    if config.get("speech_backend") != "nemo":
        return {"requested": "mlx", "placement": "not_probed"}
    binary = Path(config["native_sdk"]) / "bin" / ("nemo-speech.exe" if os.name == "nt" else "nemo-speech")
    result = subprocess.run([str(binary), "doctor", "--json"], capture_output=True, text=True,
                            timeout=30, check=True, **hidden_process())
    raw = json.loads(result.stdout)
    backend = config.get("native_backend", "cpu")
    features = raw.get("features", {})
    if features.get("backend_cuda") is not False:
        raise RuntimeError("Expected a verified CUDA-free native runtime")
    if backend != "cpu" and (not features.get("backend_" + backend) or not raw.get("accelerator_available")):
        raise RuntimeError(f"{backend} is unavailable; explicitly install/select the CPU profile to test CPU")
    gpu = int(config.get("gpu", -1))
    devices = raw.get("devices", [])
    selected = next((d for d in devices if d.get("index") == gpu and d.get("type") == "gpu"), None)
    if backend != "cpu" and selected is None:
        raise RuntimeError(f"GPU index {gpu} is not a reported GPU")
    if backend == "cpu" and gpu != -1:
        raise ValueError("CPU profile must use gpu=-1")
    return {"version": raw.get("version"), "requested": backend, "cuda_compiled": False,
            "selected_device": selected, "devices": devices,
            "placement": "explicit_device_requested; per_operator_offload_not_measured",
            "warnings": raw.get("runtime_warnings", [])}


class WorkerSession:
    def __init__(self, config, callback=None):
        self.events = queue.Queue()
        self.logs = collections.deque(maxlen=120)
        self.callback = callback
        self.waiting_ready = True
        self.lock = threading.Lock()
        self.process = subprocess.Popen([sys.executable, "-u", str(WORKER_DIR / "worker.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}, **hidden_process())
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._logs, daemon=True)]
        for thread in self.readers:
            thread.start()
        self.send({**config, "type": "configure", "allow_downloads": False})

    def _read(self):
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                timestamp = time.monotonic()
                if self.waiting_ready:
                    self.events.put((timestamp, event))
                if self.callback:
                    self.callback(timestamp, event)
        except (ValueError, OSError) as exc:
            self.logs.append(redact(exc))
        finally:
            if self.waiting_ready:
                self.events.put((time.monotonic(), {"type": "eof"}))

    def _logs(self):
        try:
            for line in self.process.stderr:
                self.logs.append(redact(line.strip()[:2000]))
        except (ValueError, OSError):
            pass

    def send(self, event):
        with self.lock:
            self.process.stdin.write(json.dumps(event) + "\n")
            self.process.stdin.flush()

    def ready(self, timeout=180, cancelled=None):
        deadline = time.monotonic() + timeout
        while True:
            if cancelled and cancelled.is_set():
                raise InterruptedError("Cancelled while loading")
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker startup exceeded limit")
            try:
                _, event = self.events.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if event["type"] == "ready":
                self.waiting_ready = False
                return event
            if event["type"] in ("error", "eof"):
                raise RuntimeError(event.get("message", "Worker exited during startup") + "\n" + "\n".join(self.logs)[-8000:])
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker startup exceeded limit")

    def stop(self, timeout=60):
        if self.process.poll() is None:
            self.send({"type": "stop"})
            self.process.stdin.close()
            self.process.wait(timeout=timeout)
        for thread in self.readers:
            thread.join(timeout=2)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        for thread in self.readers:
            thread.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
