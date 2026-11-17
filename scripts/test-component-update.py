#!/usr/bin/env python3
"""Mac acceptance for signed component swaps and tamper rejection.

Uses the existing local release key, disposable fixtures under dist, and a
release-built GPUI executable. No captured audio or user installation is used.
"""
import base64
import hashlib
import platform
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]; os.chdir(ROOT)
release=json.loads((ROOT/'Sources/CaptionFlow/Resources/worker/release.json').read_text())
OLD_SEQUENCE=release['sequence']; NEW_SEQUENCE=OLD_SEQUENCE+1
OLD_VERSION=release['version']
parts=OLD_VERSION.split('.'); parts[-1]=str(int(parts[-1])+1); NEW_VERSION='.'.join(parts)
assert sys.platform == 'darwin', 'This installed-bundle acceptance uses macOS'
directory=ROOT/'dist/update-acceptance'
if directory.exists():shutil.rmtree(directory)
directory.mkdir(parents=True)
app=directory/'CaptionFlow Test.app'; stage=directory/'.CaptionFlow-update-acceptance';stage.mkdir()
marker=directory/'launched.txt'

def make_bundle(path,text,sequence):
 (path/'Contents/MacOS').mkdir(parents=True)
 (path/'Contents/Resources/worker').mkdir(parents=True)
 (path/'Contents/Resources/python/lib').mkdir(parents=True)
 (path/'Contents/Resources/python/lib/runtime.bin').write_bytes(hashlib.shake_256(b'unchanged runtime fixture').digest(1024*1024))
 source=directory/(text+'.c')
 source.write_text('#include <stdio.h>\nint main(){FILE*f=fopen('+json.dumps(str(marker))+',"w");if(f){fputs('+json.dumps(text)+',f);fclose(f);}return 0;}\n')
 subprocess.run(['/usr/bin/clang',str(source),'-o',str(path/'Contents/MacOS/Probe')],check=True,env={**os.environ,'DEVELOPER_DIR':'/Library/Developer/CommandLineTools'})
 (path/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleExecutable='Probe',CFBundleIdentifier='dev.captionflow.update-acceptance',CFBundleName='CaptionFlow Test',CFBundlePackageType='APPL',CFBundleShortVersionString=OLD_VERSION if sequence==OLD_SEQUENCE else NEW_VERSION,CFBundleVersion=str(sequence))))
 (path/'Contents/Resources/worker/release.json').write_text(json.dumps(dict(version=OLD_VERSION if sequence==OLD_SEQUENCE else NEW_VERSION,sequence=sequence)))
 subprocess.run([sys.executable,'scripts/sign-app.py',str(path)],check=True,stdout=subprocess.DEVNULL)

make_bundle(app,'old',OLD_SEQUENCE)
replacement=stage/app.name;make_bundle(replacement,'new',NEW_SEQUENCE)
subprocess.run([sys.executable,'scripts/package-update.py','--root',str(app),'--platform','macos-aarch64','--output',str(directory/'old-payload')],check=True,stdout=subprocess.DEVNULL)
subprocess.run([sys.executable,'scripts/package-update.py','--root',str(replacement),'--platform','macos-aarch64','--output',str(directory/'payload')],check=True,stdout=subprocess.DEVNULL)
old=json.loads(base64.b64decode(json.loads((directory/'old-payload/gpui-macos-aarch64.json').read_text())['payload']))
new=json.loads(base64.b64decode(json.loads((directory/'payload/gpui-macos-aarch64.json').read_text())['payload']))
assert old['components']['python']['sha256']==new['components']['python']['sha256'], 'Unchanged runtime would be downloaded again'
changed=sum(component['size'] for name,component in new['components'].items() if component['sha256']!=old['components'][name]['sha256'])
full=sum(component['size'] for component in new['components'].values())
assert changed < full / 2, (changed,full)
print(f'Changed components: {changed} bytes; full download: {full} bytes; unchanged Python reused')
shutil.copy2(directory/'payload/gpui-macos-aarch64.json',stage/'manifest.json')
helper=ROOT/'gpui/target'/('aarch64-apple-darwin' if platform.machine()=='arm64' else 'x86_64-apple-darwin')/'release/CaptionFlowGPUI'
subprocess.run([str(helper),'--apply-update',str(stage),str(app),'99999999'],check=True,timeout=20)
for _ in range(50):
 if marker.exists():break
 time.sleep(.1)
assert marker.read_text()=='new','Replacement did not relaunch'
assert (stage/'previous/Contents/MacOS/Probe').exists(),'Previous installation not preserved'
assert json.loads((app/'Contents/Resources/worker/release.json').read_text())['sequence']==NEW_SEQUENCE
print('Signed component update: verified, installed, relaunched; previous install retained')

# A tampered stage must leave the existing installation intact.
marker.unlink();bad=directory/'.CaptionFlow-update-tampered';bad.mkdir()
shutil.copytree(stage/'previous',bad/app.name)
shutil.copy2(stage/'manifest.json',bad/'manifest.json')
(bad/app.name/'Contents/Resources/worker/release.json').write_text('tampered')
subprocess.run([str(helper),'--apply-update',str(bad),str(app),'99999999'],check=True,timeout=20)
assert (bad/'update-error.txt').exists(),'Tampered update was not rejected'
assert json.loads((app/'Contents/Resources/worker/release.json').read_text())['sequence']==NEW_SEQUENCE
print('Tampered component rejected; installed app remained intact')
