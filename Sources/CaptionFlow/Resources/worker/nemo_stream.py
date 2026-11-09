"""Bindings to the pinned NeMo-Speech.cpp 0.3.0 ASR C ABI (Apache-2.0).

ABI definitions: NVIDIA/NeMo-Speech.cpp/include/nemo_speech/asr.h.
Models stay resident, streams retain encoder/decoder state, PCM stays in memory.
"""
import ctypes as C
import os
from pathlib import Path
import sys
import time


class Sized(C.Structure):
    def __init__(self, **values):
        super().__init__()
        self.size = C.sizeof(self)
        for key, value in values.items():
            setattr(self, key, value)


class Backend(Sized):
    _fields_ = [("size", C.c_size_t), ("gpu", C.c_int32)]


class Model(Sized):
    _fields_ = [("size", C.c_size_t), ("path", C.c_char_p), ("name", C.c_char_p)]


class Streaming(Sized):
    _fields_ = [("size", C.c_size_t), ("chunk_size", C.c_float),
                ("left", C.c_float), ("right", C.c_float), ("rnnt_right_context", C.c_int32)]


class Endpointing(Sized):
    _fields_ = [("size", C.c_size_t), ("enable", C.c_bool),
                ("vad_based", C.c_bool), ("stop_history_eou_ms", C.c_int32)]


class Config(Sized):
    _fields_ = [("size", C.c_size_t)] + [(name, C.c_void_p) for name in
                ("backend", "model", "streaming", "decoder", "vad", "endpointing",
                 "postproc", "diar", "batching", "salm")]


class Options(Sized):
    _fields_ = [("size", C.c_size_t), ("request_id", C.c_char_p), ("language_code", C.c_char_p),
                ("interim_results", C.c_bool), ("word_offsets", C.c_bool),
                ("punctuation", C.c_bool), ("verbatim", C.c_bool), ("profanity", C.c_bool),
                ("stop_history_eou_ms", C.c_int32), ("speech_contexts", C.c_void_p),
                ("speech_context_count", C.c_size_t), ("max_alternatives", C.c_int32),
                ("diarization", C.c_bool), ("max_speaker_count", C.c_int32)]


def address(value):
    return C.addressof(value)


