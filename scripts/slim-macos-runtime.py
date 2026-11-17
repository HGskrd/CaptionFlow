"""Specialize a disposable bundled Python copy for CaptionFlow's inference API.

Whisper's word alignment and PyTorch checkpoint-conversion APIs are not used by
CaptionFlow. Preserve all speech backends, tokenizers, certificates and licenses.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil

WHISPER_TRANSCRIBE_SHA256 = '21f015e0d56c5e6194d07d86d772730940fff52d8f06d55838ae92ebccc24fbb'
UNUSED = {'torch', 'numba', 'llvmlite', 'sympy', 'mpmath', 'networkx'}


def size(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())


def lazy_whisper_timing(site):
    path = site / 'mlx_whisper/transcribe.py'
    source = path.read_text()
    if hashlib.sha256(path.read_bytes()).hexdigest() != WHISPER_TRANSCRIBE_SHA256:
        raise ValueError('mlx-whisper changed: review the lazy timing patch before packaging')
    original = 'from .timing import add_word_timestamps\n'
    call = '                if word_timestamps:\n                    add_word_timestamps('
    if source.count(original) != 1 or source.count(call) != 1:
        raise ValueError('Unexpected Whisper word-timestamp import/call structure')
    source = source.replace(original, '')
    source = source.replace(call, '                if word_timestamps:\n                    from .timing import add_word_timestamps\n\n                    add_word_timestamps(')
    compile(source, str(path), 'exec')
    path.write_text(source)


def slim(runtime, arch):
    runtime = Path(runtime).resolve()
    site = runtime / 'lib/python3.11/site-packages'
    if not (runtime / 'bin/python3').exists() or not site.is_dir():
        raise ValueError('Expected a disposable bundled CPython 3.11 runtime')
    before = size(runtime)
    removed = {}
    if arch == 'arm64':
        # Fail before removing any files if the pinned upstream source changed.
        lazy_whisper_timing(site)
        for distribution in importlib.metadata.distributions(path=[str(site)]):
            name = distribution.metadata['Name'].lower().replace('_', '-')
            if name not in UNUSED:
                continue
            records = list(distribution.files or [])
            if not records:
                raise ValueError('Cannot safely remove distribution without RECORD: ' + name)
            paths = [Path(distribution.locate_file(entry)) for entry in records]
            if any(not path.resolve().is_relative_to(runtime) for path in paths):
                raise ValueError('Distribution RECORD escapes private runtime: ' + name)
            removed[name] = distribution.version
            for path in paths:
                if path.is_file() or path.is_symlink():
                    path.unlink()
    # Only packaging/development data: runtime helpers such as numpy.testing,
    # tokenizer vocabularies, mel filters and package metadata remain intact.
    for directory in sorted(runtime.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not directory.is_symlink() and directory.name in {'tests', '__pycache__'}:
            shutil.rmtree(directory)
    for directory in [runtime / 'include', runtime / 'lib/python3.11/test', site / 'numpy/_core/include']:
        if directory.is_dir():
            shutil.rmtree(directory)
    for path in runtime.glob('lib/*.a'):
        path.unlink()
    for folder, _, _ in os.walk(runtime, topdown=False):
        directory = Path(folder)
        if directory != runtime and not any(directory.iterdir()):
            directory.rmdir()
    report = {'profile': 1, 'architecture': arch, 'before_bytes': before,
              'after_bytes': size(runtime), 'removed_distributions': removed,
              'whisper_word_timestamps': False if arch == 'arm64' else None}
    (runtime / '.captionflow-runtime-profile.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--arch', required=True, choices=['arm64', 'x86_64'])
    args = parser.parse_args()
    print(json.dumps(slim(args.runtime, args.arch)))
