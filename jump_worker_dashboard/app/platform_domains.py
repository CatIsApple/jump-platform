from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .file_manager import data_dir
from .sites import SITE_KEYS


_FILENAME = "platform_domains.json"

_CACHE: dict[str, str] | None = None
_CACHE_MTIME: float | None = None


def platform_domains_path() -> Path:
    return data_dir() / _FILENAME


def _normalize_mapping(obj: Any) -> dict[str, str]:
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        if v is None:
            out[key] = ""
            continue
        if not isinstance(v, str):
            v = str(v)
        out[key] = v.strip()
    return out


def load_platform_domains() -> dict[str, str]:
    """플랫폼 도메인 매핑 로드.

    파일이 없거나 파싱에 실패하면 빈 dict를 반환한다.
    """
    path = platform_domains_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}
    except Exception:
        mtime = None

    global _CACHE, _CACHE_MTIME  # noqa: PLW0603
    if _CACHE is not None and mtime is not None and _CACHE_MTIME == mtime:
        return dict(_CACHE)

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    mapping = _normalize_mapping(data)
    _CACHE = dict(mapping)
    _CACHE_MTIME = mtime
    return mapping


def save_platform_domains(mapping: dict[str, str]) -> None:
    path = platform_domains_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 템플릿 키는 항상 포함시키되, 사용자가 추가한 키도 보존한다.
    payload: dict[str, str] = {}
    for k in SITE_KEYS:
        payload[k] = (mapping.get(k) or "").strip()
    for k, v in mapping.items():
        if k in payload:
            continue
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        if v is None:
            payload[key] = ""
        elif isinstance(v, str):
            payload[key] = v.strip()
        else:
            payload[key] = str(v).strip()

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    global _CACHE, _CACHE_MTIME  # noqa: PLW0603
    _CACHE = dict(payload)
    try:
        _CACHE_MTIME = path.stat().st_mtime
    except Exception:
        _CACHE_MTIME = None


def ensure_platform_domains(db: Database | None = None) -> dict[str, str]:
    """도메인 매핑 파일이 없으면 생성한다.

    - 기존 DB(workflows)에 도메인이 있으면, 해당 값을 초기값으로 채워준다.
    - 파일이 이미 있으면, 누락된 SITE_KEYS만 보정한다.
    """
    path = platform_domains_path()
    mapping: dict[str, str] = {}
    changed = False

    if path.exists():
        mapping = load_platform_domains()
    else:
        changed = True

    # 템플릿 키 보정
    for k in SITE_KEYS:
        if k not in mapping:
            mapping[k] = ""
            changed = True

    # DB 값으로 빈 항목 채우기(초기 마이그레이션 편의)
    if db is not None:
        try:
            for wf in db.list_workflows():
                sk = (wf.site_key or "").strip()
                dom = (wf.domain or "").strip()
                if not sk or not dom:
                    continue
                if not mapping.get(sk):
                    mapping[sk] = dom
                    changed = True
        except Exception:
            pass

    if changed:
        save_platform_domains(mapping)
    return mapping


def resolve_platform_domain(site_key: str, fallback_domain: str = "") -> str:
    site = (site_key or "").strip()
    if not site:
        return (fallback_domain or "").strip()
    mapping = load_platform_domains()
    v = (mapping.get(site) or "").strip()
    if v:
        return v
    return (fallback_domain or "").strip()

