"""오피아트 사이트 전용 HTML 파싱 함수.

gnuboard 기반, fetch API 점프.
게시글: li.list-item 또는 tbody tr 패턴.
댓글: 다양한 gnuboard/eyoom 패턴 지원.
"""
from __future__ import annotations

import re

from .._apms_parsers import (
    parse_boards_apms as parse_boards,
    parse_comments_gnuboard,
    parse_posts_list_item,
    parse_posts_table,
    parse_profile_apms as parse_profile,
)
from ...types import Comment, Post


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 추출."""
    posts = parse_posts_list_item(page_source, board_id)
    if not posts:
        posts = parse_posts_table(page_source, board_id)
    return posts


def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    여러 gnuboard/eyoom 댓글 HTML 패턴을 순서대로 시도:
    1. article#comment_### (표준 gnuboard)
    2. li#c_### (APMS 변형)
    3. div#c_### (eyoom/커스텀 테마)
    4. div.view-comment-item (eyoom)
    5. section.comment / div.cmt_item
    """
    # 패턴 1+2: 표준 gnuboard (article#comment_###, li#c_###)
    comments = parse_comments_gnuboard(page_source, post_id)
    if comments:
        return comments

    # 패턴 3: div.media#c_XXXX (APMS Op 테마 - opguide와 동일)
    for m in re.finditer(
        r'<div\s+class="media"\s+id="c_(\d+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        cmt_html = m.group(2)
        # textarea#save_comment_XXXX에서 내용 추출 (가장 정확)
        author = ""
        member_m = re.search(r'class="member"[^>]*>(.*?)</span>\s*</a>', cmt_html, re.DOTALL)
        if not member_m:
            member_m = re.search(r'class="member"[^>]*>(.*?)</span>\s*</b>', cmt_html, re.DOTALL)
        if member_m:
            texts = re.findall(r'>([^<]+)', member_m.group(1))
            for t in reversed(texts):
                t = t.strip()
                if t:
                    author = t
                    break
        if not author:
            author_m = re.search(r'class="member"[^>]*>(?:<[^>]*>)*\s*([^<]+)', cmt_html, re.DOTALL)
            if author_m:
                author = author_m.group(1).strip()

        content = ""
        tc_m = re.search(
            r'<textarea\s+id="save_comment_' + re.escape(cmt_id) + r'"[^>]*>(.*?)</textarea>',
            page_source, re.DOTALL,
        )
        if tc_m:
            content = tc_m.group(1).strip()
        else:
            mc_m = re.search(r'class="media-content"[^>]*>(.*?)<(?:span|textarea|div)', cmt_html, re.DOTALL)
            if mc_m:
                content = re.sub(r'<[^>]+>', '', mc_m.group(1)).strip()

        date_m = re.search(
            r'class="media-info"[^>]*>\s*(?:<[^>]*>)*\s*([0-9]{2,4}[.\-/][0-9]{1,2}[.\-/][0-9]{1,2}\s+[0-9]{2}:[0-9]{2})',
            cmt_html,
        )
        date = date_m.group(1).strip() if date_m else ""

        if author or content:
            comments.append(Comment(id=cmt_id, post_id=post_id, author=author, content=content, date=date))
    if comments:
        return comments

    # 패턴 4: div#c_### (eyoom 및 커스텀 테마)
    for m in re.finditer(
        r'<div\s+id="c_(\d+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        cmt_html = m.group(2)
        c = _extract_comment_fields(cmt_id, cmt_html, post_id)
        if c:
            comments.append(c)
    if comments:
        return comments

    # 패턴 5: div.view-comment-item (eyoom)
    for m in re.finditer(
        r'<div[^>]*class="[^"]*view-comment-item[^"]*"[^>]*>(.*?)</div>\s*</div>',
        page_source,
        re.DOTALL,
    ):
        cmt_html = m.group(1)
        cmt_id_m = re.search(r'id="c_(\d+)"', m.group(0))
        cmt_id = cmt_id_m.group(1) if cmt_id_m else ""
        c = _extract_comment_fields(cmt_id, cmt_html, post_id)
        if c:
            comments.append(c)
    if comments:
        return comments

    # 패턴 6: section#bo_vc 내 댓글 영역
    bo_vc_m = re.search(r'<section\s+id="bo_vc"[^>]*>(.*?)</section>', page_source, re.DOTALL)
    if bo_vc_m:
        vc_html = bo_vc_m.group(1)
        for m in re.finditer(
            r'<(?:div|article|li)\s+[^>]*(?:id="(?:comment_|c_|cmt_)(\d+)"|class="[^"]*(?:cmt_item|comment-item|comment_list_item)[^"]*")[^>]*>(.*?)</(?:div|article|li)>',
            vc_html,
            re.DOTALL,
        ):
            cmt_id = m.group(1) or ""
            cmt_html = m.group(2)
            c = _extract_comment_fields(cmt_id, cmt_html, post_id)
            if c:
                comments.append(c)

    return comments


def _extract_comment_fields(cmt_id: str, cmt_html: str, post_id: str) -> Comment | None:
    """댓글 HTML 블록에서 작성자/내용/날짜 추출."""
    author = ""
    for pat in [
        r'class="comment-name"[^>]*>.*?<a[^>]*>\s*([^<]+)',
        r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)',
        r'class="sv_member"[^>]*>([^<]+)',
        r'class="[^"]*cmt_name[^"]*"[^>]*>(?:<[^>]*>)*\s*([^<]+)',
        r'class="[^"]*name[^"]*"[^>]*>(?:<[^>]*>)*\s*([^<]+)',
    ]:
        am = re.search(pat, cmt_html, re.DOTALL)
        if am:
            author = am.group(1).strip()
            if author:
                break

    content = ""
    for pat in [
        r'class="comment-cont-txt"[^>]*>(.*?)(?:</div>)',
        r'class="cmt_contents[^"]*"[^>]*>(.*?)</div>',
        r'class="cmt_textbox[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*comment-text[^"]*"[^>]*>(.*?)</(?:div|p)>',
        r'class="[^"]*cmt_content[^"]*"[^>]*>(.*?)</div>',
    ]:
        cm = re.search(pat, cmt_html, re.DOTALL)
        if cm:
            raw = cm.group(1)
            raw = re.sub(r'<br\s*/?>', '\n', raw)
            content = re.sub(r'<[^>]+>', '', raw).strip()
            if content:
                break

    date = ""
    for pat in [
        r'class="comment-time"[^>]*>.*?([0-9]{2,4}[./-][0-9]{1,2}[./-][0-9]{1,2}[^<]*)',
        r'class="cmt_date[^"]*"[^>]*>([^<]+)',
        r'datetime[^>]*>([^<]+)',
        r'class="[^"]*date[^"]*"[^>]*>([^<]+)',
    ]:
        dm = re.search(pat, cmt_html, re.DOTALL)
        if dm:
            date = dm.group(1).strip()
            if date:
                break

    if author or content:
        return Comment(id=cmt_id, post_id=post_id, author=author, content=content, date=date)
    return None


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
