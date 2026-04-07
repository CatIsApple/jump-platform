"""Site modules data classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Profile:
    nickname: str = ""
    level: str = ""
    post_count: int = 0
    comment_count: int = 0
    point: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Board:
    id: str = ""
    name: str = ""
    description: str = ""
    post_count: int = 0


@dataclass
class Post:
    id: str = ""
    board_id: str = ""
    subject: str = ""
    author: str = ""
    date: str = ""
    view_count: int = 0
    comment_count: int = 0


@dataclass
class Comment:
    id: str = ""
    post_id: str = ""
    parent_id: str = ""  # 대댓글인 경우 부모 댓글 ID
    author: str = ""
    content: str = ""
    date: str = ""
    is_reply: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "post_id": self.post_id,
            "parent_id": self.parent_id,
            "author": self.author,
            "content": self.content,
            "date": self.date,
            "is_reply": self.is_reply,
        }


@dataclass
class JumpResult:
    status: str = ""
    message: str = ""
    remaining_count: int = -1
    cooldown_seconds: int = 0


@dataclass
class WriteResult:
    success: bool = False
    id: str = ""
    message: str = ""


@dataclass
class LoginResult:
    success: bool = False
    method: str = ""  # "cookie" | "form" | "ajax" | "already"
    message: str = ""
    account: dict[str, str] = field(default_factory=dict)
