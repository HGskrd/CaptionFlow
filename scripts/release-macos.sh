#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE_DIR="${RELEASE_DIR:-$PWD/dist/macos-release}"
mkdir -p "$RELEASE_DIR"
for ARCH in arm64 x86_64; do
    ARCH="$ARCH" OUTPUT_DIR="$RELEASE_DIR/$ARCH" ./scripts/build-app.sh
    APP="$RELEASE_DIR/$ARCH/CaptionFlow.app"
    VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")
    ditto -c -k --sequesterRsrc --keepParent "$APP" "$RELEASE_DIR/CaptionFlow-macOS-$ARCH-$VERSION.zip"
done
cd "$RELEASE_DIR"
shasum -a 256 "CaptionFlow-macOS-arm64-$VERSION.zip" "CaptionFlow-macOS-x86_64-$VERSION.zip" > SHA256SUMS-macOS.txt
cat SHA256SUMS-macOS.txt
