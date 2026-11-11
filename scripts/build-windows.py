"""Assemble a Windows x64 portable ZIP from official Python and pinned wheels.

Runs on macOS/Linux too; assembling Windows binaries is NOT a Windows runtime test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Sources/CaptionFlow/Resources/worker'))
from runtime_assets import NEMO_HASHES, NEMO_VERSION, LLAMA_ASSETS, LLAMA_TAG, download, sha256


def verify_windows_dependencies(wheels):
    # pip --platform still evaluates dependency markers against the build host.
    # Use the pinned closure, then validate it against Windows rather than pulling
    # Linux CUDA dependencies from otherwise CPU-only wheel metadata.
    from email.parser import BytesParser
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name
    metadata = []
    for wheel in wheels.glob('*.whl'):
        with zipfile.ZipFile(wheel) as archive:
            name = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            metadata.append(BytesParser().parsebytes(archive.read(name)))
    versions = {canonicalize_name(m['Name']): m['Version'] for m in metadata}
    environment = {**default_environment(), 'sys_platform': 'win32', 'os_name': 'nt',
                   'platform_system': 'Windows', 'platform_machine': 'AMD64',
                   'python_version': '3.14', 'python_full_version': '3.14.10', 'extra': ''}
    for model in metadata:
        for raw in model.get_all('Requires-Dist', []):
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            installed = versions.get(canonicalize_name(requirement.name))
            if installed is None or installed not in requirement.specifier:
                raise ValueError(f"Windows closure: {model['Name']} needs {raw}; found {installed}")
    if any(name.startswith(('nvidia-', 'cuda-')) for name in versions):
        raise ValueError('CUDA dependency in Windows payload')


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
                    '--dest', str(wheels), '--no-deps', '--only-binary=:all:', '--platform', 'win_amd64',
                    '--python-version', '3.14', '--implementation', 'cp'], check=True)
    verify_windows_dependencies(wheels)
    packages = staging / 'python/Lib/site-packages'; packages.mkdir(parents=True)
    for wheel in sorted(wheels.glob('*.whl')):
        digest = sha256(wheel)
        if wheel.name in lock and lock[wheel.name] != digest: raise ValueError('Wheel digest changed: ' + wheel.name)
        lock[wheel.name] = digest
        with zipfile.ZipFile(wheel) as stream: stream.extractall(packages)
    for source, target in [('portable','portable'),('Sources/CaptionFlow/Resources/worker','Sources/CaptionFlow/Resources/worker'),('docs/fixtures','docs/fixtures')]:
        shutil.copytree(ROOT / source, staging / target, ignore=shutil.ignore_patterns('__pycache__','data','results','*.pyc','caption_audio.*'))
    subprocess.run([sys.executable, str(ROOT/'scripts/build-audio.py'), '--cc',
                    os.environ.get('WINDOWS_CC', 'gcc' if os.name == 'nt' else 'x86_64-w64-mingw32-gcc'),
                    '--output', str(staging/'portable/caption_audio.dll')], check=True)
    shutil.copy2(ROOT/'native/CaptionAudio/vendor/COPYING', staging/'portable/SpeexDSP-LICENSE.txt')
    data = staging / 'portable/data/downloads'; data.mkdir(parents=True)
    for backend in ('cpu','vulkan'):
        key = 'windows-x86_64-' + backend
        name = f'nemo-speech-{NEMO_VERSION}-{key}.zip'
        download(f'https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{NEMO_VERSION}/{name}', downloads / name, NEMO_HASHES[key])
        shutil.copy2(downloads / name, data / name)
        if backend == 'cpu':
            # PyTorch must also load on machines without a system-wide VC++ redistributable.
            # Reuse the verified SDK redistributables next to embedded python.exe.
            import fnmatch
            with zipfile.ZipFile(downloads / name) as runtime_zip:
                for member in runtime_zip.namelist():
                    filename = Path(member).name
                    if any(fnmatch.fnmatch(filename.lower(), pattern) for pattern in ('msvcp140*.dll', 'vcruntime140*.dll', 'concrt140.dll', 'vcomp140.dll')):
                        (staging / 'python' / filename).write_bytes(runtime_zip.read(member))
        suffix, digest = LLAMA_ASSETS[key]; name = f'llama-{LLAMA_TAG}-bin-{suffix}'
        download(f'https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}', downloads / name, digest)
        shutil.copy2(downloads / name, data / name)
    if offline_models:
        shutil.copytree(ROOT / 'dist/portable-data/models', staging / 'portable/data/models')
    # Setup will rebuild absolute configuration paths on the recipient's machine.
    launcher = [
        '@echo off', 'cd /d "%~dp0"',
        'set "CAPTIONFLOW_DATA=%~dp0portable\\data"',
        'if exist "%LOCALAPPDATA%\\CaptionFlow\\network-consent.json" set "CAPTIONFLOW_DATA=%LOCALAPPDATA%\\CaptionFlow"',
        'if not exist "%CAPTIONFLOW_DATA%" mkdir "%CAPTIONFLOW_DATA%"',
        '"python\\python.exe" -u "portable\\launch.py" --data "%CAPTIONFLOW_DATA%"',
        'if errorlevel 1 (echo CaptionFlow could not start. See "%CAPTIONFLOW_DATA%\\launcher.log" & pause)',
    ]
    (staging / 'Start CaptionFlow.cmd').write_bytes(('\r\n'.join(launcher)+'\r\n').encode('utf-8'))
    (staging / 'README.txt').write_text('CaptionFlow Windows x64 preview\n\nExtract the entire ZIP before opening Start CaptionFlow.cmd. No Python or CUDA installation is required. A current Vulkan-capable graphics driver is required for GPU mode. Choose CPU if Vulkan is unavailable.\n\nFirst launch asks whether to participate online. No means no internet access: supply the pinned models offline, then click Download / verify models to configure cached files. Yes permits downloads, diagnostic metrics and signed update checks. No audio or transcript uploads.\n\nClick Download / verify models, then Start listening. Choose Microphone + call audio for a single mixed transcription stream. Speaker echo cancellation is enabled; headphones are recommended for the first test. Windows hardware capture and GPU performance still need validation on your machine.\n\nClick Start / resume benchmark. Choose Until I stop, Quick (5 minutes), 30 minutes or Overnight (8 hours). Setup and CPU fallback are automatic. Stop saves partial results; matching runs resume. Failed profiles are skipped. Keep the laptop plugged in with its lid open. Export diagnostics / results creates a ZIP for asynchronous troubleshooting. Keep portable/data when moving to a new release.\n\nInstaller startup and automated audio tests run on Windows CI. Physical device and driver behavior require testing. Third-party licenses are included in the embedded Python, wheels and runtime archives.\n')
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
