from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Workflow:
    id: Optional[int]
    name: str
    site_key: str
    domain: str
    shop_name: str
    username: str
    password: str
    enabled: bool = True
    use_browser: bool = True
    schedules: list[str] = field(default_factory=list)
    post_urls: list[str] = field(default_factory=list)  # 알밤 등 멀티 포스트용


@dataclass
class HistoryItem:
    id: int
    workflow_id: int
    workflow_name: str
    trigger_type: str
    scheduled_for: str
    started_at: str
    finished_at: str
    status: str
    message: str
