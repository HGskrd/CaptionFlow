#!/usr/bin/env python3
"""Build a relocatable, architecture-specific Python runtime for the Mac app."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--arch', required=True, choices=['arm64', 'x86_64'])
parser.add_argument('--source', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
requirements = root / 'Sources/CaptionFlow/Resources/worker/requirements.txt'
constraints = root / f'scripts/macos-requirements-{args.arch}.txt'
stamp = {'python': '3.11.12', 'architecture': args.arch,
         'requirements_sha256': hashlib.sha256(requirements.read_bytes() + constraints.read_bytes()).hexdigest()}
output = args.output.resolve()
output.parent.mkdir(parents=True, exist_ok=True)
lock = output.with_suffix('.lock').open('w')
fcntl.flock(lock, fcntl.LOCK_EX)
marker = output / '.captionflow-runtime.json'
if marker.exists() and json.loads(marker.read_text()) == stamp:
    print(output)
    raise SystemExit(0)
if args.source:
    source = args.source.resolve()
else:
    downloads = root / '.build/python-downloads'
    architecture = 'aarch64' if args.arch == 'arm64' else args.arch
    identity = f'cpython-3.11.12-macos-{architecture}-none'
    subprocess.run(['uv', 'python', 'install', identity, '--install-dir', str(downloads)], check=True)
    source = downloads / identity
if output.exists():
    shutil.rmtree(output)
shutil.copytree(source, output, symlinks=True)
# This is our private redistribution copy, not the uv-managed installation.
for external_marker in output.glob('lib/python*/EXTERNALLY-MANAGED'):
    external_marker.unlink()
python = output / 'bin/python3'
environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
    environment.pop(key, None)
subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                '--no-compile', '-r', str(requirements), '-c', str(constraints)], check=True, env=environment)
modules = 'numpy, huggingface_hub, certifi'
if args.arch == 'arm64':
    modules += ', mlx.core, mlx_whisper, mlx_lm, mlx_audio'
subprocess.run([str(python), '-I', '-c', f'import platform; assert platform.machine() == {args.arch!r}; import {modules}'],
               check=True, env=environment)
for cache in output.rglob('__pycache__'):
    shutil.rmtree(cache)
marker.write_text(json.dumps(stamp, indent=2) + '\n')
print(output)
