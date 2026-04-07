"""오피나라 사이트 전용 HTML 파싱 함수.

gnuboard 기반. cuteAlert 라이브러리 사용.
게시글: li.list-item 또는 tbody tr 패턴.
"""
from __future__ import annotations

from .._apms_parsers import (
    parse_boards_apms as parse_boards,
    parse_comments_gnuboard as parse_comments,
    parse_posts_list_item,
    parse_posts_table,
    parse_profile_apms as parse_profile,
)
from ...types import Post


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 추출."""
    posts = parse_posts_list_item(page_source, board_id)
    if not posts:
        posts = parse_posts_table(page_source, board_id)
    return posts


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
