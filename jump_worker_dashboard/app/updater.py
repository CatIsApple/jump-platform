"""Auto-updater — 백엔드 R2 프록시 기반 안전 업데이트.

흐름:
  1. check_latest_version() → GET /v1/updates/latest
  2. compare_versions() → 현재 vs 최신
  3. download_update() → GET /v1/updates/download/:id (Bearer 인증)
  4. verify_sha256() → 무결성 검증
  5. install_and_restart() → OS별 분기
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests


@dataclass(frozen=True)
class UpdateInfo:
    id: int
    version: str
    platform: str
    filename: str
    size: int
    sha256: str
    notes: str
    released_at: int
    download_path: str

    @property
    def is_newer_than(self) -> Callable[[str], bool]:
        return lambda current: _is_newer(self.version, current)


# ── 버전 비교 ──

def _parse_version(v: str) -> tuple[int, ...]:
    """'0.8.0' 또는 'v0.8.0' → (0, 8, 0)."""
    s = (v or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in s.split("."):
        # '1a2' 같은 형태 방어 — 숫자만 추출
        digits = ""
        for c in chunk:
            if c.isdigit():
                digits += c
            else:
                break
        try:
            parts.append(int(digits) if digits else 0)
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def _is_newer(remote: str, current: str) -> bool:
    return _parse_version(remote) > _parse_version(current)


# ── 플랫폼 ──

def detect_platform() -> str:
    s = sys.platform
    if s.startswith("win"):
        return "windows"
    if s == "darwin":
        return "macos"
    return "windows"  # 기본값


# ── 백엔드 호출 ──

class UpdateError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def check_latest_version(base_url: str, token: str, *, timeout_s: float = 15.0) -> UpdateInfo | None:
    """최신 릴리즈 정보 조회. 없으면 None."""
    if not base_url or not token:
        raise UpdateError("base_url 또는 token 누락")
    p = detect_platform()
    url = base_url.rstrip("/") + f"/v1/updates/latest?platform={p}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-Update-Platform": p,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout_s)
    except requests.RequestException as exc:
        raise UpdateError(f"업데이트 확인 실패: {exc}") from exc

    if resp.status_code in (401, 403):
        raise UpdateError("인증 실패", status_code=resp.status_code)
    if resp.status_code >= 400:
        raise UpdateError(f"서버 오류 {resp.status_code}", status_code=resp.status_code)

    try:
        data = resp.json()
    except Exception:
        raise UpdateError("응답 파싱 실패")

    latest = data.get("latest")
    if not isinstance(latest, dict):
        return None

    return UpdateInfo(
        id=int(latest.get("id", 0)),
        version=str(latest.get("version", "")).strip(),
        platform=str(latest.get("platform", "")).strip(),
        filename=str(latest.get("filename", "")).strip(),
        size=int(latest.get("size", 0)),
        sha256=str(latest.get("sha256", "")).strip().lower(),
        notes=str(latest.get("notes", "")),
        released_at=int(latest.get("released_at", 0)),
        download_path=str(latest.get("download_path", "")).strip(),
    )


def download_update(
    base_url: str,
    token: str,
    info: UpdateInfo,
    dest_path: Path,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    chunk_size: int = 64 * 1024,
    timeout_s: float = 600.0,
) -> Path:
    """바이너리 다운로드 + sha256 검증."""
    url = base_url.rstrip("/") + info.download_path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream",
    }

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout_s) as resp:
            if resp.status_code in (401, 403):
                raise UpdateError("다운로드 인증 실패", status_code=resp.status_code)
            if resp.status_code >= 400:
                raise UpdateError(f"다운로드 오류 {resp.status_code}", status_code=resp.status_code)

            total = info.size or int(resp.headers.get("Content-Length") or 0)
            received = 0
            sha = hashlib.sha256()

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    sha.update(chunk)
                    received += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(received, total)
                        except Exception:
                            pass
    except requests.RequestException as exc:
        raise UpdateError(f"다운로드 실패: {exc}") from exc

    actual_hash = sha.hexdigest().lower()
    if info.sha256 and actual_hash != info.sha256:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise UpdateError(
            f"무결성 검증 실패: sha256 불일치\n"
            f"기대: {info.sha256}\n실제: {actual_hash}"
        )

    if dest_path.exists():
        try:
            dest_path.unlink()
        except Exception:
            pass
    tmp_path.replace(dest_path)
    return dest_path


# ── 설치 & 재시작 ──

def install_and_restart_windows(setup_exe_path: Path) -> None:
    """Windows: CMD 배치 파일 launcher로 부모 종료 대기 → UAC 상승 → 설치 → 재시작.

    설계 원칙 (과거 PowerShell 방식의 문제 수정):
      1. Nuitka onefile의 Job Object에서 탈출 (CREATE_BREAKAWAY_FROM_JOB)
         → 부모 종료 시 헬퍼가 함께 killed 되지 않음
      2. cmd.exe (PowerShell 아님) — AV/ExecutionPolicy 방해 최소
      3. 설치는 `start "" /wait` 로 실행 — 내부 UAC 상승이 정상 동작
      4. 모든 단계를 log_dir 에 직접 기록 (Start-Transcript 같은 추가 메커니즘 불필요)
      5. CREATE_NO_WINDOW / DETACHED_PROCESS 상호 배타 충돌 제거
    """
    # 로그 디렉토리 — 가장 먼저 확보
    log_dir = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "jump_worker_dashboard"
    log_dir.mkdir(parents=True, exist_ok=True)
    py_log_path = log_dir / "update_py.log"
    bat_log_path = log_dir / "update.log"

    def _pylog(msg: str) -> None:
        try:
            with open(py_log_path, "a", encoding="utf-8") as f:
                from datetime import datetime as _dt
                f.write(f"[{_dt.now().isoformat(timespec='seconds')}] {msg}\n")
        except Exception:
            pass

    _pylog("=" * 60)
    _pylog(f"install_and_restart_windows called (cmd.exe launcher)")
    _pylog(f"setup_exe_path={setup_exe_path}")
    _pylog(f"setup_exe exists={setup_exe_path.exists()}")
    _pylog(f"setup_exe size={setup_exe_path.stat().st_size if setup_exe_path.exists() else 'N/A'}")
    _pylog(f"sys.executable={sys.executable}")
    _pylog(f"parent_pid={os.getpid()}")
    _pylog(f"LOCALAPPDATA={os.environ.get('LOCALAPPDATA', '(unset)')}")

    if not setup_exe_path.exists():
        _pylog("FATAL: installer file missing — aborting")
        raise UpdateError(f"인스톨러 파일이 없습니다: {setup_exe_path}")

    # 배치 파일을 log_dir 에 직접 작성 (tmp 는 onefile 환경에서 사라질 수 있음)
    bat_path = log_dir / "update.bat"
    parent_pid = os.getpid()

    # cmd.exe 배치 스크립트 — PowerShell 의존성 제거
    bat_script = f"""@echo off
