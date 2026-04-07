"""오피뷰 사이트 전용 HTML 파싱 함수.

APMS Basic 테마 (li.list-item 패턴).
게시판: bo_table 기반 네비게이션.
댓글: #bo_vc > div.media[id^="c_"] (APMS media 패턴).
프로필: mypage.php (.sv_member, /images/mb/, list-group-item).
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from ...types import Board, Comment, Post, Profile

# 게시판 목록과 게시글 목록은 공용 APMS 파서 사용
from .._apms_parsers import (
    parse_boards_apms as parse_boards,
    parse_posts_list_item as parse_posts,
)


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)


# ──────────────────────────────────────────────────────────
#  parse_profile
# ──────────────────────────────────────────────────────────

def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출.

    /bbs/mypage.php:
    - 닉네임: .mypage-skin .sv_member (이미지 제거 후 텍스트)
    - 레벨: img[src*='/images/mb/'] → 숫자 추출
    - 포인트: .list-group-item "XXX점 MP"
    """
    soup = BeautifulSoup(page_source, "html.parser")

    nickname = ""
    level = ""
    point = 0

    # 닉네임: .mypage-skin .sv_member
    skin = soup.select_one(".mypage-skin")
    if skin:
        member_el = skin.select_one(".sv_member")
        if member_el:
            texts = list(member_el.stripped_strings)
            nickname = texts[-1] if texts else ""
    if not nickname:
        member_el = soup.select_one(".sv_member")
        if member_el:
            texts = list(member_el.stripped_strings)
            nickname = texts[-1] if texts else ""

    # 레벨: img with /images/mb/ in src
    level_img = None
    if skin:
        level_img = skin.select_one("img[src*='/images/mb/']")
    if not level_img:
        level_img = soup.select_one("img[src*='/images/mb/']")
    if level_img:
        src = level_img.get("src", "")
        if isinstance(src, str):
            m = re.search(r"/(?:images/mb|mb)/(\d+)\.png", src)
            if m:
                level = m.group(1)

    # 포인트: "XXX점 MP" in list-group-item
    for li in soup.select(".list-group-item"):
        text = _text(li)
        m = re.search(r"([0-9,]+)점", text)
        if m and "MP" in text:
            point = int(m.group(1).replace(",", ""))
            break

    # fallback: 포인트 패턴
    if not point:
        m = re.search(r"포인트[^<]*?<[^>]*>([0-9,]+)", page_source)
        if m:
            point = int(m.group(1).replace(",", ""))

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=0,
        comment_count=0,
    )


# ──────────────────────────────────────────────────────────
#  parse_comments
# ──────────────────────────────────────────────────────────

def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    오피뷰 댓글 구조 (APMS media 패턴):
    #bo_vc > .comment-media > div.media[id^="c_"]
      .media-body > .media-heading > .sv_member → 작성자
      .media-body 직접 텍스트 노드 → 내용
    """
    soup = BeautifulSoup(page_source, "html.parser")
    comments: list[Comment] = []

    bo_vc = soup.select_one("#bo_vc")
    if not bo_vc:
        return comments

    for media in bo_vc.select("div.media[id]"):
        cid = media.get("id", "")
        if isinstance(cid, list):
            cid = cid[0] if cid else ""
        cid = str(cid)
        if not cid.startswith("c_"):
            continue
        cid = cid[2:]  # "c_" 접두사 제거

        # 작성자: .sv_member 마지막 텍스트
        author = ""
        author_el = media.select_one(".sv_member")
        if author_el:
            texts = list(author_el.stripped_strings)
            author = texts[-1] if texts else ""

        # 내용: .media-body 직접 자식 텍스트 노드 (중복 제거)
        body = media.select_one(".media-body")
        if not body:
            continue

        content_parts: list[str] = []
        seen_texts: set[str] = set()
        for child in body.children:
            if isinstance(child, NavigableString):
                text = child.strip()
                if text and text not in seen_texts:
                    content_parts.append(text)
                    seen_texts.add(text)
        content = " ".join(content_parts)

        # fallback: .media-body에서 heading/btn 제외 텍스트
        if not content:
            clone = BeautifulSoup(str(body), "html.parser")
            for tag in clone.select(".media-heading, .cmt-good-btn, .cmt_btn, .edit_cmt, .sv_wrap"):
                tag.decompose()
            content = _text(clone).strip()

        if not content:
            continue

        comments.append(Comment(
            id=cid,
            post_id=post_id,
            author=author,
            content=content,
            date="",
        ))

    return comments


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
