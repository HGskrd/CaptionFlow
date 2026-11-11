"""Windowed launcher with persistent diagnostics and a per-user data directory."""
import argparse
from pathlib import Path
import shutil
import sys
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    log = data / 'launcher.log'
    if log.exists() and log.stat().st_size > 1024 * 1024:
        log.replace(data / 'launcher.previous.log')
    with log.open('a', encoding='utf-8', buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        try:
            # Seed shipped runtimes locally before asking about network consent.
            downloads = data / 'downloads'
            downloads.mkdir(exist_ok=True)
            for archive in (Path(__file__).parent / 'data/downloads').glob('*.zip'):
                destination = downloads / archive.name
                if not destination.exists():
                    shutil.copy2(archive, destination)
            from app import main as run
            sys.argv = [sys.argv[0], '--data', str(data)] + (['--smoke-test'] if args.smoke_test else [])
            run()
        except SystemExit:
            raise
        except BaseException:
            traceback.print_exc()
            if sys.platform == 'win32' and not args.smoke_test:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, f'CaptionFlow could not start. Details: {log}', 'CaptionFlow', 0x10)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