setlocal enabledelayedexpansion

rem ── 로그 초기화 ──
set "LOG={bat_log_path}"
echo [%date% %time%] === Update started (parent PID={parent_pid}) === > "%LOG%"

rem ── 1. 부모 프로세스 종료 대기 (최대 30초) ──
echo [%date% %time%] Waiting for parent app to exit... >> "%LOG%"
set /a _tries=0
:waitloop
tasklist /FI "IMAGENAME eq jump-worker-dashboard.exe" 2>nul | find /I "jump-worker-dashboard.exe" >nul
if errorlevel 1 goto parentgone
set /a _tries+=1
if !_tries! geq 30 (
    echo [%date% %time%] Timeout waiting — force killing >> "%LOG%"
    taskkill /F /IM jump-worker-dashboard.exe /T >> "%LOG%" 2>&1
    goto parentgone
)
timeout /t 1 /nobreak >nul
goto waitloop

:parentgone
echo [%date% %time%] Parent exited — proceeding with install >> "%LOG%"
timeout /t 2 /nobreak >nul

rem ── 2. 인스톨러 실행 (UAC 내부 상승) ──
echo [%date% %time%] Running installer: {setup_exe_path} >> "%LOG%"
"{setup_exe_path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- >> "%LOG%" 2>&1
set _rc=!errorlevel!
echo [%date% %time%] Installer exit code: !_rc! >> "%LOG%"

rem ── 3. 설치 검증 ──
timeout /t 2 /nobreak >nul
set "EXE="
for %%p in (
    "%ProgramFiles%\\GUARDIAN\\jump-worker-dashboard.exe"
    "%ProgramFiles(x86)%\\GUARDIAN\\jump-worker-dashboard.exe"
    "%LOCALAPPDATA%\\Programs\\GUARDIAN\\jump-worker-dashboard.exe"
) do (
    if exist %%~p (
        set "EXE=%%~p"
        goto foundexe
    )
)

rem 레지스트리 fallback
for /f "tokens=2*" %%a in ('reg query "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall" /s /f "GUARDIAN" 2^>nul ^| find "InstallLocation"') do (
    if exist "%%~b\\jump-worker-dashboard.exe" (
        set "EXE=%%~b\\jump-worker-dashboard.exe"
        goto foundexe
    )
)

