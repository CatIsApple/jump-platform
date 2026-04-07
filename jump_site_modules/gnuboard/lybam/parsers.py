"""리밤(외로운밤) 사이트 전용 HTML 파싱 함수.

APMS Basic 테마 계열 (.layer_login 모달 로그인, a.btn.btn-pink 점프 버튼).
게시글: li.list-item 패턴.
"""
from __future__ import annotations

from .._apms_parsers import (
    parse_boards_apms as parse_boards,
    parse_comments_gnuboard as parse_comments,
    parse_posts_list_item as parse_posts,
    parse_profile_apms as parse_profile,
)

__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
