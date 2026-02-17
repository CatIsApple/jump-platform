from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable


_LOCK = threading.RLock()


def data_dir() -> Path:
    """앱 데이터 디렉토리.

    우선순위:
    1) 환경변수 `JUMP_WORKER_DATA_DIR`
    2) frozen 실행파일: `~/jump_worker_dashboard/data`
    3) 소스 실행: `jump_worker_dashboard/data`
    """
    env_path = os.environ.get("JUMP_WORKER_DATA_DIR", "").strip()
    if env_path:
        d = Path(env_path).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        d = Path.home() / "jump_worker_dashboard" / "data"
    else:
        base = Path(__file__).resolve().parents[1]
        d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cookies_path() -> Path:
    return data_dir() / "cookies.json"


def login_data_path() -> Path:
    # 원본과 동일하게 숨김 파일명을 사용
    return data_dir() / ".logindata.json"


def artifacts_dir() -> Path:
    """실행 증적(스크린샷 등) 저장 디렉토리."""
    d = data_dir() / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_artifacts(*, max_keep: int = 500, max_age_days: int = 14) -> None:
    """증적 폴더 정리.

    - max_age_days: 오래된 파일 삭제(0이면 비활성)
    - max_keep: 최신 파일만 보관(0이면 비활성)
    """
    d = artifacts_dir()
    now = time.time()
    max_age_seconds = max(0, int(max_age_days)) * 86400

    # 1) 기간 기반 정리
    if max_age_seconds > 0:
        for p in list(d.iterdir()):
            if not p.is_file():
                continue
            try:
                if now - p.stat().st_mtime > max_age_seconds:
                    p.unlink()
            except Exception:
                continue

    # 2) 개수 기반 정리
    keep = int(max_keep)
    if keep <= 0:
        return
    try:
        files = [p for p in d.iterdir() if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            try:
                p.unlink()
            except Exception:
                continue
    except Exception:
        return


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        return {}
    except Exception:
        return {}
    return {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
    tmp.replace(path)


def save_cookies(
    driver: Any,
    domain_key: str,
    userid: str,
    cookie_keys: Iterable[str] | None,
    filepath: Path | None = None,
) -> bool:
    """Selenium driver의 쿠키를 cookies.json에 저장.

    원본 FileManager.save_cookies의 파일 포맷을 유지한다.
    """
    path = filepath or cookies_path()
    try:
        current_url = str(getattr(driver, "current_url", ""))
        if domain_key not in current_url:
            # 원본도 도메인 불일치 시 저장하지 않음
            return False

        keys = set(cookie_keys or [])
        driver_cookies = driver.get_cookies()
        if not isinstance(driver_cookies, list):
            return False

        with _LOCK:
            cookies = _load_json_file(path)
            cookies.setdefault(domain_key, {}).setdefault(userid, {})

            for c in driver_cookies:
                name = c.get("name")
                if not name:
                    continue
                if keys and name not in keys:
                    continue
                cookies[domain_key][userid][name] = {
                    "value": c.get("value", ""),
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                }

            _atomic_write_json(path, cookies)
        return True
    except Exception:
        return False


def load_cookies(
    driver: Any,
    domain_key: str,
    userid: str,
    cookie_keys: Iterable[str] | None,
    filepath: Path | None = None,
) -> bool:
    """cookies.json에서 쿠키를 읽어 Selenium driver에 주입.

    원본 jump.exe(FileManager.load_cookies) 방식에 맞춰:
    - 이미 driver에 존재하는 쿠키의 value만 교체한다.
      (도메인/패스 불일치로 add_cookie가 실패하는 문제를 줄이기 위함)
    """
    path = filepath or cookies_path()
    if not path.exists():
        return False

    try:
        with _LOCK:
            cookies = _load_json_file(path)
        domain_cookies = cookies.get(domain_key, {}).get(userid, {})
        if not isinstance(domain_cookies, dict) or not domain_cookies:
            return False

        keys = set(cookie_keys or [])
        injected = 0
        for name, info in domain_cookies.items():
            if keys and name not in keys:
                continue
            if not isinstance(info, dict):
                continue
            value = str(info.get("value", ""))
            if not value:
                continue

            # 1) 원본 방식: driver에 존재하는 쿠키를 찾아 value만 교체
            try:
                existing_cookie = next(
                    (c for c in (driver.get_cookies() or []) if c.get("name") == name),
                    None,
                )
            except Exception:
                existing_cookie = None

            if existing_cookie:
                try:
                    updated_cookie = dict(existing_cookie)
                    updated_cookie["value"] = value
                    driver.delete_cookie(name)
                    driver.add_cookie(updated_cookie)
                    injected += 1
                    continue
                except Exception:
                    # fall through to best-effort injection
                    pass

            # 2) best-effort: 쿠키가 driver에 없을 때(또는 업데이트 실패 시) 현재 도메인으로 주입 시도
            try:
                driver.delete_cookie(name)
            except Exception:
                pass
            try:
                cookie: dict[str, Any] = {"name": name, "value": value}
                if isinstance(info.get("path"), str) and info.get("path"):
                    cookie["path"] = info["path"]
                driver.add_cookie(cookie)
                injected += 1
            except Exception:
                continue

        return injected > 0
    except Exception:
        return False


def save_json(
    domain_key: str,
    userid: str,
    new_data: dict[str, Any],
    filepath: Path | None = None,
) -> bool:
    """원본 FileManager.save_json과 동일한 포맷으로 토큰/로그인 데이터를 저장."""
    path = filepath or login_data_path()
    try:
        with _LOCK:
            data = _load_json_file(path)
            data.setdefault(domain_key, {})
            data[domain_key][userid] = new_data
            _atomic_write_json(path, data)
        return True
    except Exception:
        return False


def load_json(domain_key: str, userid: str, filepath: Path | None = None) -> dict[str, Any] | None:
    """원본 FileManager.load_json과 동일."""
    path = filepath or login_data_path()
    try:
        with _LOCK:
            data = _load_json_file(path)
        value = data.get(domain_key, {}).get(userid)
        if isinstance(value, dict):
            return value
        return None
    except Exception:
        return None
