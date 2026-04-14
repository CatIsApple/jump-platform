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
    """Windows: setup.exe를 사일런트 모드로 실행 → 현재 앱 종료.

    Inno Setup 사일런트 플래그:
      /VERYSILENT  : 진행 UI 없음
      /SUPPRESSMSGBOXES : 메시지박스 자동 OK
      /NORESTART   : 자동 재부팅 방지
      /CLOSEAPPLICATIONS : 실행 중인 앱 자동 종료
      /RESTARTAPPLICATIONS : 설치 후 앱 재실행
    """
    if not setup_exe_path.exists():
        raise UpdateError(f"인스톨러 파일이 없습니다: {setup_exe_path}")

    flags = [
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
    ]

    # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [str(setup_exe_path), *flags],
        creationflags=creationflags,
        close_fds=True,
    )

    # 현재 앱 종료 (인스톨러가 실행 중인 앱 닫기를 처리)
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

    # zip 해제
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
    script_content = f"""#!/bin/bash
set -e
sleep 2
# 백업 후 교체
mv "{app_path}" "{backup_path}" 2>/dev/null || true
mv "{new_app}" "{app_path}"
xattr -cr "{app_path}" 2>/dev/null || true
# 백업 제거
rm -rf "{backup_path}" 2>/dev/null || true
rm -rf "{tmp_dir}" 2>/dev/null || true
# 재시작
open "{app_path}"
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
