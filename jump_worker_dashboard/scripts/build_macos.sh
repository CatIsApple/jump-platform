#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv-build}"
APP_NAME="jump-worker-dashboard"
DIST_DIR="$ROOT/dist"
BUILD_DIR="$ROOT/build"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PY" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install \
  "customtkinter>=5.2.0" \
  "requests>=2.31.0" \
  "selenium>=4.20.0" \
  "beautifulsoup4>=4.12.0" \
  "brotli>=1.1.0" \
  "pyinstaller>=6.10.0"

rm -rf "$BUILD_DIR" "$DIST_DIR/$APP_NAME" "$DIST_DIR/$APP_NAME.app"

# jump_site_modules: CI가 복사하거나, monorepo 루트에서 가져옴
if [[ ! -d "jump_site_modules" && -d "../jump_site_modules" ]]; then
  cp -r ../jump_site_modules ./jump_site_modules
fi

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "assets/calendar.ico" \
  --add-data "assets:assets" \
  --add-data "jump_site_modules:jump_site_modules" \
  --hidden-import=jump_site_modules \
  --hidden-import=jump_site_modules.base \
  --hidden-import=jump_site_modules.exceptions \
  --hidden-import=jump_site_modules.gnuboard_base \
  --hidden-import=jump_site_modules.types \
  --collect-submodules=jump_site_modules \
  main.py

ZIP_OUT="$DIST_DIR/${APP_NAME}-macos.zip"
rm -f "$ZIP_OUT"
if [[ -d "$DIST_DIR/$APP_NAME.app" ]]; then
  ditto -c -k --sequesterRsrc --keepParent "$DIST_DIR/$APP_NAME.app" "$ZIP_OUT"
else
  (cd "$DIST_DIR" && zip -r "$ZIP_OUT" "$APP_NAME")
fi

echo "빌드 완료:"
echo " - 앱: $DIST_DIR/$APP_NAME.app"
echo " - zip: $ZIP_OUT"
