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
  "2captcha-python>=1.2.0" `
  "pyinstaller>=6.10.0"

if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\$APP_NAME") { Remove-Item -Recurse -Force "dist\$APP_NAME" }
if (Test-Path "dist\$APP_NAME.exe") { Remove-Item -Force "dist\$APP_NAME.exe" }

# jump_site_modules: CI가 복사하거나, monorepo 루트에서 가져옴
if (!(Test-Path "jump_site_modules") -and (Test-Path "..\jump_site_modules")) {
  Copy-Item -Recurse "..\jump_site_modules" ".\jump_site_modules"
}

& "$VENV_DIR\Scripts\pyinstaller.exe" `
  --noconfirm `
  --clean `
  --windowed `
  --name "$APP_NAME" `
  --icon "assets\calendar.ico" `
  --add-data "assets;assets" `
  --add-data "jump_site_modules;jump_site_modules" `
  --hidden-import=jump_site_modules `
  --hidden-import=jump_site_modules.base `
  --hidden-import=jump_site_modules.exceptions `
  --hidden-import=jump_site_modules.gnuboard_base `
  --hidden-import=jump_site_modules.types `
  --collect-submodules=jump_site_modules `
  --collect-submodules=selenium `
  --collect-data=certifi `
  --copy-metadata=certifi `
  --copy-metadata=requests `
  --copy-metadata=urllib3 `
  --hidden-import=selenium.webdriver.chrome.webdriver `
  --hidden-import=selenium.webdriver.chrome.service `
  --hidden-import=selenium.webdriver.chrome.options `
  --hidden-import=selenium.webdriver.common.by `
  --hidden-import=selenium.webdriver.support.ui `
  --hidden-import=selenium.webdriver.support.expected_conditions `
  main.py

Write-Host "빌드 완료: dist\$APP_NAME"
