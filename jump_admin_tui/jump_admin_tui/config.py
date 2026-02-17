from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


def _default_config_dir() -> Path:
    # macOS/Linux: ~/.config/jump_admin_tui
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "jump_admin_tui"
    return Path.home() / ".config" / "jump_admin_tui"


@dataclass(frozen=True)
class AppConfig:
    api_base_url: str = ""
    access_client_id: str = ""
    access_client_secret: str = ""
    admin_token: str = ""  # optional


def config_path() -> Path:
    return _default_config_dir() / "config.json"


def load_config() -> AppConfig:
    p = config_path()
    if not p.exists():
        return AppConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return AppConfig()

    return AppConfig(
        api_base_url=str(data.get("api_base_url", "")).strip(),
        access_client_id=str(data.get("access_client_id", "")).strip(),
        access_client_secret=str(data.get("access_client_secret", "")).strip(),
        admin_token=str(data.get("admin_token", "")).strip(),
    )


def save_config(cfg: AppConfig) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

