"""Check every shipped native archive and import optional code without networking."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
args=parser.parse_args();root=args.root.resolve()
sys.path.insert(0,str(root/'Sources/CaptionFlow/Resources/worker'))
from runtime_assets import NEMO_HASHES,NEMO_VERSION,LLAMA_ASSETS,LLAMA_TAG,sha256,extract
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
def blocked(*args,**kwargs): raise AssertionError('Code check attempted a network connection')
socket.socket.connect=blocked
import numpy,psutil,cffi,cryptography,rapidfuzz,torch,fermion
from PySide6.QtWidgets import QApplication
assert torch.version.cuda is None
archives=root/'portable/data/downloads'
for backend in ['cpu','vulkan']:
    key='windows-x86_64-'+backend
    name=f'nemo-speech-{NEMO_VERSION}-{key}.zip'
    assert sha256(archives/name)==NEMO_HASHES[key],name
    suffix,digest=LLAMA_ASSETS[key]
    name=f'llama-{LLAMA_TAG}-bin-{suffix}'
    assert sha256(archives/name)==digest,name
# The CPU SDK must load using bundled redistributables, without a model download.
with tempfile.TemporaryDirectory() as temporary:
    archive=archives/f'nemo-speech-{NEMO_VERSION}-windows-x86_64-cpu.zip'
    sdk=extract(archive,Path(temporary)/'nemo')
    result=subprocess.run([str(sdk/'bin/nemo-speech.exe'),'doctor','--json'],capture_output=True,text=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    evidence=json.loads(result.stdout)
    assert evidence['features']['backend_cuda'] is False,evidence
print(json.dumps({'bundled_native_archives':4,'optional_phonon_code_imported':True,'cuda_required':False,'network_connections_allowed':False}))
