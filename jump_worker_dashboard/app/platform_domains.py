from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .file_manager import data_dir
from .sites import SITE_KEYS


_FILENAME = "platform_domains.json"

# 내부 캐시: {"site_key": {"domain": str, "enabled": bool}}
_CACHE_FULL: dict[str, dict[str, Any]] | None = None
_CACHE_MTIME: float | None = None


def platform_domains_path() -> Path:
    return data_dir() / _FILENAME


def _parse_entry(v: Any) -> dict[str, Any]:
    """JSON 값을 {"domain": str, "enabled": bool}로 정규화.

    하위 호환:
      - 문자열 → {"domain": str, "enabled": True}
      - dict   → {"domain": str, "enabled": bool}
      - None   → {"domain": "", "enabled": True}
    """
    if v is None:
        return {"domain": "", "enabled": True}
    if isinstance(v, str):
        return {"domain": v.strip(), "enabled": True}
    if isinstance(v, dict):
        domain = str(v.get("domain") or "").strip()
        enabled = v.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = bool(enabled)
        return {"domain": domain, "enabled": enabled}
    return {"domain": str(v).strip(), "enabled": True}


def _normalize_full(obj: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(obj, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        out[key] = _parse_entry(v)
    return out


def _invalidate_cache() -> None:
    global _CACHE_FULL, _CACHE_MTIME  # noqa: PLW0603
    _CACHE_FULL = None
    _CACHE_MTIME = None


def _load_full() -> dict[str, dict[str, Any]]:
    """전체 데이터 로드 (캐시 포함)."""
    path = platform_domains_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    except Exception:
        mtime = None

    global _CACHE_FULL, _CACHE_MTIME  # noqa: PLW0603
    if _CACHE_FULL is not None and mtime is not None and _CACHE_MTIME == mtime:
        return {k: dict(v) for k, v in _CACHE_FULL.items()}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    full = _normalize_full(data)
    _CACHE_FULL = {k: dict(v) for k, v in full.items()}
    _CACHE_MTIME = mtime
    return full


# ── Public API ──


def load_platform_domains() -> dict[str, str]:
    """활성화된 도메인만 반환 (하위 호환 API).

    반환: {"site_key": "domain"} (enabled=True인 것만)
    """
    full = _load_full()
    return {k: v["domain"] for k, v in full.items() if v.get("enabled", True)}


def load_platform_domains_full() -> dict[str, dict[str, Any]]:
    """전체 도메인 매핑 반환 (enabled 포함).

    반환: {"site_key": {"domain": str, "enabled": bool}}
    """
    return _load_full()


def save_platform_domains(mapping: dict[str, Any]) -> None:
    """도메인 매핑 저장.

    입력 형식:
      - dict[str, str]: 하위 호환 (전부 enabled=True)
      - dict[str, dict]: 새 형식 {"domain": str, "enabled": bool}
    """
    path = platform_domains_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 기존 데이터에서 enabled 상태 보존
    existing = _load_full()

    payload: dict[str, dict[str, Any]] = {}

    # 템플릿 키 보장
    for k in SITE_KEYS:
        if k in mapping:
            entry = _parse_entry(mapping[k])
        elif k in existing:
            entry = existing[k]
        else:
            entry = {"domain": "", "enabled": True}
        payload[k] = entry

    # 추가 키 보존
    for k, v in mapping.items():
        if k in payload:
            # 이미 위에서 처리됨 — 단, mapping에서 온 값 우선
            new_entry = _parse_entry(v)
            # enabled 상태: 새 매핑이 dict이면 그 값 사용, 아니면 기존 보존
            if isinstance(v, dict) and "enabled" in v:
                payload[k]["enabled"] = new_entry["enabled"]
            if new_entry["domain"]:
                payload[k]["domain"] = new_entry["domain"]
            continue
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        payload[key] = _parse_entry(v)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    _invalidate_cache()
    # 즉시 캐시 갱신
    _load_full()


def save_platform_domains_full(full_mapping: dict[str, dict[str, Any]]) -> None:
    """전체 매핑을 그대로 저장 (enabled 포함)."""
    path = platform_domains_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, dict[str, Any]] = {}
    for k in SITE_KEYS:
        if k in full_mapping:
            payload[k] = _parse_entry(full_mapping[k])
        else:
            payload[k] = {"domain": "", "enabled": True}
    for k, v in full_mapping.items():
        if k not in payload:
            key = k.strip() if isinstance(k, str) else ""
            if key:
                payload[key] = _parse_entry(v)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    _invalidate_cache()
    _load_full()


def set_platform_enabled(site_key: str, enabled: bool) -> None:
    """특정 사이트의 활성화 상태 변경."""
    full = _load_full()
    key = (site_key or "").strip()
    if not key:
        return
    if key in full:
        full[key]["enabled"] = enabled
    else:
        full[key] = {"domain": "", "enabled": enabled}
    save_platform_domains_full(full)


def is_platform_enabled(site_key: str) -> bool:
    """사이트가 활성화되어 있는지 확인."""
    full = _load_full()
    key = (site_key or "").strip()
    entry = full.get(key)
    if entry is None:
        return True  # 등록 안 된 키는 기본 활성
    return entry.get("enabled", True)


def ensure_platform_domains(db: Database | None = None) -> dict[str, str]:
    """도메인 매핑 파일이 없으면 생성한다.

    - 기존 DB(workflows)에 도메인이 있으면, 해당 값을 초기값으로 채워준다.
    - 파일이 이미 있으면, 누락된 SITE_KEYS만 보정한다.
    - 반환: 활성화된 도메인만 (하위 호환)
    """
    path = platform_domains_path()
    full: dict[str, dict[str, Any]] = {}
    changed = False

    if path.exists():
        full = _load_full()
    else:
        changed = True

    # 템플릿 키 보정
    for k in SITE_KEYS:
        if k not in full:
            full[k] = {"domain": "", "enabled": True}
            changed = True

    # DB 값으로 빈 항목 채우기(초기 마이그레이션 편의)
    if db is not None:
        try:
            for wf in db.list_workflows():
                sk = (wf.site_key or "").strip()
                dom = (wf.domain or "").strip()
                if not sk or not dom:
                    continue
                if sk in full and not full[sk]["domain"]:
                    full[sk]["domain"] = dom
                    changed = True
                elif sk not in full:
                    full[sk] = {"domain": dom, "enabled": True}
                    changed = True
        except Exception:
            pass

    if changed:
        save_platform_domains_full(full)

    # 하위 호환: 활성화된 것만 반환
    return {k: v["domain"] for k, v in full.items() if v.get("enabled", True)}


def resolve_platform_domain(site_key: str, fallback_domain: str = "") -> str:
    """도메인 조회. 비활성화된 사이트는 빈 문자열 반환."""
    site = (site_key or "").strip()
    if not site:
        return (fallback_domain or "").strip()
    full = _load_full()
    entry = full.get(site)
    if entry is None:
        return (fallback_domain or "").strip()
    if not entry.get("enabled", True):
        return ""  # 비활성화 → 빈 문자열
    v = (entry.get("domain") or "").strip()
    if v:
        return v
    return (fallback_domain or "").strip()
