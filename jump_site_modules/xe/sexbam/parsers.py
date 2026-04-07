"""섹밤 사이트 전용 HTML 파싱 함수.

XpressEngine 기반. Cloudflare Turnstile 캡차.
게시판: table#bd_lst (XE 기본 게시판 스킨).
게시판 URL: /{mid} (예: /so01, /sjoy0)
게시글 URL: /{mid}/{document_srl}
"""
from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup, Tag

from ...types import Board, Comment, Post, Profile


def _text(el: Tag | None) -> str:
    """태그의 텍스트를 정리하여 반환."""
    if el is None:
        return ""
    return unescape(el.get_text(separator=" ", strip=True))


def _parse_int(s: str) -> int:
    """콤마 포함 숫자 문자열을 int로 변환."""
    nums = re.sub(r"[^\d]", "", s)
    return int(nums) if nums else 0


# ──────────────────────────────────────────────────────────
#  parse_profile
# ──────────────────────────────────────────────────────────

def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출."""
    soup = BeautifulSoup(page_source, "html.parser")

    nickname = ""
    level = 0
    point = 0
    extra: dict[str, str] = {}

    # 닉네임: XE 멤버 링크
    nick_el = soup.select_one("a[class*='member_']")
    if nick_el:
        # 레벨 아이콘 제거 후 텍스트
        for img in nick_el.select("img"):
            img.decompose()
        nickname = _text(nick_el)

    # 레벨: xe_point_level_icon alt 속성 "[레벨:XX]"
    level_icon = soup.select_one("img.xe_point_level_icon")
    if level_icon:
        alt = level_icon.get("alt", "")
        if isinstance(alt, str):
            m = re.search(r"레벨[:\s]*(\d+)", alt)
            if m:
                level = int(m.group(1))
        # 포인트: title 속성 "포인트:XXXpoint"
        title = level_icon.get("title", "")
        if isinstance(title, str):
            m = re.search(r"포인트[:\s]*([\d,]+)", title)
            if m:
                point = _parse_int(m.group(1))

    # 추가 정보 탐색
    for el in soup.select("td, li, div, span"):
        text = _text(el)
        if "게시글" in text or "작성글" in text:
            m = re.search(r"[\d,]+", text)
            if m:
                extra["post_count"] = str(_parse_int(m.group()))
        if "댓글" in text and "수" in text:
            m = re.search(r"[\d,]+", text)
            if m:
                extra["comment_count"] = str(_parse_int(m.group()))

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        extra=extra,
    )


# ──────────────────────────────────────────────────────────
#  parse_boards
# ──────────────────────────────────────────────────────────

def parse_boards(page_source: str) -> list[Board]:
    """네비게이션 메뉴에서 게시판 목록 추출."""
    soup = BeautifulSoup(page_source, "html.parser")
    boards: list[Board] = []
    seen: set[str] = set()

    # ul.gnb 내의 링크들
    for a_tag in soup.select("ul.gnb a[href]"):
        href = a_tag.get("href", "")
        if not isinstance(href, str):
            continue

        # 로그인/회원가입 제외
        if "act=disp" in href or "login" in href.lower() or "signup" in href.lower():
            continue

        # /{mid} 또는 full URL에서 mid 추출
        # XE clean URL: /mid 또는 /index.php?mid=xxx
        board_id = ""
        m = re.search(r"mid=([^&]+)", href)
        if m:
            board_id = m.group(1)
        else:
            # Clean URL: https://domain/mid 또는 /mid
            path = href.rstrip("/")
            if "/" in path:
                last_part = path.rsplit("/", 1)[-1]
                # 숫자만이면 document_srl이므로 건너뛰기
                if last_part and not last_part.isdigit() and not last_part.startswith("index"):
                    board_id = last_part

        if not board_id or board_id in seen:
            continue

        # 부모 li에 active 확인하여 현재 게시판이면 포함
        name = _text(a_tag)
        if not name:
            continue

        seen.add(board_id)
        boards.append(Board(id=board_id, name=name))

    return boards


# ──────────────────────────────────────────────────────────
#  parse_posts
# ──────────────────────────────────────────────────────────

def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 추출.

    XE 기본 게시판 스킨: table#bd_lst > tbody > tr
    컬럼: 제목(td.title), 글쓴이(td.author), 날짜(td.time), 조회수(td.m_no), 추천(td.m_no)
    """
    soup = BeautifulSoup(page_source, "html.parser")
    table = soup.select_one("#bd_lst, table.bd_lst")
    if table is None:
        return []

    posts: list[Post] = []

    for tr in table.select("tbody tr"):
        # 공지글 건너뛰기
        if "notice" in (tr.get("class") or []):
            continue

        # 제목
        title_td = tr.select_one("td.title")
        if not title_td:
            continue

        title_link = title_td.select_one("a[href]")
        if not title_link:
            continue

        subject = _text(title_link)
        href = title_link.get("href", "")

        # document_srl 추출 (post ID)
        post_id = ""
        if isinstance(href, str):
            # Clean URL: /mid/393096246 또는 ?document_srl=393096246
            m = re.search(r"/(\d{5,})", href)
            if m:
                post_id = m.group(1)
            else:
                m = re.search(r"document_srl=(\d+)", href)
                if m:
                    post_id = m.group(1)

        if not post_id:
            continue

        # 댓글 수
        comment_count = 0
        reply_num = title_td.select_one("a.replyNum")
        if reply_num:
            comment_count = _parse_int(_text(reply_num))

        # 글쓴이
        author_td = tr.select_one("td.author")
        author = ""
        if author_td:
            author_link = author_td.select_one("a[class*='member_']")
            if author_link:
                # 레벨 아이콘 제거
                for img in author_link.select("img"):
                    img.decompose()
                author = _text(author_link)
            else:
                author = _text(author_td)

        # 날짜
        time_td = tr.select_one("td.time")
        date = _text(time_td) if time_td else ""

        # 조회수 & 추천: td.m_no (순서대로)
        m_no_tds = tr.select("td.m_no")
        view_count = _parse_int(_text(m_no_tds[0])) if len(m_no_tds) >= 1 else 0

        posts.append(Post(
            id=post_id,
            board_id=board_id,
            subject=subject,
            author=author,
            date=date,
            view_count=view_count,
            comment_count=comment_count,
        ))

    return posts


