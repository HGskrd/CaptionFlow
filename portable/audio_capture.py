"""Isolated hardware capture. stdout is float32 PCM only. Diagnostics contain metrics only."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import traceback
import numpy as np
from audio_pipeline import Mixer, RATE, FRAME, LATENCY


def capture(mode, diagnostic, echo=True):
    import soundcard as sc
    if os.name != 'nt' and mode != 'microphone':
        raise RuntimeError('Use the native Mac app for simultaneous Mac audio and microphone capture')
    started=time.monotonic(); origin=started+.25
    mixer=Mixer(origin,mode,echo)
    stopped=threading.Event(); errors=[]; seen={}; peaks={}; channels={}; ready=set()
    sources=([0] if mode=='microphone' else [1] if mode=='system' else [0,1])
    state_lock=threading.Lock()
    report={'schema':1,'mode':mode,'echo_cancellation':bool(echo and mode=='mixed'),
            'sample_rate':RATE,'inference_streams':1,'alignment':'arrival-estimated with drift correction',
            'buffer_seconds':LATENCY,'status':'starting'}
    diagnostic=Path(diagnostic); diagnostic.parent.mkdir(parents=True,exist_ok=True)
    def save(status, code=None):
        report.update(mixer.stats()); report.update(status=status,elapsed_seconds=time.monotonic()-started)
        with state_lock:
            report['source_channels']=dict(channels); report['source_peak_rms']=dict(peaks)
            report['source_last_callback_age']={str(k):round(time.monotonic()-v,3) for k,v in seen.items()}
        report['failure_code']=code
        tmp=diagnostic.with_suffix('.tmp');tmp.write_text(json.dumps(report,indent=2));tmp.replace(diagnostic)
    save('starting')
    def record(source):
        com=False
        try:
            if os.name=='nt':
                result=ctypes.windll.ole32.CoInitializeEx(None,0)
                if result not in (0,1):raise RuntimeError('Audio thread COM initialization failed')
                com=True
            device=sc.default_microphone() if source==0 else sc.get_microphone(sc.default_speaker().id,include_loopback=True)
            if device is None:raise RuntimeError('Required audio endpoint unavailable')
            # All native channels, then downmix. SoundCard mono WASAPI capture is unreliable.
            with device.recorder(samplerate=RATE,blocksize=FRAME*2) as recorder:
                while not stopped.is_set():
                    block=recorder.record(numframes=FRAME*2)
                    stamp=time.monotonic()-len(block)/RATE
                    mono=np.mean(block,axis=1).astype(np.float32)
                    mixer.push(source,mono,stamp)
                    with state_lock:
                        seen[source]=time.monotonic();ready.add(source);channels[str(source)]=block.shape[1]
                        peaks[str(source)]=max(peaks.get(str(source),0),float(np.sqrt(np.mean(mono*mono))))
        except Exception as exc:
            with state_lock:errors.append((source,type(exc).__name__))
            stopped.set()
        finally:
            if com:ctypes.windll.ole32.CoUninitialize()
    threads=[threading.Thread(target=record,args=(s,),daemon=True) for s in sources]
    for thread in threads:thread.start()
    import sys
    next_frame=None; pending=[]; last_save=0; processing=0
    try:
        while not stopped.is_set():
            now=time.monotonic()
            with state_lock:
                ages=[now-seen.get(s,started) for s in sources]; all_ready=len(ready)==len(sources)
            if max(ages)>(3 if all_ready else 10):raise RuntimeError('capture_callback_stalled')
            if not all_ready:
                stopped.wait(.01);continue
            if next_frame is None: next_frame=now
            wait=next_frame+LATENCY-now
            if wait>0:stopped.wait(min(wait,.02));continue
            if -wait>.5:raise RuntimeError('capture_processing_backlog')
            before=time.perf_counter();pending.append(mixer.pull(next_frame));processing+=time.perf_counter()-before
            next_frame+=FRAME/RATE
            if len(pending)==10:
                sys.stdout.buffer.write(np.concatenate(pending).astype('<f4').tobytes());sys.stdout.buffer.flush();pending=[]
            if now-last_save>=2:
                report['dsp_seconds']=processing;save('capturing');last_save=now
        if errors:raise RuntimeError('capture_device_error_'+str(errors[0][0])+'_'+errors[0][1])
    except Exception as exc:
        # No exception strings/device names/paths/audio enter this report.
        code=str(exc) if str(exc).startswith('capture_') else type(exc).__name__
        save('failed',code);raise
    finally:
        stopped.set()
        for thread in threads:thread.join(timeout=.5)
        # A wedged driver thread is discarded with this process. Do not free its mixer.
        if not any(thread.is_alive() for thread in threads):mixer.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['microphone','system','mixed'],required=True)
    p.add_argument('--diagnostic',required=True);p.add_argument('--no-echo',action='store_true');a=p.parse_args()
    try:capture(a.mode,a.diagnostic,not a.no_echo)
    except Exception as exc:
        path=Path(a.diagnostic)
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'schema':1,'status':'failed','failure_code':type(exc).__name__,'phase':'capture_startup','inference_streams':1}))
        raise SystemExit(1)
