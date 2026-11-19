#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="1.0.3"
APP="$ROOT/outputs/Mixxx Live Downloader.app"
STAGING="$ROOT/work/release-staging"
ZIP="$ROOT/outputs/mixxx-live-downloader-v${VERSION}-macos-x86_64.zip"

if [[ ! -d "$APP" ]]; then
  echo "App fehlt. Zuerst zsh ./build_app.sh ausführen."
  exit 1
fi

rm -rf "$STAGING"
mkdir -p "$STAGING"
ditto "$APP" "$STAGING/Mixxx Live Downloader.app"
cp "$ROOT/install.command" "$STAGING/Install.command"
cp "$ROOT/README_Installer.txt" "$STAGING/README.txt"
chmod +x "$STAGING/Install.command"

rm -f "$ZIP" "$ZIP.sha256"
ditto -c -k --sequesterRsrc --keepParent "$STAGING" "$ZIP"
shasum -a 256 "$ZIP" > "$ZIP.sha256"
echo "Release-Dateien:"
echo "$ZIP"
echo "$ZIP.sha256"
