"""Assemble a Windows x64 portable ZIP from official Python and pinned wheels.

Runs on macOS/Linux too; assembling Windows binaries is NOT a Windows runtime test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Sources/CaptionFlow/Resources/worker'))
from runtime_assets import NEMO_HASHES, NEMO_VERSION, LLAMA_ASSETS, LLAMA_TAG, download, sha256


def build(output, offline_models=False):
    staging = ROOT / 'dist/windows-staging/CaptionFlow'
    downloads = ROOT / 'dist/windows-downloads'; downloads.mkdir(parents=True, exist_ok=True)
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # Hash is recorded from the official download and reused by subsequent builds.
    lock_path = ROOT / 'portable/windows-assets.json'
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    python_name = 'python-3.14.10-embed-amd64.zip'
    python_url = 'https://www.python.org/ftp/python/3.14.10/' + python_name
    archive = downloads / python_name
    if python_name not in lock:
        with urllib.request.urlopen(python_url, timeout=60) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        lock[python_name] = sha256(archive)
    else:
        download(python_url, archive, lock[python_name])
    with zipfile.ZipFile(archive) as stream: stream.extractall(staging / 'python')
    (staging / 'python/python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../portable\n../Sources/CaptionFlow/Resources/worker\nimport site\n')
    wheels = downloads / 'wheels'
    # Never overlay old and new versions when requirements change.
    if wheels.exists(): shutil.rmtree(wheels)
    wheels.mkdir()
    subprocess.run([sys.executable, '-m', 'pip', 'download', '-r', str(ROOT / 'portable/requirements.txt'),
                    '--dest', str(wheels), '--only-binary=:all:', '--platform', 'win_amd64',
                    '--python-version', '3.14', '--implementation', 'cp'], check=True)
    packages = staging / 'python/Lib/site-packages'; packages.mkdir(parents=True)
    for wheel in sorted(wheels.glob('*.whl')):
        digest = sha256(wheel)
        if wheel.name in lock and lock[wheel.name] != digest: raise ValueError('Wheel digest changed: ' + wheel.name)
        lock[wheel.name] = digest
        with zipfile.ZipFile(wheel) as stream: stream.extractall(packages)
    for source, target in [('portable','portable'),('Sources/CaptionFlow/Resources/worker','Sources/CaptionFlow/Resources/worker'),('docs/fixtures','docs/fixtures')]:
        shutil.copytree(ROOT / source, staging / target, ignore=shutil.ignore_patterns('__pycache__','data','results','*.pyc'))
    data = staging / 'portable/data/downloads'; data.mkdir(parents=True)
    for backend in ('cpu','vulkan'):
        key = 'windows-x86_64-' + backend
        name = f'nemo-speech-{NEMO_VERSION}-{key}.zip'
        download(f'https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{NEMO_VERSION}/{name}', downloads / name, NEMO_HASHES[key])
        shutil.copy2(downloads / name, data / name)
        suffix, digest = LLAMA_ASSETS[key]; name = f'llama-{LLAMA_TAG}-bin-{suffix}'
        download(f'https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}', downloads / name, digest)
        shutil.copy2(downloads / name, data / name)
    if offline_models:
        shutil.copytree(ROOT / 'dist/portable-data/models', staging / 'portable/data/models')
    # Setup will rebuild absolute configuration paths on the recipient's machine.
    (staging / 'Start CaptionFlow.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\nif not exist "portable\\data" mkdir "portable\\data"\r\n"python\\python.exe" -u "portable\\app.py" >> "portable\\data\\launcher.log" 2>&1\r\nif errorlevel 1 (echo CaptionFlow could not start. See portable\\data\\launcher.log & pause)\r\n')
    (staging / 'README.txt').write_text('CaptionFlow Windows x64 preview\n\nExtract the entire ZIP before opening Start CaptionFlow.cmd. No Python or CUDA installation is required. A current Vulkan-capable graphics driver is required for GPU mode. Choose CPU if Vulkan is unavailable.\n\nFirst launch asks whether to participate online. No means no internet access: supply the pinned models offline, then click Download / verify models to configure cached files. Yes permits downloads, diagnostic metrics and signed update checks. No audio or transcript uploads.\n\nClick Download / verify models, then Start listening. Default microphone and Windows system audio are supported in code; Windows capture/GPU execution has not yet been validated on physical hardware.\n\nClick Start / resume benchmark. Choose Until I stop, Quick (5 minutes), 30 minutes or Overnight (8 hours). Setup and CPU fallback are automatic. Stop saves partial results; matching runs resume. Failed profiles are skipped. Keep the laptop plugged in with its lid open. Export diagnostics / results creates a ZIP for asynchronous troubleshooting. Keep portable/data when moving to a new release.\n\nThis is a preview assembled on macOS, not Windows-certified. Third-party licenses are included in the embedded Python, wheels and runtime archives.\n')
    lock_path.write_text(json.dumps(lock, indent=2) + '\n')
    (staging / 'dependency-sha256.json').write_text(json.dumps(lock, indent=2))
    output = Path(output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for path in sorted(staging.rglob('*')):
            if path.is_file(): bundle.write(path, 'CaptionFlow/' + str(path.relative_to(staging)))
    print(json.dumps({'artifact':str(output),'sha256':sha256(output),'size':output.stat().st_size}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'dist/CaptionFlow-Windows-x64.zip'))
    parser.add_argument('--offline-models',action='store_true')
    args=parser.parse_args(); build(args.output,args.offline_models)
