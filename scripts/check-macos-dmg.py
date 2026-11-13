#!/usr/bin/env python3
"""Verify a DMG, copy its app as an install, and run bundled offline inference."""
import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time
import wave

from ds_store import DSStore

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('--arch', choices=['arm64', 'x86_64'], required=True)
parser.add_argument('--cache', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
environment = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONNOUSERSITE='1', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_XET='1')
for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'):
    environment.pop(key, None)
subprocess.run(['hdiutil', 'verify', str(args.image.resolve())], check=True, stdout=subprocess.DEVNULL)
with tempfile.TemporaryDirectory(prefix=f'captionflow-install-{args.arch}-') as temporary:
    root = Path(temporary)
    mount = root/'Disk image'
    mount.mkdir()
    subprocess.run(['hdiutil', 'attach', str(args.image.resolve()), '-readonly', '-nobrowse', '-noautoopen',
                    '-mountpoint', str(mount)], check=True, stdout=subprocess.DEVNULL)
    try:
        assert sorted(p.name for p in mount.iterdir() if not p.name.startswith('.')) == ['Applications', 'CaptionFlow.app']
        assert os.readlink(mount/'Applications') == '/Applications'
        with DSStore.open(str(mount/'.DS_Store'), 'r') as store:
            locations = {record.filename: record.value for record in store if record.code == b'Iloc'}
        assert locations['CaptionFlow.app'] == (160, 192), locations
        assert locations['Applications'] == (440, 192), locations
        app = root/'Applications/CaptionFlow.app'
        app.parent.mkdir()
        subprocess.run(['ditto', str(mount/'CaptionFlow.app'), str(app)], check=True)
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    subprocess.run(['/Library/Developer/CommandLineTools/usr/bin/lipo', str(app/'Contents/MacOS/CaptionFlow'),
                    '-verify_arch', args.arch], check=True)
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    expected_minimum = '26.0' if args.arch == 'arm64' else '14.0'
    assert info['LSMinimumSystemVersion'] == expected_minimum, info
    assert (app/'Contents/Resources/CaptionFlow.icns').is_file()
    python = app/'Contents/Resources/python/bin/python3'
    environment['SSL_CERT_FILE'] = str(app/'Contents/Resources/python/lib/python3.11/site-packages/certifi/cacert.pem')
    imports = 'import numpy, huggingface_hub, ssl; assert ssl.create_default_context().get_ca_certs()'
    if args.arch == 'arm64':
        imports += '; import mlx.core, mlx_whisper, mlx_lm, mlx_audio'
    probe = subprocess.check_output([str(python), '-B', '-I', '-c',
        'import platform,sys,json; '+imports+'; print(json.dumps(dict(arch=platform.machine(),prefix=sys.prefix)))'],
        env=environment, text=True)
    probe = json.loads(probe)
    assert probe['arch'] == args.arch, probe
    assert Path(probe['prefix']).resolve().is_relative_to(app.resolve()), probe
    fixture = root/'speech.wav'
    subprocess.run(['/usr/bin/say', '-v', 'Samantha', '-r', '145', '-o', str(fixture), '--file-format=WAVE',
                    '--data-format=LEI16@16000', 'The train leaves at nine tomorrow morning.'], check=True)
    with wave.open(str(fixture)) as audio:
        assert audio.getframerate() == 16000 and audio.getnchannels() == 1
        samples = audio.readframes(audio.getnframes())
    # Convert PCM with the bundled NumPy, avoiding a build-machine runtime dependency.
    pcm = subprocess.check_output([str(python), '-B', '-I', '-c',
        "import sys,numpy as np; sys.stdout.buffer.write((np.frombuffer(sys.stdin.buffer.read(),dtype='<i2').astype('<f4')/32768).tobytes())"],
        input=samples, env=environment)
    configuration = {
        'type': 'configure', 'speech_model': 'mlx-community/whisper-small-mlx' if args.arch == 'arm64' else 'nvidia/nemotron-3.5-asr-streaming-0.6b',
        'translation_model': 'mlx-community/Qwen2.5-1.5B-Instruct-4bit' if args.arch == 'arm64' else 'tencent/Hy-MT2-1.8B-GGUF',
        'targets': ['French'], 'source_language': 'en', 'cache_dir': str(args.cache.resolve()),
        'allow_downloads': False, 'silence_threshold': 0.008,
    }
    messages = [json.dumps(configuration)]
    for offset in range(0, len(pcm), 3200*4):
        messages.append(json.dumps({'type': 'audio', 'pcm': base64.b64encode(pcm[offset:offset+3200*4]).decode()}))
    messages += [json.dumps({'type': 'audio', 'pcm': base64.b64encode(bytes(16000*4)).decode()}), json.dumps({'type': 'stop'})]
    started = time.monotonic()
    result = subprocess.run([str(python), '-B', '-u', str(app/'Contents/Resources/worker/worker.py')],
                            input='\n'.join(messages)+'\n', env=environment, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    events = [json.loads(line) for line in result.stdout.splitlines()]
    sources = [event for event in events if event['type'] == 'source' and event.get('final')]
    translations = [event for event in events if event['type'] == 'translation' and event.get('final')]
    assert sources and 'train' in sources[-1]['text'].lower() and 'morning' in sources[-1]['text'].lower(), events
    assert translations and 'train' in translations[-1]['text'].lower(), events
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    report = {'architecture': args.arch, 'version': info['CFBundleShortVersionString'], 'minimum_macos': expected_minimum,
              'dmg_verified': True, 'finder_layout_verified': True, 'relocated_signature_verified': True,
              'bundled_python_verified': True, 'external_python_required': False, 'downloads_during_inference': False,
              'source': sources[-1]['text'], 'translation': translations[-1]['text'], 'inference_seconds': round(time.monotonic()-started, 2),
              'execution': 'native Apple Silicon' if args.arch == 'arm64' else 'Rosetta on Apple Silicon'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(report, ensure_ascii=False))
