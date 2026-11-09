"""Owned, loopback-only llama.cpp translation server; CUDA-free packaged builds."""
import atexit
import collections
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from caption_core import visible_words


class LlamaTranslator:
    def __init__(self, configuration, emit, message_builder):
        self.emit = emit
        self.messages = message_builder
        self.model_type = configuration.get("translation_model_type", "hunyuan_v1_dense")
        self.process = None
        self.job_handle = None
        self.logs = collections.deque(maxlen=40)
        self.secret = secrets.token_urlsafe(32)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        command = [configuration["llama_server"], "--model", configuration["translation_model"],
                   "--host", "127.0.0.1", "--port", str(port), "--api-key", self.secret,
                   "--alias", "captionflow", "--ctx-size", "2048", "--parallel", "1",
                   "--n-gpu-layers", "0" if configuration.get("gpu", -1) < 0 else "99",
                   "--offline", "--log-verbosity", "4"]
        self.gpu_layers = None
        if configuration.get("gpu", -1) < 0:
            command += ["--device", "none", "--threads", str(configuration.get("translation_threads", 2)),
                        "--threads-batch", str(configuration.get("translation_threads", 2))]
        elif configuration.get("llama_device"):
            command += ["--device", configuration["llama_device"]]
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if os.name == "nt":
            self._own_windows_process()
        threading.Thread(target=self._read_logs, daemon=True).start()
        atexit.register(self.close)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("Translation runtime exited: " + "\n".join(self.logs))
                try:
                    with self.request("/health", timeout=1) as response:
                        if json.load(response).get("status") == "ok":
                            if configuration.get("gpu", -1) >= 0 and not self.gpu_layers:
                                raise RuntimeError("Translation runtime did not confirm GPU layer offload; select CPU explicitly or export diagnostics")
                            return
                except (OSError, urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            raise TimeoutError("Translation model was not ready within 120 seconds")
        except BaseException:
            self.close()
            raise

    def _read_logs(self):
        try:
            for line in self.process.stdout:
                self.logs.append(line.strip().replace(self.secret, "<redacted>"))
                match = re.search(r"offloaded (\d+)/(\d+) layers", line)
                if match:
                    self.gpu_layers = int(match.group(1))
        except (OSError, ValueError):
            pass

    def _own_windows_process(self):
        # A worker crash/TerminateProcess must not leave a resident model server.
        import ctypes as C
        from ctypes import wintypes as W
        class Basic(C.Structure):
            _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", W.DWORD),
                        ("min_ws", C.c_size_t), ("max_ws", C.c_size_t), ("active", W.DWORD),
                        ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]
        class Limits(C.Structure):
            _fields_ = [("basic", Basic), ("io", C.c_uint64 * 6),
                        ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                        ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]
        kernel.CreateJobObjectW.restype = W.HANDLE
        kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = Limits(); limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not kernel.SetInformationJobObject(handle, 9, C.byref(limits), C.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(self.process._handle)):
            if handle:
                kernel.CloseHandle(handle)
            self.process.kill(); self.process.wait()
            raise OSError(C.get_last_error(), "Could not contain the translation process")
        self.job_handle = (kernel, handle)

    def request(self, endpoint, value=None, timeout=120):
        headers = {"Authorization": "Bearer " + self.secret, "Content-Type": "application/json"}
        data = json.dumps(value).encode() if value is not None else None
        return urllib.request.urlopen(urllib.request.Request(self.url + endpoint, data=data, headers=headers), timeout=timeout)

    def translate(self, text, language, source_language, revision, caption_id):
        messages = self.messages(self.model_type, text, language)
        request = {"model": "captionflow", "messages": messages, "temperature": 0,
                   "max_tokens": 160, "stream": True, "cache_prompt": True}
        output = ""; last = ""
        with self.request("/v1/chat/completions", request) as response:
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                if "error" in event:
                    raise RuntimeError(str(event["error"]))
                choices = event.get("choices", [])
                if not choices:
                    continue
                output += choices[0].get("delta", {}).get("content") or ""
                visible = visible_words(output)
                if visible and visible != last:
                    last = visible
                    self.emit("translation", revision=revision, caption_id=caption_id, language=language,
                              text=visible, cached_tokens=0, final=False)
        self.emit("translation", revision=revision, caption_id=caption_id, language=language,
                  text=output.strip(), cached_tokens=0, final=True)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
            if self.process.stdout:
                self.process.stdout.close()
        if self.job_handle:
            kernel, handle = self.job_handle
            kernel.CloseHandle(handle)
            self.job_handle = None
