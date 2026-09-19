#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
swift build
APP="$PWD/dist/CaptionFlow.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
BIN="$(swift build --show-bin-path)"
cp "$BIN/CaptionFlow" "$APP/Contents/MacOS/CaptionFlow"
# Put runtime resources in the standard signed bundle resource directory.
rm -rf "$APP/CaptionFlow_CaptionFlow.bundle"
rm -rf "$APP/Contents/Resources/worker"
mkdir -p "$APP/Contents/Resources/worker"
cp Sources/CaptionFlow/Resources/worker/*.py Sources/CaptionFlow/Resources/worker/requirements.txt "$APP/Contents/Resources/worker/"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>CaptionFlow</string>
<key>CFBundleIdentifier</key><string>dev.captionflow.app</string>
<key>CFBundleName</key><string>CaptionFlow</string>
<key>CFBundleDisplayName</key><string>CaptionFlow</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSMicrophoneUsageDescription</key><string>CaptionFlow listens to your microphone to create private, on-device subtitles.</string>
<key>NSScreenCaptureUsageDescription</key><string>CaptionFlow captures Mac audio to transcribe the sound playing on your computer.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --deep --sign - "$APP"
printf '%s\n' "$APP"
