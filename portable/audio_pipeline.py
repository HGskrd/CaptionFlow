"""One mono timeline for native microphone and output capture. No network or storage of PCM."""
import ctypes as C
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import numpy as np
from common import hidden_process

RATE = 16000
FRAME = 160
LATENCY = .15

class Stats(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ('frames','missing_mic','missing_system','resyncs','drift_samples','limited_frames')] + [
                (name, C.c_float) for name in ('mic_rms','system_rms','cleaned_mic_rms','output_peak')]

class Mixer:
    def __init__(self, origin, mode='mixed', echo=True):
        suffix = '.dll' if os.name == 'nt' else '.dylib' if sys.platform == 'darwin' else '.so'
        self.lib = C.CDLL(str(Path(__file__).with_name('caption_audio'+suffix)))
        ptr = C.POINTER(C.c_float)
        self.lib.cf_audio_create.argtypes = [C.c_double,C.c_int,C.c_int]
        self.lib.cf_audio_create.restype = C.c_void_p
        self.lib.cf_audio_destroy.argtypes = [C.c_void_p]
        self.lib.cf_audio_push.argtypes = [C.c_void_p,C.c_int,ptr,C.c_int,C.c_double]
        self.lib.cf_audio_pull.argtypes = [C.c_void_p,C.c_double,ptr]
        self.lib.cf_audio_stats.argtypes = [C.c_void_p,C.POINTER(Stats)]
        self.handle = self.lib.cf_audio_create(origin, {'microphone':1,'system':2,'mixed':3}[mode], int(echo))
        if not self.handle: raise RuntimeError('Could not initialize audio mixer')
        self.lock = threading.Lock()
    def push(self, source, samples, start):
        data = np.ascontiguousarray(samples, dtype=np.float32)
        if data.ndim != 1 or len(data) >= 16384: raise ValueError('Expected a mono audio block shorter than one second')
        with self.lock:
            self.lib.cf_audio_push(self.handle, source, data.ctypes.data_as(C.POINTER(C.c_float)), len(data), start)
    def pull(self, start):
        data = np.zeros(FRAME, dtype=np.float32)
        with self.lock:
            self.lib.cf_audio_pull(self.handle,start,data.ctypes.data_as(C.POINTER(C.c_float)))
        return data
    def stats(self):
        value = Stats()
        with self.lock: self.lib.cf_audio_stats(self.handle,C.byref(value))
        return {name:getattr(value,name) for name,_ in Stats._fields_}
    def close(self):
        with self.lock:
            if self.handle: self.lib.cf_audio_destroy(self.handle); self.handle = None

class AudioProcess:
    """Bounded IPC and watchdog. Driver threads never live in the UI process."""
    def __init__(self, mode, diagnostic, echo=True):
        self.mode=mode; self.diagnostic=Path(diagnostic); self.echo=echo
        self.process=None; self.frames=queue.Queue(maxsize=30); self.error=None
        self.done=threading.Event(); self.reader=None
    def start(self):
        # pythonw has no console streams; explicitly use the sibling console binary
        # with CREATE_NO_WINDOW and pipes for this binary protocol.
        python=Path(sys.executable)
        if python.name.lower()=='pythonw.exe': python=python.with_name('python.exe')
        args=[str(python),'-u',str(Path(__file__).with_name('audio_capture.py')),
              '--mode',self.mode,'--diagnostic',str(self.diagnostic)]
        if not self.echo: args.append('--no-echo')
        self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,**hidden_process())
        def read():
            try:
                while not self.done.is_set():
                    data=self.process.stdout.read(1600*4)
                    if not data: break
                    if len(data)!=6400: raise RuntimeError('Audio capture ended mid-frame')
                    try:self.frames.put(data,timeout=.5)
                    except queue.Full: raise RuntimeError('Transcription transport is over three seconds behind')
            except Exception as exc:self.error=str(exc)
            finally:self.done.set()
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
    def next(self, cancelled, timeout=5):
        deadline=time.monotonic()+timeout
        while not cancelled.is_set():
            if self.error: raise RuntimeError(self.error)
            try:return self.frames.get(timeout=.1)
            except queue.Empty:
                if self.done.is_set():
                    raise RuntimeError(self.error or 'Audio capture stopped. Check audio diagnostics; reconnect the device and start again.')
                if time.monotonic()>deadline:
                    raise RuntimeError('Audio capture stalled for five seconds. Stopped the audio helper; export diagnostics.')
        return None
    def close(self):
        self.done.set()
        if self.process:
            if self.process.poll() is None:self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
            if self.reader:self.reader.join(timeout=2)
            self.process.stdout.close()
