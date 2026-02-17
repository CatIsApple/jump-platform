#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def normalize_domain(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    if s.startswith("http://"):
        s = s[7:]
    elif s.startswith("https://"):
        s = s[8:]
    return s.rstrip("/")


def req_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
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
    for k, v in headers.items():
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


def main() -> int:
    default_json = Path(__file__).resolve().parents[2] / "jump_worker_dashboard" / "data" / "platform_domains.json"

    p = argparse.ArgumentParser(description="Sync local platform_domains.json to jump-backend admin API")
    p.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "https://api.guardian01.online"))
    p.add_argument("--json-file", default=str(default_json))
    p.add_argument("--access-id", default=os.environ.get("CF_ACCESS_CLIENT_ID", ""), required=False)
    p.add_argument("--access-secret", default=os.environ.get("CF_ACCESS_CLIENT_SECRET", ""), required=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--delete-empty", action="store_true", help="빈 도메인은 DELETE 호출")
    p.add_argument("--insecure", action="store_true", help="TLS 인증서 검증 비활성화(테스트 전용)")
    args = p.parse_args()

    if not args.access_id or not args.access_secret:
        print("[ERROR] Access Client ID/Secret이 필요합니다.", file=sys.stderr)
        return 2

    base = args.base_url.strip().rstrip("/")
    if not base.startswith("http"):
        print("[ERROR] base-url 형식 오류", file=sys.stderr)
        return 2

    path = Path(args.json_file)
    if not path.exists():
        print(f"[ERROR] json 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("[ERROR] json 최상위가 dict가 아닙니다.", file=sys.stderr)
        return 2

    headers = {
        "CF-Access-Client-Id": args.access_id,
        "CF-Access-Client-Secret": args.access_secret,
    }

    ok = 0
    fail = 0
    skipped = 0

    for site_key, raw_domain in data.items():
        if not isinstance(site_key, str):
            continue
        site = site_key.strip()
        if not site:
            continue

        domain = normalize_domain(str(raw_domain or ""))

        if not domain:
            if not args.delete_empty:
                skipped += 1
                print(f"[SKIP] {site}: 빈 도메인")
                continue
            if args.dry_run:
                print(f"[DRY] DELETE {site}")
                ok += 1
                continue
            st, payload = req_json(
                "DELETE",
                f"{base}/v1/admin/platform-domains?site_key={urllib.parse.quote(site)}",
                headers=headers,
                insecure=args.insecure,
            )
            if st == 200:
                ok += 1
                print(f"[OK] DELETE {site}")
            else:
                fail += 1
                print(f"[FAIL] DELETE {site}: HTTP {st} / {payload}")
            continue

        body = {"site_key": site, "domain": domain}
        if args.dry_run:
            ok += 1
            print(f"[DRY] PUT {site} -> {domain}")
            continue

        st, payload = req_json(
            "PUT",
            f"{base}/v1/admin/platform-domains",
            headers=headers,
            body=body,
            insecure=args.insecure,
        )
        if st == 200:
            ok += 1
            print(f"[OK] {site} -> {domain}")
        else:
            fail += 1
            print(f"[FAIL] {site}: HTTP {st} / {payload}")

    print(f"완료: success={ok}, failed={fail}, skipped={skipped}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
