#!/usr/bin/env python3
"""Create deterministic component archives and an Ed25519-signed update manifest."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import stat
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://h.gskrd.me/apps/captionflow/releases/'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package(root, target, output, key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    root = root.resolve()
    metadata = root / ('Sources/CaptionFlow/Resources/worker/release.json' if target.startswith('windows') else 'Contents/Resources/worker/release.json')
    release = json.loads(metadata.read_text())
    components = {}
    for path in sorted(root.rglob('*')):
        if path.is_dir() and not path.is_symlink(): continue
        name = path.relative_to(root).as_posix()
        if name.startswith(('python/', 'Contents/Resources/python/')): group = 'python'
        elif name.startswith('portable/data/downloads/'): group = 'runtimes'
        elif name.startswith(('Contents/Resources/worker/', 'Sources/', 'docs/', 'portable/')) and not name.endswith('.ico'): group = 'worker'
        else: group = 'app'
        link = path.readlink().as_posix() if path.is_symlink() else None
        data = link.encode() if link is not None else None
        entry = dict(path=name, sha256=hashlib.sha256(data).hexdigest() if data is not None else sha(path),
                     size=len(data) if data is not None else path.stat().st_size,
                     mode=stat.S_IMODE(path.lstat().st_mode) & 0o777)
        if link is not None: entry['symlink'] = link
        components.setdefault(group, []).append(entry)
    output.mkdir(parents=True, exist_ok=True)
    result = dict(schema=2, platform=target, **release, expires=int(time.time())+180*86400, components={})
    for name, files in components.items():
        temporary = output / f'{target}-{name}.zip'
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for entry in files:
                info = zipfile.ZipInfo(entry['path'], date_time=(1980,1,1,0,0,0))
                info.create_system = 3
                info.external_attr = ((stat.S_IFLNK if 'symlink' in entry else stat.S_IFREG) | entry['mode']) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                path = root / entry['path']
                if 'symlink' in entry: archive.writestr(info,entry['symlink'].encode())
                else:
                    with path.open('rb') as source, archive.open(info,'w',force_zip64=entry['size']>=2**31) as destination:
                        import shutil
                        shutil.copyfileobj(source,destination)
        digest = sha(temporary)
        filename = f'CaptionFlow-{release["version"]}-{target}-{name}-{digest[:16]}.zip'
        archive = output / filename
        temporary.replace(archive)
        result['components'][name] = dict(url=ORIGIN+filename, sha256=digest, size=archive.stat().st_size, files=files)
    payload = json.dumps(result,sort_keys=True,separators=(',',':')).encode()
    private = Ed25519PrivateKey.from_private_bytes(key.read_bytes())
    envelope = dict(payload=base64.b64encode(payload).decode(),signature=base64.b64encode(private.sign(payload)).decode())
    manifest = output / f'gpui-{target}.json'
    manifest.write_text(json.dumps(envelope,indent=2)+'\n')
    print(json.dumps(dict(manifest=str(manifest),version=release['version'],components={k:v['size'] for k,v in result['components'].items()})))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--platform',required=True,choices=['windows-x86_64','macos-aarch64','macos-x86_64'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--key',type=Path,default=Path.home()/'.config/captionflow/release-signing.key')
    args=parser.parse_args(); package(args.root,args.platform,args.output,args.key)
