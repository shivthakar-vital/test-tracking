#!/bin/bash
# Installs or updates Test Tracker on this Mac. Run in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/shivthakar-vital/test-tracking/main/install.sh | bash
set -euo pipefail

APP="Test Tracker.app"
URL="${TRACKER_ZIP_URL:-https://github.com/shivthakar-vital/test-tracking/releases/latest/download/Test-Tracker-mac.zip}"
DEST="${TRACKER_INSTALL_DIR:-/Applications}"

if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]; then
    echo "Sorry, Test Tracker needs a Mac with Apple Silicon (M1 or newer)."
    exit 1
fi
if [ -d "$HOME/Applications/$APP" ] && [ ! -d "$DEST/$APP" ]; then
    DEST="$HOME/Applications"             # update it where it was installed before
fi
if [ ! -w "$DEST" ]; then                 # not an admin: install just for this user
    DEST="$HOME/Applications"
    mkdir -p "$DEST"
fi

echo "Downloading Test Tracker…"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fL --progress-bar "$URL" -o "$TMP/app.zip"
ditto -x -k "$TMP/app.zip" "$TMP"

pkill -f "$APP/Contents/MacOS" 2>/dev/null || true     # close it if it's running (update)
sleep 1
rm -rf "${DEST:?}/$APP"
mv "$TMP/$APP" "$DEST/"
xattr -dr com.apple.quarantine "$DEST/$APP" 2>/dev/null || true

echo "✓ Installed Test Tracker in $DEST. Opening it now…"
open "$DEST/$APP"
