"""Regenerate synthetic mixed-call fixtures after building caption_audio.

fr-call-local.wav was generated with macOS Amélie:
  say -v Amélie -o /tmp/local.aiff 'Bonjour, je vous entends bien. Nous pouvons commencer la réunion.'
  afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/local.aiff docs/fixtures/fr-call-local.wav
No captured conversation is used.
"""
import hashlib
import json
from pathlib import Path
import sys
import wave
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
from audio_pipeline import Mixer

def load(path):
    with wave.open(str(path)) as stream:
        return np.frombuffer(stream.readframes(stream.getnframes()),'<i2').astype('f4')/32768

def main():
    directory=ROOT/'docs/fixtures'
    far=load(directory/'fr-synthetic.wav');near=load(directory/'fr-call-local.wav')
    reference=json.loads((directory/'fr-synthetic.json').read_text())['reference']
    for name,offset in [('turns',len(far)+8000),('overlap',32000)]:
        n=((max(len(far),offset+len(near))+16000+159)//160)*160
        system=np.zeros(n,'f4');system[:len(far)]=far
        mic=np.zeros(n,'f4');mic[offset:offset+len(near)]=near;mic[480:]+=system[:-480]*.45
        mixer=Mixer(0);frames=[]
        try:
            for i in range(0,n,160):
                mixer.push(0,mic[i:i+160],i/16000);mixer.push(1,system[i:i+160],i/16000)
                frames.append(mixer.pull(i/16000))
        finally:mixer.close()
        pcm=(np.clip(np.concatenate(frames),-1,1)*32767).astype('<i2')
        path=directory/f'fr-call-{name}.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,16000,0,'NONE','not compressed'));stream.writeframes(pcm.tobytes())
        manifest={'audio':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'language':'fr',
                  'kind':'synthetic_mixed_call_'+name,'reference':reference+' Bonjour, je vous entends bien. Nous pouvons commencer la réunion.',
                  'overlapping_speech':name=='overlap',
                  'provenance':'Synthetic Thomas and Amélie voices, 30ms simulated speaker echo, shared CaptionAudio mixer. Not a real-room accuracy corpus.'}
        path.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
