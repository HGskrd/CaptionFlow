#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XCODE_DEVELOPER="${XCODE_DEVELOPER:-/Applications/Xcode.app/Contents/Developer}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Library/Developer/CommandLineTools}"
export SWIFT_EXEC="${SWIFT_EXEC:-$XCODE_DEVELOPER/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc}"
FRAMEWORKS="$XCODE_DEVELOPER/Platforms/MacOSX.platform/Developer/Library/Frameworks"
LIBRARIES="$XCODE_DEVELOPER/Platforms/MacOSX.platform/Developer/usr/lib"
SWIFT="$DEVELOPER_DIR/usr/bin/swift"
cd "$ROOT_DIR"
"$SWIFT" build --build-tests --disable-sandbox \
  -Xswiftc -I -Xswiftc "$LIBRARIES" -Xswiftc -F -Xswiftc "$FRAMEWORKS" \
  -Xlinker -L -Xlinker "$LIBRARIES" \
  -Xlinker -rpath -Xlinker "$FRAMEWORKS" -Xlinker -rpath -Xlinker "$LIBRARIES"
BIN_DIR="$("$SWIFT" build --show-bin-path)"
exec "$XCODE_DEVELOPER/Platforms/MacOSX.platform/Developer/Library/Xcode/Agents/xctest" "$BIN_DIR/CaptionFlowPackageTests.xctest"
