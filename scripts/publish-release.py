#!/usr/bin/env python3
"""Publish one complete platform release after packaging and verification.

Run without --publish for a read-only preflight. Installers must be built from
this checkout; attach the installers, Linux archive, Arch package, PKGBUILD and their checksum file.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REPO='HGskrd/CaptionFlow'


def validate(directory, version, notes):
    names=['CaptionFlow-Setup.exe','CaptionFlow-Setup-All-in-One.exe','CaptionFlow-macOS-arm64.dmg','CaptionFlow-macOS-x86_64.dmg','CaptionFlow-Linux-x86_64.tar.gz']
    arch_packages = list(directory.glob(f'captionflow-bin-{version}-*-x86_64.pkg.tar.zst'))
    if len(arch_packages) != 1:
        raise ValueError('A release requires exactly one matching Arch pacman package')
    names += ['PKGBUILD', arch_packages[0].name]
    if any(not (directory/name).is_file() or (directory/name).stat().st_size==0 for name in names):
        raise ValueError('A release requires all platform installers, the Linux archive, Arch package and PKGBUILD')
    if len(notes.split())>100 or len(notes)>1000:
        raise ValueError('Release notes must stay under 100 words and 1,000 characters')
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('Commit all release changes before publishing')
    lines=[]
    for name in names:
        with (directory/name).open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        lines.append(f'{digest}  {name}\n')
    checksums=directory/'SHA256SUMS.txt'; checksums.write_text(''.join(lines))
    return [directory/name for name in names]+[checksums]


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--notes',type=Path,required=True)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    version=json.loads((ROOT/'Sources/CaptionFlow/Resources/worker/release.json').read_text())['version']
    tag='v'+version
    assets=validate(args.directory.resolve(),version,args.notes.read_text())
    print(f'{tag}: '+', '.join(path.name for path in assets))
    if args.publish:
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        existing=subprocess.run(['gh','release','view',tag,'--repo',REPO,'--json','isDraft'],capture_output=True,text=True)
        if existing.returncode==0:
            raise SystemExit('Release already exists; edit its notes explicitly rather than replacing published downloads')
        subprocess.run(['gh','release','create',tag,'--repo',REPO,'--target',head,'--title',f'CaptionFlow {version}',
                        '--notes-file',str(args.notes.resolve()),'--draft',*[str(path) for path in assets]],check=True)
        # A failed upload leaves a draft. A partial release never becomes latest.
        release=json.loads(subprocess.check_output(['gh','release','view',tag,'--repo',REPO,'--json','assets'],text=True))
        if {item['name'] for item in release['assets']} != {path.name for path in assets}:
            raise SystemExit('Draft asset set is incomplete; publication stopped')
        subprocess.run(['gh','release','edit',tag,'--repo',REPO,'--draft=false','--latest'],check=True)
