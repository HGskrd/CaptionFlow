"""Sign an explicitly selected release. This does not upload or publish it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive',type=Path);parser.add_argument('--version',required=True)
parser.add_argument('--sequence',type=int,required=True);parser.add_argument('--platform',choices=['windows-x86_64','macos-aarch64'],required=True)
parser.add_argument('--key',type=Path,default=Path.home()/'.config/captionflow/release-signing.key')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if args.sequence<1:parser.error('Sequence must be positive and increase with each release')
digest=hashlib.sha256()
with args.archive.open('rb') as stream:
 for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
payload=json.dumps({'version':args.version,'sequence':args.sequence,'platform':args.platform,
 'url':'https://h.gskrd.me/apps/captionflow/releases/'+args.archive.name,
 'sha256':digest.hexdigest(),'size':args.archive.stat().st_size,'expires':int(time.time()+90*86400)},sort_keys=True,separators=(',',':')).encode()
key=Ed25519PrivateKey.from_private_bytes(args.key.read_bytes())
args.output.write_text(json.dumps({'payload':base64.b64encode(payload).decode(),'signature':base64.b64encode(key.sign(payload)).decode()},indent=2)+'\n')
print('Signed manifest:',args.output)
