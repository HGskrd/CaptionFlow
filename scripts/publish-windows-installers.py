"""Add verified online/all-in-one variants to the existing matching release."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def gh(*args):
    return subprocess.check_output(['gh',*args],cwd=ROOT,text=True)


def publish(directory):
    directory=Path(directory).resolve()
    manifest=json.loads((directory/'windows-installers.json').read_text())
    version=json.loads((ROOT/'Sources/CaptionFlow/Resources/worker/release.json').read_text())['version']
    assert manifest['version']==version
    report=json.loads((directory/'installer-test-report.json').read_text())
    assert all(report[k] for k in ('same_installed_files','normal_preview','all_in_one_preview',
                                  'uninstallation_verified','user_data_preserved','tampered_download_rejected','all_in_one_code_verified'))
    tag='v'+version
    release=json.loads(gh('release','view',tag,'--json','assets,isDraft'))
    assert not release['isDraft']
    assets={a['name']:a for a in release['assets']}
    complete=manifest['all_in_one']
    assert assets[complete['name']]['digest']=='sha256:'+complete['sha256']
    normal=directory/'CaptionFlow-Setup.exe'
    normal_sha=hashlib.file_digest(normal.open('rb'),'sha256').hexdigest()
    # Refuse to replace an unrelated concurrently published installer.
    assert assets['CaptionFlow-Setup.exe']['digest'] in (manifest['normal']['replaces_digest'],'sha256:'+normal_sha)
    gh('release','download',tag,'--pattern','SHA256SUMS.txt','--dir',str(directory))
    checksum_path=directory/'SHA256SUMS.txt'
    checksums={line.split(None,1)[1]:line.split()[0] for line in checksum_path.read_text().splitlines()}
    checksums[complete['name']]=complete['sha256']
    checksums[normal.name]=normal_sha
    checksum_path.write_text(''.join(f'{digest}  {name}\n' for name,digest in sorted(checksums.items())))
    gh('release','upload',tag,str(normal),'--clobber')
    gh('release','upload',tag,str(checksum_path),'--clobber')
    notes=ROOT/f'docs/releases/v{version}.md'
    assert len(notes.read_text().split())<=100
    gh('release','edit',tag,'--notes-file',str(notes))
    assets={a['name']:a for a in json.loads(gh('release','view',tag,'--json','assets'))['assets']}
    for name,digest in checksums.items():
        assert assets[name]['digest']=='sha256:'+digest,name
    print(json.dumps({'release':tag,'normal_bytes':normal.stat().st_size,'all_in_one_bytes':complete['size'],
                      'normal_sha256':normal_sha,'all_in_one_sha256':complete['sha256'],'checksums_verified':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    publish(parser.parse_args().directory)
