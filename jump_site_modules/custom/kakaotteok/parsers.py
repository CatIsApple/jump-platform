"""카카오떡 사이트 전용 HTML 파싱 함수.

eyoom gnuboard 테마 기반. gnuboard 공용 APMS 파서를 재활용하되,
eyoom 고유 패턴에 대한 fallback을 추가.
"""
from __future__ import annotations

import re

from ...types import Board, Comment, Post, Profile

# gnuboard 공용 파서 임포트
from ...gnuboard._apms_parsers import (
    parse_boards_apms,
    parse_comments_gnuboard,
    parse_posts_list_item,
    parse_posts_table,
    parse_profile_apms,
)


def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출.

    eyoom 테마 마이페이지 (/mypage/):
    - 닉네임: <strong>테스트닉07</strong> <span>님의 페이지</span>
    - 포인트: info-point 영역 <strong>0</strong>
    - 경험치: info-point 영역 경험치 <strong>3</strong>
    - 레벨: [레벨 1] 또는 progress-info
    - 가입일: info-box-bottom 영역
    """
    # eyoom /mypage/ 패턴 (info-title-name 안에 닉네임)
    nickname = _extract(r'class="info-title-name"[^>]*>\s*<strong>([^<]+)', page_source)
    if not nickname:
        # fallback: "XXX님의 페이지" 패턴
        nickname = _extract(r'<strong>([^<]+)</strong>\s*<span[^>]*>님의 페이지', page_source)
    if not nickname:
        nickname = _extract(r'class="mypage_name[^"]*"[^>]*>([^<]+)', page_source)
    if not nickname:
        nickname = _extract(r'class="nickname[^"]*"[^>]*>([^<]+)', page_source)

    # 레벨: info_level_area "N Lv" → Lv.N.gif → [레벨 N]
    level = ""
    # 1) info_level_area 영역의 "N Lv" 텍스트
    lv_area = _extract(r'class="info_level_area[^"]*"[^>]*>(.*?)</div>', page_source)
    if lv_area:
        level = _extract(r'(\d+)\s*Lv', lv_area)
    # 2) 레벨 이미지: /img/lv/Lv.N.gif 또는 Lv.N.png
    if not level:
        level = _extract(r'<img[^>]+src="[^"]*[/\\]Lv\.(\d+)\.(?:gif|png)', page_source)
    # 3) "N Lv" 텍스트 (페이지 전체)
    if not level:
        level = _extract(r'(\d+)\s*Lv\b', page_source)
    # 4) [레벨 N] 패턴 (다른 테마 호환)
    if not level:
        level = _extract(r'\[레벨\s*(\d+)\]', page_source)

    # 포인트: info-point 영역 "포인트 -" 다음의 <strong>N</strong>
    point = 0
    point_block = _extract(r'class="info-point[^"]*"[^>]*>(.*?)</div>\s*<div\s+class="widht-50', page_source)
    if point_block:
        point = _extract_int(r'<strong>([0-9,]+)</strong>', point_block)
    if not point:
        point = _extract_int(r'포인트[^<]*?(?:<[^>]*>){1,5}\s*<strong>([0-9,]+)', page_source)
    if not point:
        point = _extract_int(r'class="text-crimson"[^>]*><strong>([0-9,]+)', page_source)

    # 경험치
    exp = _extract_int(r'경험치.*?<strong>([0-9,]+)', page_source)

    # 가입일
    join_date = _extract(r'가입일[^:]*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[^<]*)', page_source)

    # post_count / comment_count - eyoom mypage에는 표시 안될 수 있음
    post_count = _extract_int(r'작성글[^<]*?<[^>]*>([0-9,]+)', page_source)
    comment_count = _extract_int(r'작성댓글[^<]*?<[^>]*>([0-9,]+)', page_source)

    if not nickname:
        # APMS 공통 파서 fallback
        profile = parse_profile_apms(page_source)
        if profile.nickname:
            return profile

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards(page_source: str) -> list[Board]:
    """네비게이션에서 bo_table 기반 게시판 목록 추출.

    eyoom gnuboard: 표준 bo_table 링크 + eyoom 커스텀 라우팅.
    카카오떡 특수: nav 링크가 fun_nologin() JS 호출이므로
    <a> href에 bo_table이 없을 수 있음 → 전체 소스 fallback.
    """
    # APMS 공통 파서 (bo_table= 링크)
    boards = parse_boards_apms(page_source)
    if boards:
        return boards

    # eyoom 고유: /board/XXX 형식 라우팅도 체크
    seen: set[str] = set()
    result: list[Board] = []
    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*?/board/([^/"?]+)"[^>]*>\s*'
        r'(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    ):
        board_id = m.group(1).strip()
        name = m.group(2).strip()
        if board_id in seen or not name:
            continue
        seen.add(board_id)
        result.append(Board(id=board_id, name=name))
    if result:
        return result

    # Fallback: 전체 소스에서 bo_table= 패턴 추출
    # JS onclick, hidden input, script 등 모든 곳에서 검색
    skip_tables = {"new", "undefined", "null", ""}
    for m in re.finditer(r'bo_table=([a-zA-Z][a-zA-Z0-9_]{1,30})', page_source):
        bo_table = m.group(1)
        if bo_table in seen or bo_table in skip_tables:
            continue
        seen.add(bo_table)
        # bo_table 주변에서 이름 추출 시도
        name = _find_board_name(page_source, bo_table) or bo_table
        result.append(Board(id=bo_table, name=name))

    return result


def _find_board_name(page_source: str, bo_table: str) -> str:
    """bo_table ID 주변 컨텍스트에서 게시판 이름 추출."""
    # 패턴 1: bo_table=XXX">텍스트</a>
    m = re.search(
        rf'bo_table={re.escape(bo_table)}[^>]*>\s*(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    )
    if m:
        name = m.group(1).strip()
        if name and len(name) < 30:
            return name
    # 패턴 2: bo_table=XXX 가 포함된 줄에서 텍스트
    for line_m in re.finditer(
        rf'[^\n]*bo_table={re.escape(bo_table)}[^\n]*', page_source
    ):
        line = line_m.group(0)
        title_m = re.search(r'title="([^"]+)"', line)
        if title_m:
            return title_m.group(1).strip()
    return ""


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 목록 추출.

    eyoom gnuboard: div.bl-list (/{board_id}/{post_id} URL) 또는
    li.list-item, 테이블 패턴.
    """
    # eyoom bl-list 패턴 (/{board_id}/{post_id}? URL)
    posts = _parse_posts_bl_list(page_source, board_id)
    if posts:
        return posts

    # APMS li.list-item 패턴
    posts = parse_posts_list_item(page_source, board_id)
    if posts:
        return posts

    # gnuboard 테이블 패턴
    posts = parse_posts_table(page_source, board_id)
    if posts:
        return posts

    # eyoom 고유 패턴: div.list-row 또는 div.bo_list
    posts = _parse_posts_eyoom(page_source, board_id)
    return posts


