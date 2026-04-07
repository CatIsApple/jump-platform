"""공용 APMS 테마 HTML 파싱 함수.

대부분의 gnuboard 사이트가 APMS 테마를 사용하므로 공통 파서를 여기에 정의.
사이트별 parsers.py에서 import하여 사용하거나 오버라이드.

지원 패턴:
1. APMS Basic (li.list-item): hellobam, opview, lybam, bamje, opnara, oplove 등
2. APMS Op (tr.partner): opguide 등
3. gnuboard table (tbody tr td): opmania 등
"""
from __future__ import annotations

import re

from ..types import Board, Comment, Post, Profile


def parse_profile_apms(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출 (APMS 공통)."""
    nickname = _extract(r'<b class="name"[^>]*>([^<]+)</b>', page_source)
    if not nickname:
        nickname = _extract(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', page_source)
    if not nickname:
        nickname = _extract(r'class="sv_member"[^>]*>([^<]+)', page_source)

    level = _extract(r'<img[^>]+src="[^"]*level[^"]*?/(\w+)\.png', page_source)
    point = _extract_int(r'포인트[^<]*?<[^>]*>([0-9,]+)', page_source)
    post_count = _extract_int(r'작성글[^<]*?<[^>]*>([0-9,]+)', page_source)
    comment_count = _extract_int(r'작성댓글[^<]*?<[^>]*>([0-9,]+)', page_source)

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards_apms(page_source: str) -> list[Board]:
    """네비게이션에서 bo_table 기반 게시판 목록 추출 (APMS 공통)."""
    boards: list[Board] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*>\s*'
        r'(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    ):
        bo_table = m.group(1).strip()
        name = m.group(2).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))
    return boards


def parse_posts_list_item(page_source: str, board_id: str = "") -> list[Post]:
    """li.list-item 기반 게시글 목록 추출 (APMS Basic 계열).

    hellobam, opview, lybam, bamje, opnara, oplove 등에서 사용.
    """
    posts: list[Post] = []

    for m in re.finditer(
        r'<li\s+class="list-item[^"]*"[^>]*>(.*?)</li>',
        page_source,
        re.DOTALL,
    ):
        item_html = m.group(1)

        wr_id_m = re.search(r'wr_id=(\d+)', item_html)
        if not wr_id_m:
            continue
        wr_id = wr_id_m.group(1)

        bo_m = re.search(r'bo_table=(\w+)', item_html)
        post_board_id = bo_m.group(1) if bo_m else board_id

        # 제목
        subj_m = re.search(
            r'class="item-subject"[^>]*>\s*(.*?)\s*</a>',
            item_html,
            re.DOTALL,
        )
        subject = ""
        if subj_m:
            raw = subj_m.group(1)
            raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>[^<]*</span>', '', raw)
            raw = re.sub(r'<span\s+class="[^"]*wr-comment[^"]*"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
            subject = re.sub(r'<[^>]+>', '', raw).strip()
            subject = re.sub(r'\s+', ' ', subject).strip()

        # 작성자
        author_m = re.search(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', item_html)
        author = author_m.group(1).strip() if author_m else ""

        # 날짜
        date_m = re.search(r'class="wr-date[^"]*"[^>]*>\s*(?:<[^>]*>)?\s*([^<]+)', item_html)
        date = date_m.group(1).strip() if date_m else ""

        # 조회수
        hit_m = re.search(r'class="wr-hit[^"]*"[^>]*>\s*([0-9,]+)', item_html)
        view_count = int(hit_m.group(1).replace(',', '')) if hit_m else 0

        # 댓글수
        cmt_m = re.search(r'class="count[^"]*"[^>]*>\s*(\d+)', item_html)
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        posts.append(Post(
            id=wr_id,
            board_id=post_board_id,
            subject=subject,
            author=author,
            date=date,
            view_count=view_count,
            comment_count=comment_count,
        ))

    return _dedup_posts(posts)


def parse_posts_table(page_source: str, board_id: str = "") -> list[Post]:
    """tbody tr 기반 게시글 목록 추출 (gnuboard 표준 테이블 계열).

    opmania 등 #flogin 기반 사이트에서 사용.
    """
    posts: list[Post] = []

    for m in re.finditer(
        r'<tr[^>]*>(.*?)</tr>',
        page_source,
        re.DOTALL,
    ):
        row_html = m.group(1)

        wr_id_m = re.search(r'wr_id=(\d+)', row_html)
        if not wr_id_m:
            continue
        wr_id = wr_id_m.group(1)

        bo_m = re.search(r'bo_table=(\w+)', row_html)
        post_board_id = bo_m.group(1) if bo_m else board_id

        # 제목 - 여러 패턴 시도
        subject = ""
        for pat in [
            r'class="bo_tit"[^>]*>\s*(.*?)\s*</a>',
            r'class="item-subject"[^>]*>\s*(.*?)\s*</a>',
            r'<td\s+class="[^"]*td_subject[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>',
        ]:
            subj_m = re.search(pat, row_html, re.DOTALL)
            if subj_m:
                raw = subj_m.group(1)
                raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>[^<]*</span>', '', raw)
                subject = re.sub(r'<[^>]+>', '', raw).strip()
                subject = re.sub(r'\s+', ' ', subject).strip()
                break

        # 제목 fallback: 첫 번째 a 태그
        if not subject:
            a_m = re.search(r'<a[^>]*href="[^"]*wr_id=\d+[^"]*"[^>]*>(.*?)</a>', row_html, re.DOTALL)
            if a_m:
                raw = a_m.group(1)
                raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>[^<]*</span>', '', raw)
                subject = re.sub(r'<[^>]+>', '', raw).strip()
                subject = re.sub(r'\s+', ' ', subject).strip()

        # 작성자
        author_m = re.search(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', row_html)
        if not author_m:
            author_m = re.search(r'class="sv_member"[^>]*>([^<]+)', row_html)
        author = author_m.group(1).strip() if author_m else ""

        # 날짜
        date_m = re.search(r'class="[^"]*td_date[^"]*"[^>]*>\s*([^<]+)', row_html)
        if not date_m:
            date_m = re.search(r'class="wr-date[^"]*"[^>]*>\s*(?:<[^>]*>)?\s*([^<]+)', row_html)
        date = date_m.group(1).strip() if date_m else ""

        # 조회수
        hit_m = re.search(r'class="[^"]*td_num_po[^"]*"[^>]*>\s*([0-9,]+)', row_html)
        if not hit_m:
            hit_m = re.search(r'class="wr-hit[^"]*"[^>]*>\s*([0-9,]+)', row_html)
        view_count = int(hit_m.group(1).replace(',', '')) if hit_m else 0

        # 댓글수
        cmt_m = re.search(r'class="cnt_cmt"[^>]*>(\d+)', row_html)
        if not cmt_m:
            cmt_m = re.search(r'class="count[^"]*"[^>]*>\s*(\d+)', row_html)
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        posts.append(Post(
            id=wr_id,
            board_id=post_board_id,
            subject=subject,
            author=author,
            date=date,
            view_count=view_count,
            comment_count=comment_count,
        ))

    return _dedup_posts(posts)


def parse_comments_gnuboard(page_source: str, post_id: str = "") -> list[Comment]:
    """gnuboard 표준 댓글 추출 (모든 테마 공통).

    패턴 1: article#comment_### (표준)
    패턴 2: li#c_### (APMS 변형)
    """
    comments: list[Comment] = []

    # 패턴 1
    for m in re.finditer(
        r'<article\s+id="comment_(\d+)"[^>]*>(.*?)</article>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        cmt_html = m.group(2)
        comments.append(_parse_comment_html(cmt_id, cmt_html, post_id))

    # 패턴 2 (fallback)
    if not comments:
        for m in re.finditer(
            r'<li\s+[^>]*id="c_(\d+)"[^>]*>(.*?)</li>',
            page_source,
            re.DOTALL,
        ):
            cmt_id = m.group(1)
            cmt_html = m.group(2)
            comments.append(_parse_comment_html(cmt_id, cmt_html, post_id))

    return comments


# ─── internal helpers ───

def _parse_comment_html(cmt_id: str, cmt_html: str, post_id: str) -> Comment:
    author_m = re.search(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', cmt_html)
    if not author_m:
        author_m = re.search(r'class="sv_member"[^>]*>([^<]+)', cmt_html)
    author = author_m.group(1).strip() if author_m else ""

    content_m = re.search(r'class="cmt_contents[^"]*"[^>]*>(.*?)</div>', cmt_html, re.DOTALL)
    if not content_m:
        content_m = re.search(r'class="cmt_textbox[^"]*"[^>]*>(.*?)</div>', cmt_html, re.DOTALL)
    content = re.sub(r'<[^>]+>', '', content_m.group(1)).strip() if content_m else ""

    date_m = re.search(r'class="cmt_date[^"]*"[^>]*>([^<]+)', cmt_html)
    if not date_m:
        date_m = re.search(r'datetime[^>]*>([^<]+)', cmt_html)
    date = date_m.group(1).strip() if date_m else ""

    return Comment(id=cmt_id, post_id=post_id, author=author, content=content, date=date)


def _dedup_posts(posts: list[Post]) -> list[Post]:
    """wr_id 기준 중복 제거 (마지막 것 유지)."""
    seen: dict[str, int] = {}
    unique: list[Post] = []
    for p in posts:
        if p.id in seen:
            unique[seen[p.id]] = p
        else:
            seen[p.id] = len(unique)
            unique.append(p)
    return unique


def _extract(pattern: str, html: str) -> str:
    m = re.search(pattern, html, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_int(pattern: str, html: str) -> int:
    s = _extract(pattern, html)
    if not s:
        return 0
    return int(s.replace(',', ''))
