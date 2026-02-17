#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import platform
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from jump_worker_dashboard.app.backend_client import BackendConfig, BackendError, WorkerBackendClient, normalize_base_url


def default_device_id() -> str:
    raw = f"{platform.node()}|{platform.system()}|{platform.machine()}"
    return f"jump-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def load_setting(db_path: Path, key: str, default: str = "") -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row and row[0] is not None else default
    finally:
        conn.close()


def main() -> int:
    default_db = ROOT / "data" / "worker_dashboard.db"

    p = argparse.ArgumentParser(description="worker backend integration smoke test")
    p.add_argument("--db-path", default=str(default_db))
    p.add_argument("--base-url", default="")
    p.add_argument("--license-key", default="")
    p.add_argument("--device-id", default="")
    args = p.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[ERROR] DB 파일이 없습니다: {db_path}", file=sys.stderr)
        return 2

    base_url = normalize_base_url(args.base_url or load_setting(db_path, "backend_base_url", ""))
    license_key = (args.license_key or load_setting(db_path, "backend_license_key", "")).strip()
    device_id = (args.device_id or load_setting(db_path, "backend_device_id", "")).strip() or default_device_id()

    if not base_url:
        print("[ERROR] backend_base_url이 비어 있습니다.", file=sys.stderr)
        return 2
    if not license_key:
        print("[ERROR] backend_license_key가 비어 있습니다.", file=sys.stderr)
        return 2

    client = WorkerBackendClient(BackendConfig(base_url=base_url))

    print(f"[1/3] login: {base_url}")
    try:
        login = client.login(license_key=license_key, device_id=device_id)
    except BackendError as exc:
        print(f"[ERROR] login 실패: {exc} (status={exc.status_code})", file=sys.stderr)
        return 1

    token = str(login.get("token") or "").strip()
    if not token:
        print("[ERROR] login 응답에 token 누락", file=sys.stderr)
        return 1
    print("  - login OK")

    print("[2/3] platform-domains")
    try:
        domains = client.platform_domains(token)
    except BackendError as exc:
        print(f"[ERROR] platform-domains 실패: {exc} (status={exc.status_code})", file=sys.stderr)
        return 1

    count = 0
    if isinstance(domains.get("domains"), dict):
        count = len(domains["domains"])
    print(f"  - domains OK ({count}개)")

    print("[3/3] logout")
    try:
        client.logout(token)
    except BackendError as exc:
        print(f"[WARN] logout 실패(치명 아님): {exc} (status={exc.status_code})")
    print("  - done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
