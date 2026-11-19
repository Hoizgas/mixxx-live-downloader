#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

mkdir -p assets outputs work
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python "$ROOT/make_icon.py" "$ROOT/assets"

pyinstaller \
  --noconfirm \
  --windowed \
  --name "Mixxx Live Downloader" \
  --icon "$ROOT/assets/MixxxDownloader.icns" \
  --osx-bundle-identifier "com.hoizgas.mixxxlivedownloader" \
  --distpath "$ROOT/outputs" \
  --workpath "$ROOT/work/build" \
  --specpath "$ROOT/work" \
  "$ROOT/app.py"

rm -rf "$ROOT/outputs/Mixxx Live Downloader"

echo "App erstellt: $ROOT/outputs/Mixxx Live Downloader.app"
