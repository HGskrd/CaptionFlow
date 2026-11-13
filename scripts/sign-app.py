#!/usr/bin/env python3
"""Sign with a persistent local development certificate in the login Keychain.

Run with --create-identity once, with user approval. No trusted root is added,
no Keychain password is stored, and no macOS privacy permissions are changed.
"""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

NAME = 'CaptionFlow Local Development'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sign_nested_runtime(app, identity, keychain=None):
    runtime = app / 'Contents/Resources/python'
    if not runtime.exists():
        return
    magic = {bytes.fromhex(value) for value in ('feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
    for path in sorted(runtime.rglob('*'), key=lambda value: len(value.parts), reverse=True):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in magic:
                continue
        command = ['/usr/bin/codesign', '--force', '--sign', identity, '--timestamp=none']
        if keychain:
            command += ['--keychain', str(keychain)]
        run(*command, str(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    app = Path(sys.argv[1]).resolve()
    supplied = os.environ.get('CAPTIONFLOW_SIGNING_IDENTITY')
    if supplied:
        sign_nested_runtime(app, supplied)
        run('/usr/bin/codesign', '--force', '--sign', supplied, str(app))
        return
    keychain = Path.home() / 'Library/Keychains/login.keychain-db'
    found = subprocess.run(['/usr/bin/security', 'find-certificate', '-c', NAME, '-p', str(keychain)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='captionflow-signing-') as temporary:
        directory = Path(temporary)
        certificate = directory / 'certificate.pem'
        if found.returncode == 0:
            certificate.write_bytes(found.stdout)
        elif '--create-identity' in sys.argv[2:]:
            private_key = directory / 'private.key'
            archive = directory / 'identity.p12'
            password = secrets.token_urlsafe(36)
            environment = dict(os.environ, CAPTIONFLOW_CERT_PASSWORD=password)
            previous_umask = os.umask(0o077)
            try:
                run('/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                    '-subj', '/CN=' + NAME + '/', '-addext', 'keyUsage=critical,digitalSignature',
                    '-addext', 'extendedKeyUsage=critical,codeSigning', '-addext', 'basicConstraints=critical,CA:FALSE',
                    '-keyout', str(private_key), '-out', str(certificate), stderr=subprocess.DEVNULL)
                run('/usr/bin/openssl', 'pkcs12', '-export', '-inkey', str(private_key), '-in', str(certificate),
                    '-out', str(archive), '-name', NAME, '-passout', 'env:CAPTIONFLOW_CERT_PASSWORD', env=environment)
                run('/usr/bin/security', 'import', str(archive), '-k', str(keychain), '-P', password,
                    '-x', '-T', '/usr/bin/codesign', stdout=subprocess.DEVNULL)
            finally:
                os.umask(previous_umask)
        else:
            sys.exit('No stable signing identity. With user approval, run scripts/sign-app.py APP --create-identity once, or set CAPTIONFLOW_SIGNING_IDENTITY. Ad-hoc signing invalidates privacy grants after each rebuild.')
        der = subprocess.check_output(['/usr/bin/openssl', 'x509', '-in', str(certificate), '-outform', 'DER'])
        identity = hashlib.sha1(der).hexdigest()
        sign_nested_runtime(app, identity, keychain)
        run('/usr/bin/codesign', '--force', '--sign', identity, '--keychain', str(keychain), '--timestamp=none', str(app))
        run('/usr/bin/codesign', '--verify', '--strict', str(app))
        print('Signed with the persistent CaptionFlow development identity.')


if __name__ == '__main__':
    main()