# ──────────────────────────────────────────────────────────
#  parse_comments
# ──────────────────────────────────────────────────────────

def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    XE 댓글: div.fdb_lst_ul > div 또는 ul.fdb_lst_ul > li 패턴.
    각 댓글에 .fdb_itm 또는 comment 관련 클래스.
    """
    soup = BeautifulSoup(page_source, "html.parser")
    comments: list[Comment] = []

    # XE 댓글 패턴들
    comment_items = soup.select(
        ".fdb_lst_ul li, .fdb_lst_ul > div, "
        ".comment_list li, .cmt_list li, "
        "[id*='comment_'] .comment_item, "
        ".xe_content .comment"
    )

    for i, item in enumerate(comment_items):
        # 작성자
        author_link = item.select_one("a[class*='member_']")
        author = ""
        if author_link:
            for img in author_link.select("img"):
                img.decompose()
            author = _text(author_link)

        # 내용
        content_el = item.select_one(
            ".xe_content, .comment_content, .fdb_lst_cnts, .cmt_content, p"
        )
        content = _text(content_el) if content_el else ""

        # 날짜
        date_el = item.select_one(
            ".date, time, .time, .comment_date"
        )
        date = _text(date_el) if date_el else ""

        if not content:
            continue

        # 댓글 ID
        comment_id = item.get("id", "") or str(i + 1)

        comments.append(Comment(
            id=str(comment_id),
            post_id=post_id,
            author=author,
            content=content,
            date=date,
        ))

    return comments


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