echo [%date% %time%] ERROR: exe not found after install >> "%LOG%"
goto done

:foundexe
echo [%date% %time%] Launching: !EXE! >> "%LOG%"
start "" "!EXE!"

:done
echo [%date% %time%] === Update done === >> "%LOG%"
endlocal
"""
    bat_path.write_text(bat_script, encoding="cp949", errors="replace")
    _pylog(f"Wrote batch script: {bat_path} ({bat_path.stat().st_size} bytes)")

    # ── Windows 상수 ──
    # CREATE_NEW_PROCESS_GROUP: 0x00000200 — Ctrl+C 전파 차단
    # CREATE_BREAKAWAY_FROM_JOB: 0x01000000 — 부모 Job Object에서 탈출 (Nuitka onefile 대응)
    # CREATE_NEW_CONSOLE: 0x00000010 — 독립 콘솔 (사용자에게 안 보이도록 SW_HIDE로 숨김)
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    CREATE_NEW_CONSOLE = 0x00000010
    creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_CONSOLE

    # SW_HIDE = 0 — 콘솔 창 숨기기
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE

    try:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=True,
            cwd=str(log_dir),
        )
        _pylog(f"cmd.exe launched: PID={proc.pid}")
    except FileNotFoundError as exc:
        _pylog(f"FATAL: cmd.exe not found: {exc}")
        raise UpdateError("시스템 오류: cmd.exe를 찾을 수 없습니다.") from exc
    except Exception as exc:
        _pylog(f"FATAL: cmd.exe launch failed: {type(exc).__name__}: {exc}")
        raise UpdateError(f"업데이트 헬퍼 실행 실패: {exc}") from exc

    _pylog("install_and_restart_windows: exiting parent process")
    # 부모 앱 종료 — 헬퍼가 모든 후속 작업 처리
    # 아주 짧게 대기해서 Popen이 완전히 분리되도록
    import time as _t
    _t.sleep(0.3)
    os._exit(0)


def install_and_restart_macos(zip_path: Path) -> None:
    """macOS: zip 압축 해제 → 기존 .app 교체 → 재시작.

    실행 중인 .app 위치 추정:
      sys.executable = .../jump-worker-dashboard.app/Contents/MacOS/jump-worker-dashboard
      .app 디렉토리: .../jump-worker-dashboard.app
    """
    if not zip_path.exists():
        raise UpdateError(f"업데이트 파일이 없습니다: {zip_path}")

    exe = Path(sys.executable).resolve()
    # .app/Contents/MacOS/binary → .app
    app_path = exe
    while app_path.parent != app_path:
        if app_path.suffix == ".app":
            break
        app_path = app_path.parent
    if app_path.suffix != ".app":
        raise UpdateError(f".app 디렉토리를 찾을 수 없습니다: {sys.executable}")

    # 임시 디렉토리에 압축 해제
    tmp_dir = Path(tempfile.mkdtemp(prefix="jump_update_"))
    extract_dir = tmp_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)

    # zip 해제 — macOS .app 번들은 심볼릭 링크/확장속성 유지가 중요하므로 ditto 사용.
    # Python zipfile은 심볼릭 링크를 일반 파일로 만들어 번들이 깨짐.
    try:
        subprocess.run(
            ["ditto", "-x", "-k", str(zip_path), str(extract_dir)],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # ditto 실패 시 fallback
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

    # extract_dir 안에서 .app 찾기
    new_app = None
    for entry in extract_dir.rglob("*.app"):
        if entry.is_dir():
            new_app = entry
            break
    if new_app is None:
        raise UpdateError("업데이트 zip에서 .app 을 찾을 수 없습니다.")

    # 교체 스크립트 — 현재 앱 종료된 후 실행
    script_path = tmp_dir / "update.sh"
    backup_path = app_path.parent / f"{app_path.name}.bak.{os.getpid()}"
    log_path = Path.home() / "jump_worker_dashboard" / "data" / "update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script_content = f"""#!/bin/bash
# 모든 출력을 ~/jump_worker_dashboard/data/update.log 에 기록 (디버깅용)
exec > "{log_path}" 2>&1
set -x

echo "=== Update script started: $(date) ==="

# 1. 이전 프로세스가 완전히 종료될 때까지 충분히 대기
sleep 5

# 2. 혹시 남은 프로세스 강제 종료
pkill -9 -f "jump-worker-dashboard" 2>/dev/null || true
sleep 2

# 3. 기존 앱을 .bak으로 백업 (재시도)
MOVED=0
for i in 1 2 3 4 5; do
  if mv "{app_path}" "{backup_path}" 2>/dev/null; then
    MOVED=1
    echo "기존 앱 백업 완료 (시도 $i)"
    break
  fi
  echo "mv 재시도 $i..."
  sleep 2
