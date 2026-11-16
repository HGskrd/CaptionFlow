"""Optional, session-long Sortformer sidecar using the pinned NeMo C ABI.

Audio remains in memory. Unavailable or lagging diarization disables speaker
labels without stopping captions. Mixed/ambiguous turns stay unlabelled.
"""
import collections
import ctypes as C
from pathlib import Path
import queue
import threading
from nemo_stream import Sized


class ModelConfig(Sized):
    _fields_ = [('size',C.c_size_t),('model_path',C.c_char_p),('gpu',C.c_int32),('preset',C.c_char_p)] + [(name,C.c_int32) for name in ('chunk_frames','right_context_frames','left_context_frames','fifo_frames','spkcache_frames','update_period_frames')]


class Segment(C.Structure):
    _fields_ = [('start',C.c_double),('end',C.c_double),('speaker',C.c_int32)]


MODEL_ID = 'nvidia/Nemotron-3-Diarization'
MODEL_REVISION = 'f667ed73aee57d40cc39428eb768b4fd87a0a29e'
MODEL_FILE = 'Nemotron-3-Diarization.q8_0.gguf'
MODEL_SHA256 = '08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1'


def resolve_model(value, cache, allow_network, progress):
    if value != MODEL_ID: return value
    from runtime_assets import download
    return str(download(f'https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{MODEL_FILE}',
                        Path(cache)/MODEL_FILE,MODEL_SHA256,progress=progress,allow_network=allow_network))


def dominant_speaker(start, end, segments):
    scores = collections.defaultdict(float)
    for left,right,speaker in segments:
        overlap = max(0,min(end,right)-max(start,left))
        if speaker > 0 and overlap: scores[str(speaker)] += overlap
    if not scores: return None
    speaker = max(scores,key=scores.get)
    return speaker if scores[speaker] / sum(scores.values()) >= .65 else None


class LiveDiarization:
    def __init__(self, library, model_path, gpu, emit):
        path=Path(model_path).expanduser().resolve()
        if not path.is_file(): raise FileNotFoundError('Choose a local Sortformer GGUF speaker model')
        self.lib=library;self.emit=emit;self.model=C.c_void_p();self.stream=C.c_void_p()
        ptr=C.c_void_p
        signatures={
            'create':([C.POINTER(ModelConfig),C.POINTER(ptr)],C.c_int),
            'destroy':([ptr],None),'stream_open':([ptr,C.POINTER(ptr)],C.c_int),
            'stream_push_f32':([ptr,C.POINTER(C.c_float),C.c_size_t,C.c_int32],C.c_int),
            'stream_finish':([ptr],C.c_int),'stream_close':([ptr],None),
            'segments':([ptr,ptr,C.POINTER(Segment),C.c_size_t,C.POINTER(C.c_size_t)],C.c_int)}
        for name,(args,result) in signatures.items():
            function=getattr(self.lib,'nemo_speech_diar_'+name);function.argtypes=args;function.restype=result
        config=ModelConfig(model_path=str(path).encode(),gpu=gpu,left_context_frames=-1)
        self.check(self.lib.nemo_speech_diar_create(C.byref(config),C.byref(self.model)))
        try:self.check(self.lib.nemo_speech_diar_stream_open(self.model,C.byref(self.stream)))
        except BaseException:self.lib.nemo_speech_diar_destroy(self.model);raise
        self.queue=queue.Queue(maxsize=250);self.closed=threading.Event();self.disabled=False
        self.lock=threading.Lock();self.ranges=collections.OrderedDict();self.labels={};self.segments=[]
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def check(self,status):
        if status:
            message=self.lib.nemo_speech_asr_last_error()
            raise RuntimeError(message.decode(errors='replace') if message else 'Diarization runtime failed')

    def feed(self,frame):
        if self.closed.is_set() or self.disabled:return
        try:self.queue.put_nowait(frame)
        except queue.Full:
            self.disabled=True
            self.emit('status',message='Speaker detection cannot keep up; captions continue without new speaker labels.')

    def register(self,caption_id,start,end):
        with self.lock:
            self.ranges[str(caption_id)]=(start,end)
            while len(self.ranges)>200:
                old,_=self.ranges.popitem(last=False);self.labels.pop(old,None)
            self.label_turns()

    def label_turns(self):
        for caption_id,(start,end) in self.ranges.items():
            speaker=dominant_speaker(start,end,self.segments)
            if speaker is None and caption_id in self.labels:
                self.labels.pop(caption_id)
                self.emit('speaker',caption_id=caption_id,speaker_id='')
            elif speaker is not None and self.labels.get(caption_id)!=speaker:
                self.labels[caption_id]=speaker
                self.emit('speaker',caption_id=caption_id,speaker_id=speaker)

    def refresh(self):
        count=C.c_size_t()
        self.check(self.lib.nemo_speech_diar_segments(self.stream,None,None,0,C.byref(count)))
        values=(Segment*count.value)()
        self.check(self.lib.nemo_speech_diar_segments(self.stream,None,values,count.value,C.byref(count)))
        with self.lock:
            self.segments=[(value.start,value.end,value.speaker) for value in values]
            self.label_turns()

    def run(self):
        try:
            while not self.disabled and (not self.closed.is_set() or not self.queue.empty()):
                try:frame=self.queue.get(timeout=.1)
                except queue.Empty:continue
                self.check(self.lib.nemo_speech_diar_stream_push_f32(self.stream,frame.ctypes.data_as(C.POINTER(C.c_float)),len(frame),16000))
                self.refresh()
            if not self.disabled:
                self.check(self.lib.nemo_speech_diar_stream_finish(self.stream));self.refresh()
        except Exception as exc:
            self.disabled=True
            self.emit('status',message=f'Speaker detection unavailable; captions continue: {exc}')
        finally:
            self.lib.nemo_speech_diar_stream_close(self.stream);self.lib.nemo_speech_diar_destroy(self.model)

    def finish(self):
        self.closed.set();self.thread.join(timeout=1)
