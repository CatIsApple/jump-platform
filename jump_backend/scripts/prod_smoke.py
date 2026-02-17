#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def req_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    insecure: bool = False,
) -> tuple[int, Any]:
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, method=method.upper(), data=data)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", os.environ.get("JUMP_HTTP_UA", DEFAULT_UA))
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        if v:
            req.add_header(k, v)

    try:
        ctx = ssl._create_unverified_context() if insecure else None
        with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = raw
            return int(resp.status), payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = raw
        return int(e.code), payload


def ensure_ok(step: str, status: int, payload: Any, allowed: set[int] | None = None) -> None:
    allowed_codes = allowed or {200}
    if status not in allowed_codes:
        raise RuntimeError(f"{step} 실패: HTTP {status} / {payload}")


def main() -> int:
    p = argparse.ArgumentParser(description="jump-backend production smoke test")
    p.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "https://api.guardian01.online"), help="예: https://api.guardian01.online")
    p.add_argument("--license-key", default=os.environ.get("LICENSE_KEY", ""), help="테스트용 라이센스 키")
    p.add_argument("--device-id", default=os.environ.get("DEVICE_ID", "prod-smoke"), help="장치 ID")
    p.add_argument("--access-id", default=os.environ.get("CF_ACCESS_CLIENT_ID", ""), help="관리자 Access Client ID")
    p.add_argument("--access-secret", default=os.environ.get("CF_ACCESS_CLIENT_SECRET", ""), help="관리자 Access Client Secret")
    p.add_argument("--skip-auth", action="store_true", help="라이센스 로그인/도메인 호출/로그아웃 검증을 건너뜀")
    p.add_argument("--insecure", action="store_true", help="TLS 인증서 검증 비활성화(테스트 전용)")
    args = p.parse_args()

    base = args.base_url.strip().rstrip("/")
    if not base.startswith("http"):
        print("[ERROR] --base-url 형식이 올바르지 않습니다.", file=sys.stderr)
        return 2

    print(f"[1/4] health check: {base}/v1/health")
    st, payload = req_json("GET", f"{base}/v1/health", insecure=args.insecure)
    ensure_ok("health", st, payload)
    print(f"  - OK ({st})")

    if args.access_id and args.access_secret:
        print(f"[2/4] admin health check: {base}/v1/admin/health")
        st, payload = req_json(
            "GET",
            f"{base}/v1/admin/health",
            headers={
                "CF-Access-Client-Id": args.access_id,
                "CF-Access-Client-Secret": args.access_secret,
            },
            insecure=args.insecure,
        )
        ensure_ok("admin health", st, payload)
        print(f"  - OK ({st})")
    else:
        print("[2/4] admin health check: SKIP (Access token 미입력)")

    if args.skip_auth:
        print("[3/4] auth/domain smoke: SKIP (--skip-auth)")
        print("[4/4] done")
        return 0

    if not args.license_key:
        print("[ERROR] --license-key 또는 LICENSE_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 2

    print("[3/4] auth login")
    st, payload = req_json(
        "POST",
        f"{base}/v1/auth/login",
        body={
            "license_key": args.license_key,
            "device_id": args.device_id,
        },
        insecure=args.insecure,
    )
    ensure_ok("auth login", st, payload)
    token = ""
    if isinstance(payload, dict):
        token = str(payload.get("token") or "").strip()
    if not token:
        raise RuntimeError(f"auth login 실패: token 누락 / {payload}")
    print("  - login OK")

    print("[3/4] platform-domains")
    st, payload = req_json(
        "GET",
        f"{base}/v1/platform-domains",
        headers={"Authorization": f"Bearer {token}"},
        insecure=args.insecure,
    )
    ensure_ok("platform-domains", st, payload)
    domains_count = 0
    if isinstance(payload, dict) and isinstance(payload.get("domains"), dict):
        domains_count = len(payload["domains"])
    print(f"  - domains OK ({domains_count}개)")

    print("[3/4] auth logout")
    st, payload = req_json(
        "POST",
        f"{base}/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
        insecure=args.insecure,
    )
    ensure_ok("auth logout", st, payload)
    print("  - logout OK")

    print("[4/4] done: production smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise
