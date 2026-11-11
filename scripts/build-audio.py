"""Build the small shared audio DSP library; no model, GPU or network required."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]

def build(output=None, cc=None):
    root = ROOT / 'native/CaptionAudio'
    suffix = '.dll' if os.name == 'nt' else '.dylib' if sys.platform == 'darwin' else '.so'
    output = Path(output or ROOT / ('portable/caption_audio' + suffix)).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    compiler = cc or os.environ.get('CC', 'cc')
    subprocess.run([compiler, '-O2', '-std=c99', '-shared', '-DHAVE_CONFIG_H',
                    '-I'+str(root/'include'), '-I'+str(root/'vendor'),
                    *(['-fPIC'] if not str(output).endswith('.dll') else ['-static-libgcc', '-Wl,--no-insert-timestamp']),
                    *(['-isysroot', os.environ['SDKROOT']] if os.environ.get('SDKROOT') and sys.platform == 'darwin' else []),
                    str(root/'audio.c'), *map(str, sorted((root/'vendor').glob('*.c'))),
                    '-lm', '-o', str(output)], check=True)
    return output

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output'); p.add_argument('--cc')
    a=p.parse_args(); print(build(a.output,a.cc))
