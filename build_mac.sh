#!/usr/bin/env bash
# Build "Test Tracker.app" into dist/.  Pass --install to also copy it to /Applications.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyinstaller

.venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
    --windowed \
    --name "Test Tracker" \
    --icon assets/icon.icns \
    --osx-bundle-identifier com.vitalbio.test-tracker \
    test_tracker.py

# Stamp the version from test_tracker.py into the app (shown in Finder → Get Info),
# then re-sign, since editing Info.plist invalidates the signature.
VERSION="$(sed -n 's/^APP_VERSION *= *"\([^"]*\)".*/\1/p' test_tracker.py)"
PLIST="dist/Test Tracker.app/Contents/Info.plist"
for key in CFBundleShortVersionString CFBundleVersion; do
    /usr/libexec/PlistBuddy -c "Set :$key $VERSION" "$PLIST" 2>/dev/null ||
        /usr/libexec/PlistBuddy -c "Add :$key string $VERSION" "$PLIST"
done
codesign --force --deep --sign - "dist/Test Tracker.app" 2>/dev/null

echo "✓ Built dist/Test Tracker.app (version $VERSION)"

if [ "${1:-}" = "--install" ]; then
    rm -rf "/Applications/Test Tracker.app"
    cp -R "dist/Test Tracker.app" /Applications/
    echo "✓ Installed to /Applications — open it from Launchpad or Spotlight"
fi
