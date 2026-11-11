"""Deterministic mixed-call DSP test. Synthetic signals only; no device access."""
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from audio_pipeline import Mixer, RATE, FRAME

def run(output):
    rng=np.random.default_rng(7); n=RATE*15
    far=rng.normal(0,.12,n).astype('f4');near=rng.normal(0,.04,n).astype('f4');near[:RATE*10]=0
    mic=np.zeros(n,'f4');mic[480:]=far[:-480]*.6;mic+=near
    mixer=Mixer(0,echo=True);frames=[];began=time.perf_counter()
    try:
        for i in range(0,n,FRAME):
            mixer.push(0,mic[i:i+FRAME],i/RATE);mixer.push(1,far[i:i+FRAME],i/RATE)
            frames.append(mixer.pull(i/RATE))
        elapsed=time.perf_counter()-began
        residual=np.concatenate(frames)-far
        erle=float(10*np.log10(np.mean(mic[RATE*7:RATE*9]**2)/max(1e-12,np.mean(residual[RATE*7:RATE*9]**2))))
        correlation=float(np.corrcoef(residual[RATE*12:RATE*14],near[RATE*12-800:RATE*14-800])[0,1])
        report={'schema':1,'kind':'synthetic_mixed_call_dsp','inference_streams':1,'signal_seconds':15,
                'processing_seconds':elapsed,'processing_realtime_factor':elapsed/15,
                'echo_reduction_db':erle,'overlap_near_end_correlation':correlation,
                'passed':erle>=10 and correlation>=.6 and elapsed<15,
                'scope':'Synthetic linear echo and overlap; excludes hardware capture, ASR and real-room validation',
                **mixer.stats()}
        Path(output).write_text(json.dumps(report,indent=2));return report
    finally:mixer.close()