done
if [[ "$MOVED" != "1" ]]; then
  echo "ERROR: 기존 앱 이동 실패 — 업데이트 중단"
  exit 1
fi

# 4. 새 앱을 ditto로 복사 (mv보다 안전, 메타데이터 보존)
ditto "{new_app}" "{app_path}"
echo "ditto 완료"

# 5. 실행 권한 전체 보장 (PyInstaller .app 내부 모든 실행 파일)
chmod -R u+rwx "{app_path}"
find "{app_path}/Contents/MacOS" -type f -exec chmod 755 {{}} \\;
find "{app_path}/Contents/Frameworks" -type f -name "*.dylib" -exec chmod 755 {{}} \\; 2>/dev/null || true
find "{app_path}/Contents/Frameworks" -type f -name "*.so" -exec chmod 755 {{}} \\; 2>/dev/null || true
echo "권한 설정 완료"

# 6. quarantine 속성 제거 (Gatekeeper 경고 방지)
xattr -cr "{app_path}" 2>/dev/null || true
echo "xattr 정리 완료"

# 7. Launch Services 강제 재등록 (이전 앱 기억 제거)
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \\
  -f "{app_path}" 2>/dev/null || true
echo "lsregister 완료"

# 8. 백업 즉시 제거
rm -rf "{backup_path}" 2>/dev/null || true

# 9. Launch Services 캐시 갱신 시간 확보
sleep 2

# 10. 새 앱 GUI 실행 — 여러 방법으로 시도
echo "----- 실행 시도 -----"
EXE="{app_path}/Contents/MacOS/jump-worker-dashboard"
echo "EXE 존재 여부: $(ls -la "$EXE" 2>&1)"

# 시도 1: 표준 open
echo "[시도 1] open '{app_path}'"
open "{app_path}"
RC=$?
echo "  rc=$RC"
sleep 2
PIDS=$(pgrep -f "jump-worker-dashboard.app/Contents/MacOS")
echo "  실행 중 PID: $PIDS"
if [[ -n "$PIDS" ]]; then
  echo "  ✓ 성공"
else
  echo "[시도 2] open -n -F '{app_path}'"
  open -n -F "{app_path}"
  sleep 2
  PIDS=$(pgrep -f "jump-worker-dashboard.app/Contents/MacOS")
  echo "  PID: $PIDS"

  if [[ -z "$PIDS" ]]; then
    echo "[시도 3] osascript activate"
    osascript -e 'tell application "{app_path}" to activate' 2>&1 || true
    sleep 2
    PIDS=$(pgrep -f "jump-worker-dashboard.app/Contents/MacOS")
    echo "  PID: $PIDS"
  fi

  if [[ -z "$PIDS" ]]; then
    echo "[시도 4] launchctl 으로 사용자 세션에서 실행"
    USER_ID=$(id -u)
    launchctl asuser "$USER_ID" open "{app_path}" 2>&1 || true
    sleep 2
    PIDS=$(pgrep -f "jump-worker-dashboard.app/Contents/MacOS")
    echo "  PID: $PIDS"
  fi
fi

if [[ -z "$PIDS" ]]; then
  echo "ERROR: 모든 실행 시도 실패. ~/jump_worker_dashboard/data/update.log 확인 필요."
else
  echo "✓ 새 앱 실행 성공: PID $PIDS"
fi

# 11. 임시 폴더 정리 (열린 파일 참조 방지를 위해 마지막에)
sleep 3
rm -rf "{tmp_dir}" 2>/dev/null || true

echo "=== Update script done: $(date) ==="
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # 백그라운드로 스크립트 실행
    subprocess.Popen(
        ["/bin/bash", str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )

    # 현재 앱 종료
    os._exit(0)


def install_and_restart(downloaded_path: Path) -> None:
    """OS별 설치 분기."""
    p = detect_platform()
    if p == "windows":
        install_and_restart_windows(downloaded_path)
    elif p == "macos":
        install_and_restart_macos(downloaded_path)
    else:
        raise UpdateError(f"지원되지 않는 플랫폼: {p}")


def get_download_dir() -> Path:
    """업데이트 파일 다운로드 위치. (홈 디렉토리 하위)"""
    base = Path.home() / "jump_worker_dashboard" / "updates"
    base.mkdir(parents=True, exist_ok=True)
    return base


__all__ = [
    "UpdateInfo",
    "UpdateError",
    "check_latest_version",
    "download_update",
    "install_and_restart",
    "install_and_restart_windows",
    "install_and_restart_macos",
    "detect_platform",
    "get_download_dir",
]
