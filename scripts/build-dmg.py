#!/usr/bin/env python3
"""Package a signed app into a drag-to-Applications disk image."""
import argparse
from pathlib import Path
import dmgbuild

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--app', type=Path, required=True)
parser.add_argument('--arch', choices=['arm64', 'x86_64'], required=True)
parser.add_argument('--art', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
label = 'Apple Silicon' if args.arch == 'arm64' else 'Intel'
dmgbuild.build_dmg(str(args.output.resolve()), f'CaptionFlow {label}', settings={
    'format': 'UDZO', 'compression_level': 6, 'filesystem': 'HFS+',
    'files': [str(args.app.resolve())], 'symlinks': {'Applications': '/Applications'},
    'background': str((args.art/f'installer-{args.arch}.png').resolve()),
    'badge_icon': str((args.art/'CaptionFlow.icns').resolve()),
    'window_rect': ((160, 120), (600, 380)), 'icon_size': 112, 'text_size': 14,
    'icon_locations': {'CaptionFlow.app': (160, 192), 'Applications': (440, 192)},
    'default_view': 'icon-view',
    'show_status_bar': False, 'show_tab_view': False, 'show_toolbar': False,
    'show_pathbar': False, 'show_sidebar': False,
})
