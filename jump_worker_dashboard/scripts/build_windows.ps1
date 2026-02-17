$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Path $PSScriptRoot -Parent
Set-Location $ROOT

$PY = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$VENV_DIR = if ($env:VENV_DIR) { $env:VENV_DIR } else { ".venv-build" }
$APP_NAME = "jump-worker-dashboard"

if (!(Test-Path $VENV_DIR)) {
  & $PY -m venv $VENV_DIR
}

& "$VENV_DIR\Scripts\python.exe" -m pip install --upgrade pip
& "$VENV_DIR\Scripts\python.exe" -m pip install `
  "customtkinter>=5.2.0" `
  "requests>=2.31.0" `
  "selenium>=4.20.0" `
  "beautifulsoup4>=4.12.0" `
  "brotli>=1.1.0" `
  "pyinstaller>=6.10.0"

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\$APP_NAME") { Remove-Item -Recurse -Force "dist\$APP_NAME" }
if (Test-Path "dist\$APP_NAME.exe") { Remove-Item -Force "dist\$APP_NAME.exe" }

& "$VENV_DIR\Scripts\pyinstaller.exe" `
  --noconfirm `
  --clean `
  --windowed `
  --name "$APP_NAME" `
  --add-data "assets;assets" `
  main.py

Write-Host "빌드 완료: dist\\$APP_NAME"
