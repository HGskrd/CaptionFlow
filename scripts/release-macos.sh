#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE_DIR="${RELEASE_DIR:-$PWD/dist/macos-release}"
PACKAGING_PYTHON="${PACKAGING_PYTHON:-python3}"
ART="$PWD/.build/macos-art"
mkdir -p "$RELEASE_DIR"
"$PACKAGING_PYTHON" scripts/macos-art.py "$ART"
for ARCH in arm64 x86_64; do
    RUNTIME="$PWD/.build/bundled-python/$ARCH"
    "$PACKAGING_PYTHON" scripts/bundle-python.py --arch "$ARCH" --output "$RUNTIME"
    ARCH="$ARCH" BUNDLED_PYTHON="$RUNTIME" APP_ICON="$ART/CaptionFlow.icns" OUTPUT_DIR="$RELEASE_DIR/$ARCH" ./scripts/build-app.sh
    APP="$RELEASE_DIR/$ARCH/CaptionFlow.app"
    VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")
    "$PACKAGING_PYTHON" scripts/build-dmg.py --app "$APP" --arch "$ARCH" --art "$ART" --output "$RELEASE_DIR/CaptionFlow-macOS-$ARCH-$VERSION.dmg"
done
cd "$RELEASE_DIR"
shasum -a 256 "CaptionFlow-macOS-arm64-$VERSION.dmg" "CaptionFlow-macOS-x86_64-$VERSION.dmg" > SHA256SUMS-macOS.txt
cat SHA256SUMS-macOS.txt