class NemoRecognizer:
    def __init__(self, sdk_path, model_path, gpu=-1, right_context=3):
        if right_context not in (0, 3, 6, 13):
            raise ValueError("Nemotron right context must be 0, 3, 6 or 13.")
        root = Path(sdk_path).resolve()
        self.dll_directories = []
        if sys.platform == "win32":
            for directory in (root / "bin", root / "lib"):
                if directory.is_dir():
                    self.dll_directories.append(os.add_dll_directory(str(directory)))
            paths = list(root.glob("bin/*asr_c*.dll")) + list(root.glob("lib/*asr_c*.dll"))
        elif sys.platform == "darwin":
            paths = list(root.glob("lib/libnemo_speech_asr_c*.dylib"))
        else:
            paths = list(root.glob("lib/libnemo_speech_asr_c.so*"))
        if not paths:
            raise FileNotFoundError("NeMo ASR library is missing. Run portable setup first.")
        self.lib = C.CDLL(str(sorted(paths)[0]))
        self.handle = C.c_void_p()
        self._bind()
        self.version = self.lib.nemo_speech_asr_version().decode()
        if self.version != "nemo-speech-asr 0.3.0":
            raise RuntimeError(f"Unsupported NeMo C ABI {self.version}; install pinned 0.3.0")
        backend = Backend(gpu=gpu)
        model = Model(path=str(Path(model_path).resolve()).encode("utf-8"))
        streaming = Streaming(chunk_size=0.16, rnnt_right_context=right_context)
        endpointing = Endpointing(enable=False)
        config = Config(backend=address(backend), model=address(model),
                        streaming=address(streaming), endpointing=address(endpointing))
        self.check(self.lib.nemo_speech_asr_create(C.byref(config), C.byref(self.handle)))

    def _bind(self):
        ptr = C.c_void_p
        signatures = {
            "version": ([], C.c_char_p), "last_error": ([], C.c_char_p),
            "create": ([C.POINTER(Config), C.POINTER(ptr)], C.c_int),
            "destroy": ([ptr], None),
            "recognition_options_default": ([], Options),
            "streaming_recognize": ([ptr, C.POINTER(Options), C.POINTER(ptr)], C.c_int),
            "stream_push_f32": ([ptr, C.POINTER(C.c_float), C.c_size_t, C.c_int32], C.c_int),
            "stream_next": ([ptr, C.POINTER(ptr)], C.c_int),
            "stream_finish": ([ptr], C.c_int), "stream_close": ([ptr], None),
            "result_is_final": ([ptr], C.c_bool),
            "result_audio_processed": ([ptr], C.c_float),
            "result_alternative_count": ([ptr], C.c_size_t),
            "result_transcript": ([ptr, C.c_size_t], C.c_char_p),
            "result_destroy": ([ptr], None),
        }
        for suffix, (args, result) in signatures.items():
            function = getattr(self.lib, "nemo_speech_asr_" + suffix)
            function.argtypes, function.restype = args, result

    def check(self, status):
        if status:
            message = self.lib.nemo_speech_asr_last_error()
            raise RuntimeError(message.decode("utf-8", errors="replace") if message else f"NeMo error {status}")

    def open_stream(self, language):
        options = self.lib.nemo_speech_asr_recognition_options_default()
        options.language_code = (language if language != "auto" else "").encode()
        options.interim_results = True
        stream = C.c_void_p()
        self.check(self.lib.nemo_speech_asr_streaming_recognize(self.handle, C.byref(options), C.byref(stream)))
        return stream

    def push(self, stream, samples, final=False):
        import numpy as np
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        if samples.size:
            self.check(self.lib.nemo_speech_asr_stream_push_f32(
                stream, samples.ctypes.data_as(C.POINTER(C.c_float)), len(samples), 16000))
        if final:
            self.check(self.lib.nemo_speech_asr_stream_finish(stream))
        while True:
            result = C.c_void_p()
            self.check(self.lib.nemo_speech_asr_stream_next(stream, C.byref(result)))
            if not result:
                break
            try:
                text = ""
                if self.lib.nemo_speech_asr_result_alternative_count(result):
                    text = self.lib.nemo_speech_asr_result_transcript(result, 0).decode("utf-8")
                yield text.strip(), self.lib.nemo_speech_asr_result_is_final(result), float(
                    self.lib.nemo_speech_asr_result_audio_processed(result))
            finally:
                self.lib.nemo_speech_asr_result_destroy(result)

    def close(self):
        if self.handle:
            self.lib.nemo_speech_asr_destroy(self.handle)
            self.handle = C.c_void_p()
        for directory in self.dll_directories:
            directory.close()
        self.dll_directories.clear()


def run_nemo_stream(inbox, recognizer, source_language, publish):
    stream = None
    try:
        while (job := inbox.next_stream()) is not None:
            utterance, samples, final = job
            if stream is None:
                stream = recognizer.open_stream(source_language)
                last_text = ""
            started = time.monotonic()
            got_final = False
            for text, is_final, processed in recognizer.push(stream, samples, final):
                if text and (text != last_text or is_final):
                    last_text = text
                    publish(utterance, text, source_language, is_final, time.monotonic() - started,
                            audio_processed_seconds=processed, revisable=True)
                    got_final |= is_final
            if final:
                if last_text and not got_final:
                    publish(utterance, last_text, source_language, True, time.monotonic() - started,
                            revisable=True)
                recognizer.lib.nemo_speech_asr_stream_close(stream)
                stream = None
    finally:
        if stream:
            recognizer.lib.nemo_speech_asr_stream_close(stream)
        recognizer.close()
