#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIGURATION="${CONFIGURATION:-release}"
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in arm64) RUST_ARCH=aarch64 ;; x86_64) RUST_ARCH=x86_64 ;; *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;; esac
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Library/Developer/CommandLineTools}"
export MACOSX_DEPLOYMENT_TARGET=14.0
export RUSTUP_TOOLCHAIN=1.98.0
export PATH="$HOME/.cargo/bin:$PATH"
CARGO="${CARGO:-$HOME/.cargo/bin/cargo}"
SWIFTC="${SWIFTC:-/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc}"
PYTHON="${PYTHON:-python3}"
OUTPUT_DIR="${OUTPUT_DIR:-$PWD/dist}"
TARGET="$RUST_ARCH-apple-darwin"
CARGO_ARGS=(build --locked --manifest-path gpui/Cargo.toml --target "$TARGET")
PROFILE=debug
if [ "$CONFIGURATION" = release ]; then CARGO_ARGS+=(--release); PROFILE=release; fi
"$CARGO" "${CARGO_ARGS[@]}"
APP="$OUTPUT_DIR/CaptionFlow.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources/worker"
cp "gpui/target/$TARGET/$PROFILE/CaptionFlowGPUI" "$APP/Contents/MacOS/CaptionFlowGPUI"
SDK="$(xcrun --show-sdk-path)"
NATIVE_BUILD="$PWD/.build/gpui-native-$ARCH"
mkdir -p "$NATIVE_BUILD"
cat > "$NATIVE_BUILD/module.modulemap" <<MAP
module CaptionAudio { header "$PWD/native/CaptionAudio/include/CaptionAudio.h" export * }
MAP
/usr/bin/clang -arch "$ARCH" -isysroot "$SDK" -O2 -std=c99 -dynamiclib -DHAVE_CONFIG_H \
  -Inative/CaptionAudio/include -Inative/CaptionAudio/vendor \
  native/CaptionAudio/audio.c native/CaptionAudio/vendor/*.c -lm \
  -install_name @rpath/libCaptionAudio.dylib -o "$APP/Contents/Frameworks/libCaptionAudio.dylib"
"$SWIFTC" -O -swift-version 5 -parse-as-library -emit-library -module-name CaptionFlowEngine \
  -target "$ARCH-apple-macosx14.0" -sdk "$SDK" -I "$NATIVE_BUILD" -L "$APP/Contents/Frameworks" -lCaptionAudio -Xlinker -rpath -Xlinker @loader_path \
  Sources/CaptionFlow/{AppState,AudioCapture,CommunitySupport,Preferences,ProgressiveCaption,WorkerBridge,ReleaseInfo,CaptionRetentionBuffer,LiveCaptionDocument,TranscriptScroll}.swift \
  gpui/native/*.swift -o "$APP/Contents/Frameworks/libCaptionFlowEngine.dylib"
rm -rf "$APP/Contents/Resources/worker"
mkdir -p "$APP/Contents/Resources/worker"
cp Sources/CaptionFlow/Resources/worker/*.{py,txt,json} "$APP/Contents/Resources/worker/"
cp "${APP_ICON:-packaging/assets/CaptionFlow.icns}" "$APP/Contents/Resources/CaptionFlow.icns"
"$PYTHON" - "$APP/Contents/Info.plist" <<'PY'
import json, plistlib, sys
from pathlib import Path
release = json.loads(Path('Sources/CaptionFlow/Resources/worker/release.json').read_text())
info = dict(CFBundleExecutable='CaptionFlowGPUI', CFBundleIdentifier='dev.captionflow.gpui',
    CFBundleName='CaptionFlow', CFBundleDisplayName='CaptionFlow', CFBundlePackageType='APPL',
    CFBundleShortVersionString=release['version'], CFBundleVersion=str(release['sequence']),
    CFBundleIconFile='CaptionFlow.icns', LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True,
    NSPrincipalClass='NSApplication',
    NSMicrophoneUsageDescription='CaptionFlow listens to your microphone to create private, on-device subtitles.',
    NSScreenCaptureUsageDescription='CaptionFlow captures Mac audio to transcribe the sound playing on your computer.')
Path(sys.argv[1]).write_bytes(plistlib.dumps(info))
PY
if [ -n "${BUNDLED_PYTHON:-}" ]; then
    rm -rf "$APP/Contents/Resources/python"
    ditto "$BUNDLED_PYTHON" "$APP/Contents/Resources/python"
    if [ "$ARCH" = arm64 ]; then /usr/libexec/PlistBuddy -c 'Set :LSMinimumSystemVersion 26.0' "$APP/Contents/Info.plist"; fi
fi
"$PYTHON" scripts/sign-app.py "$APP/Contents/Frameworks/libCaptionAudio.dylib"
"$PYTHON" scripts/sign-app.py "$APP/Contents/Frameworks/libCaptionFlowEngine.dylib"
"$PYTHON" scripts/sign-app.py "$APP"
codesign --verify --deep --strict "$APP"
lipo "$APP/Contents/MacOS/CaptionFlowGPUI" -verify_arch "$ARCH"
printf '%s\n' "$APP"
