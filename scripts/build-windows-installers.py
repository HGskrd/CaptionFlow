"""Wrap the complete Windows installer with a small checksum-pinned online installer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(all_in_one, output, test_url=None):
    version = json.loads((ROOT/'Sources/CaptionFlow/Resources/worker/release.json').read_text())['version']
    source = Path(all_in_one).resolve()
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    size = source.stat().st_size
    with source.open('rb') as stream:
        if stream.read(2) != b'MZ': raise ValueError('Expected a Windows installer executable')
    complete = output/'CaptionFlow-Setup-All-in-One.exe'
    if source != complete: shutil.copy2(source, complete)
    url = f'https://github.com/HGskrd/CaptionFlow/releases/download/v{version}/{complete.name}'
    def compile_installer(name, location, loopback=False):
        if loopback and not location.startswith('http://127.0.0.1:'):
            raise ValueError('Test installer URL must be explicit loopback HTTP')
        prefix = '/' if os.name == 'nt' else '-'
        compiler = shutil.which('makensis')
        if not compiler and os.name == 'nt':
            compiler = str(Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))/'NSIS/makensis.exe')
        command = [compiler or 'makensis', prefix+'V2', f'{prefix}DVERSION={version}', f'{prefix}DOUTPUT={output/name}',
                   f'{prefix}DPAYLOAD_URL={location}', f'{prefix}DPAYLOAD_SHA256={digest}', f'{prefix}DPAYLOAD_BYTES={size}']
        if loopback: command.append(prefix+'DLOOPBACK_TEST')
        subprocess.run([*command, str(ROOT/'packaging/windows-online.nsi')], check=True, cwd=ROOT)
    compile_installer('CaptionFlow-Setup.exe', url)
    if test_url: compile_installer('CaptionFlow-Setup-Loopback-Test.exe', test_url, True)
    manifest={'version':version, 'all_in_one':{'name':complete.name,'url':url,'sha256':digest,'size':size},
              'normal':{'name':'CaptionFlow-Setup.exe','downloads_code_during_install':True},'models_bundled':False}
    (output/'windows-installers.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-in-one',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/windows-installers')
    parser.add_argument('--test-url',help='Build an additional, non-distributable loopback test installer')
    args=parser.parse_args();build(args.all_in_one,args.output,args.test_url)