def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    eyoom 댓글 우선 (div#c_NNNNN.view-comment-item) → gnuboard 표준 fallback.
    """
    # eyoom view-comment-item 패턴 (div#c_NNNNN)
    comments = _parse_comments_eyoom_view(page_source, post_id)
    if comments:
        return comments

    # gnuboard 표준 (article#comment_###, li#c_###)
    comments = parse_comments_gnuboard(page_source, post_id)
    if comments:
        return comments

    # eyoom 고유 댓글 패턴 fallback
    return _parse_comments_eyoom(page_source, post_id)


# ─── eyoom 고유 파서 ───


def _parse_comments_eyoom_view(page_source: str, post_id: str = "") -> list[Comment]:
    """eyoom view-comment-item 패턴: div#c_NNNNN.view-comment-item.

    HTML 구조:
    - div#c_23771.view-comment-item
      - span.comment-name > a: 작성자
      - span.comment-time: 시간
      - div.comment-cont-txt: 내용
    """
    comments: list[Comment] = []

    for m in re.finditer(
        r'<div\s+id="c_(\d+)"[^>]*class="[^"]*view-comment-item[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        cmt_html = m.group(2)

        # 작성자: span.comment-name 내 a 태그 텍스트
        author = ""
        author_m = re.search(r'class="comment-name"[^>]*>.*?<a[^>]*>\s*([^<]+)', cmt_html, re.DOTALL)
        if author_m:
            author = author_m.group(1).strip()
        if not author:
            author_m = re.search(r'class="comment-name"[^>]*>([^<]+)', cmt_html)
            if author_m:
                author = author_m.group(1).strip()

        # 날짜: span.comment-time 내 텍스트
        date = ""
        date_m = re.search(r'class="comment-time"[^>]*>.*?(?:<[^>]*>)*\s*([0-9.:/ -]+)', cmt_html, re.DOTALL)
        if date_m:
            date = date_m.group(1).strip()

        # 내용: div.comment-cont-txt
        content = ""
        content_m = re.search(r'class="comment-cont-txt"[^>]*>(.*?)(?:</div>)', cmt_html, re.DOTALL)
        if content_m:
            raw = content_m.group(1)
            raw = re.sub(r'<br\s*/?>', '\n', raw)
            content = re.sub(r'<[^>]+>', '', raw).strip()
            content = re.sub(r'\n{3,}', '\n\n', content)

        if author or content:
            comments.append(Comment(
                id=cmt_id,
                post_id=post_id,
                author=author,
                content=content,
                date=date,
            ))

    # Fallback: looser pattern for div#c_NNNNN without class check
    if not comments:
        for m in re.finditer(
            r'<div\s+id="c_(\d+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            page_source,
            re.DOTALL,
        ):
            cmt_id = m.group(1)
            cmt_html = m.group(2)

            author = ""
            for pat in [
                r'class="comment-name"[^>]*>.*?<a[^>]*>\s*([^<]+)',
                r'class="sv_member"[^>]*>([^<]+)',
                r'class="member"[^>]*>([^<]+)',
            ]:
                am = re.search(pat, cmt_html, re.DOTALL)
                if am:
                    author = am.group(1).strip()
                    break

            content = ""
            for pat in [
                r'class="comment-cont-txt"[^>]*>(.*?)(?:</div>)',
                r'class="cmt_content[^"]*"[^>]*>(.*?)(?:</div>)',
                r'class="cmt_textbox[^"]*"[^>]*>(.*?)(?:</div>)',
            ]:
                cm = re.search(pat, cmt_html, re.DOTALL)
                if cm:
                    raw = cm.group(1)
                    raw = re.sub(r'<br\s*/?>', '\n', raw)
                    content = re.sub(r'<[^>]+>', '', raw).strip()
                    break

            date = ""
            dm = re.search(r'class="comment-time"[^>]*>.*?([0-9.:/ -]+)', cmt_html, re.DOTALL)
            if dm:
                date = dm.group(1).strip()

            if author or content:
                comments.append(Comment(
                    id=cmt_id,
                    post_id=post_id,
                    author=author,
                    content=content,
                    date=date,
                ))

    return comments


def _parse_posts_bl_list(page_source: str, board_id: str = "") -> list[Post]:
    """eyoom bl-list 패턴: div.bl-list + href="/{board_id}/{post_id}?" URL.

    div.bl-list 안에 중첩 div 있어서 split 방식으로 파싱.
    """
    posts: list[Post] = []
    seen: set[str] = set()

    # bl-list 경계로 분할 (중첩 div 문제 회피)
    chunks = re.split(r'<div\s+class="[^"]*\bbl-list\b', page_source)
    for chunk in chunks[1:]:  # 첫 chunk는 bl-list 이전 내용
        # bl-notice 제외
        if "bl-notice" in chunk[:200]:
            continue

        # URL 패턴: href="/{board_id}/{post_id}?" 또는 href="/board/{board_id}/{post_id}"
        link_m = re.search(r'href="/?(\w+)/(\d+)\??"', chunk)
        if not link_m:
            link_m = re.search(r'href="/board/(\w+)/(\d+)', chunk)
        if not link_m:
            # fallback: wr_id= 패턴
            wr_m = re.search(r'wr_id=(\d+)', chunk)
            if wr_m:
                post_id = wr_m.group(1)
                post_board = board_id
            else:
                continue
        else:
            post_board = link_m.group(1)
            post_id = link_m.group(2)

        if post_id in seen:
            continue
        seen.add(post_id)

        # 제목: <span class="subj"> 또는 <a> 내부 텍스트
        subject = ""
        subj_m = re.search(r'<span\s+class="subj"[^>]*>(.*?)</span>', chunk, re.DOTALL)
        if subj_m:
            subject = re.sub(r'<[^>]+>', '', subj_m.group(1)).strip()
        if not subject:
            # <a href="..."> 전체에서 텍스트 추출
            a_m = re.search(r'<a\s+href="[^"]*\d+\??"[^>]*>(.*?)</a>', chunk, re.DOTALL)
            if a_m:
                raw = a_m.group(1)
                raw = re.sub(r'<span\s+class="bl-new-icon"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
                raw = re.sub(r'<span\s+class="bl-comment"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
                subject = re.sub(r'<[^>]+>', '', raw).strip()
                subject = re.sub(r'\s+', ' ', subject).strip()

        # 작성자: bl-name-in 내 a 태그 또는 텍스트
        author = ""
        author_m = re.search(
            r'class="bl-name-in"[^>]*>.*?<a[^>]*>\s*([^<]+)',
            chunk, re.DOTALL,
        )
        if author_m:
            author = author_m.group(1).strip()
        if not author:
            author_m = re.search(r'class="bl-name-in"[^>]*>(?:<[^>]*>)*\s*([^<]+)', chunk)
            if author_m:
                author = author_m.group(1).strip()

        # 날짜: bl-item text-gray 영역
        date = ""
        date_m = re.search(
            r'class="bl-item\s+text-gray"[^>]*>\s*(?:<[^>]*>)*\s*([0-9.:/ -]+)',
            chunk, re.DOTALL,
        )
        if date_m:
            date = date_m.group(1).strip()
        if not date:
            # fred 시간 표시
            date_m = re.search(r'class="fred"[^>]*>([^<]+)', chunk)
            if date_m:
                date = date_m.group(1).strip()

        # 댓글 수
        cmt_m = re.search(r'class="bl-comment"[^>]*>\s*(\d+)', chunk)
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        posts.append(Post(
            id=post_id,
            board_id=post_board if post_board else board_id,
            subject=subject,
            author=author,
            date=date,
            view_count=0,
            comment_count=comment_count,
        ))

    return posts


def _parse_posts_eyoom(page_source: str, board_id: str = "") -> list[Post]:
    """eyoom 테마 게시글 목록 (div 기반)."""
    posts: list[Post] = []
    seen: set[str] = set()

    # eyoom에서 흔히 사용하는 div.eb-flex 또는 div.list-row 패턴
    for m in re.finditer(
        r'<div\s+class="[^"]*(?:list-row|bo_list_row|eb-grid)[^"]*"[^>]*>(.*?)</div>\s*(?:</div>)?',
        page_source,
        re.DOTALL,
    ):
        row_html = m.group(1)
        wr_id_m = re.search(r'wr_id=(\d+)', row_html)
        if not wr_id_m:
            continue
        wr_id = wr_id_m.group(1)
        if wr_id in seen:
            continue
        seen.add(wr_id)

        bo_m = re.search(r'bo_table=(\w+)', row_html)
        post_board_id = bo_m.group(1) if bo_m else board_id

        subject = ""
        for pat in [
            r'class="[^"]*subject[^"]*"[^>]*>\s*(.*?)\s*</a>',
            r'<a[^>]*href="[^"]*wr_id=\d+[^"]*"[^>]*>(.*?)</a>',
        ]:
            subj_m = re.search(pat, row_html, re.DOTALL)
            if subj_m:
                raw = subj_m.group(1)
                raw = re.sub(r'<span\s+class="[^"]*count[^"]*"[^>]*>[^<]*</span>', '', raw)
                subject = re.sub(r'<[^>]+>', '', raw).strip()
                subject = re.sub(r'\s+', ' ', subject).strip()
                break

        author_m = re.search(r'class="[^"]*member[^"]*"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', row_html)
        author = author_m.group(1).strip() if author_m else ""

        date_m = re.search(r'class="[^"]*date[^"]*"[^>]*>\s*(?:<[^>]*>)?\s*([^<]+)', row_html)
        date = date_m.group(1).strip() if date_m else ""

        hit_m = re.search(r'class="[^"]*hit[^"]*"[^>]*>\s*([0-9,]+)', row_html)
        view_count = int(hit_m.group(1).replace(',', '')) if hit_m else 0

        cmt_m = re.search(r'class="[^"]*count[^"]*"[^>]*>\s*(\d+)', row_html)
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

    return posts


def _parse_comments_eyoom(page_source: str, post_id: str = "") -> list[Comment]:
    """eyoom 테마 댓글 (div 기반 fallback)."""
    comments: list[Comment] = []

    # eyoom 댓글: div.comment-item 또는 section.comment
    for m in re.finditer(
        r'<(?:div|section)\s+[^>]*(?:id="comment[_-]?(\d+)"|class="[^"]*comment[_-]?item[^"]*")[^>]*>(.*?)</(?:div|section)>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1) or ""
        cmt_html = m.group(2)

        if not cmt_id:
            id_m = re.search(r'comment[_-]?(\d+)', m.group(0))
            cmt_id = id_m.group(1) if id_m else ""

        author_m = re.search(r'class="[^"]*member[^"]*"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', cmt_html)
        if not author_m:
            author_m = re.search(r'class="[^"]*name[^"]*"[^>]*>([^<]+)', cmt_html)
        author = author_m.group(1).strip() if author_m else ""

        content_m = re.search(r'class="[^"]*(?:cmt_content|comment-text|cmt_textbox)[^"]*"[^>]*>(.*?)</(?:div|p)>', cmt_html, re.DOTALL)
        content = re.sub(r'<[^>]+>', '', content_m.group(1)).strip() if content_m else ""

        date_m = re.search(r'class="[^"]*(?:cmt_date|comment-date|datetime)[^"]*"[^>]*>([^<]+)', cmt_html)
        date = date_m.group(1).strip() if date_m else ""

        if author or content:
            comments.append(Comment(
                id=cmt_id,
                post_id=post_id,
                author=author,
                content=content,
                date=date,
            ))

    return comments


# ─── helpers ───

def _extract(pattern: str, html: str) -> str:
    m = re.search(pattern, html, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_int(pattern: str, html: str) -> int:
    s = _extract(pattern, html)
    if not s:
        return 0
    return int(s.replace(',', ''))
