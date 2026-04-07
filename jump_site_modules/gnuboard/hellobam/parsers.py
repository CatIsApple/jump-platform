"""헬로밤 사이트 전용 HTML 파싱 함수.

APMS Basic 테마 (Bootstrap 3) 기반 gnuboard.
- 게시판 목록: header .header-mn ul li a[href*="bo_table="]
- 게시글 목록: li.list-item > .wr-subject a.item-subject
- 댓글: section.comment ul#comment_list li (gnuboard 표준)
- 프로필: /bbs/mypage.php 의 회원 정보 영역
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from ...types import Board, Comment, Post, Profile


def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출.

    APMS Basic 테마 마이페이지 구조:
    - 닉네임: <title>OOO님 마이페이지</title> 또는 <span class="member"><img ...> OOO</span>
    - 레벨: level 이미지 파일명 (/img/level/.../7.png) 또는 Lv.N 텍스트
    - 포인트: 보유포인트 <span>14,000 점</span>
    - 작성글/댓글 수: 마이페이지에 미표시 (0 반환)
    """
    # 닉네임: title 태그 "OOO님 마이페이지" 패턴
    nickname = _extract(r'<title>(.+?)님 마이페이지', page_source)
    if not nickname:
        # fallback: member span (img 태그 건너뛰기)
        nickname = _extract(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)</span>', page_source)

    # 레벨: level 이미지 파일명 숫자
    level = _extract(r'<img[^>]+src="[^"]*level[^"]*?/(\w+)\.png"', page_source)
    if not level:
        level = _extract(r'Lv\.(\d+)', page_source)

    # 포인트
    point = _extract_int(r'포인트[^<]*?<[^>]*>([0-9,]+)', page_source)

    # 작성글/댓글 수 (APMS Basic 마이페이지에 미표시, 0 반환)
    post_count = _extract_int(r'작성글[^<]*?<[^>]*>([0-9,]+)', page_source)
    comment_count = _extract_int(r'작성댓글[^<]*?<[^>]*>([0-9,]+)', page_source)

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards(page_source: str) -> list[Board]:
    """메인/네비게이션 header에서 게시판 목록 추출.

    헬로밤 header-mn:
      <li><a href="/bbs/board.php?bo_table=review"><span>실사후기</span></a></li>
    """
    boards: list[Board] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a\s+href="[^"]*[?&]bo_table=([^"&]+)"[^>]*>(.*?)</a>',
        page_source,
        re.DOTALL,
    ):
        bo_table = m.group(1).strip()
        # 내부 HTML에서 모든 태그 제거 → 순수 텍스트
        name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))
    return boards


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 목록 추출.

    헬로밤 APMS Basic:
      <li class="list-item">
        <div class="wr-num hidden-xs">112611</div>
        <div class="wr-subject">
          <a href="...?bo_table=review&wr_id=294214" class="item-subject">
            제목
            <span class="count orangered hidden-xs">1</span>  ← 댓글수
          </a>
        </div>
        <div class="wr-name hidden-xs"><span class="member">작성자</span></div>
        <div class="wr-date hidden-xs">02.20</div>
        <div class="wr-hit hidden-xs">123</div>
      </li>
    """
    posts: list[Post] = []

    # 정규(비-베스트) 목록만 추출: id가 없는 .list-body 내의 list-item
    # best 섹션은 id="bestmonth", "bestweek", "bestday"
    # 정규 목록은 <div class="list-board"> 아래 id 없는 <ul class="list-body">
    # → 안전하게 모든 list-item에서 파싱하되 best 중복은 wr_id로 제거

    for m in re.finditer(
        r'<li\s+class="list-item[^"]*"[^>]*>(.*?)</li>',
        page_source,
        re.DOTALL,
    ):
        item_html = m.group(1)

        # wr_id 추출
        wr_id_m = re.search(r'wr_id=(\d+)', item_html)
        if not wr_id_m:
            continue
        wr_id = wr_id_m.group(1)

        # bo_table 추출
        bo_m = re.search(r'bo_table=(\w+)', item_html)
        post_board_id = bo_m.group(1) if bo_m else board_id

        # 제목 추출
        subj_m = re.search(
            r'class="item-subject"[^>]*>\s*(.*?)\s*</a>',
            item_html,
            re.DOTALL,
        )
        subject = ""
        if subj_m:
            raw = subj_m.group(1)
            # span.count (댓글수), span.wr-comment, span.wr-icon 등 내용 포함 태그 제거
            raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>[^<]*</span>', '', raw)
            raw = re.sub(r'<span\s+class="[^"]*wr-comment[^"]*"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
            # 나머지 태그 제거
            subject = re.sub(r'<[^>]+>', '', raw).strip()
            # 연속 공백 정리
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

    # wr_id 중복 제거 (best와 일반 목록 겹침 방지) - 마지막 것 유지
    seen: dict[str, int] = {}
    unique: list[Post] = []
    for p in posts:
        if p.id in seen:
            unique[seen[p.id]] = p
        else:
            seen[p.id] = len(unique)
            unique.append(p)
    return unique


def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    헬로밤 APMS Basic 실제 구조:
      <div class="media" id="c_854">
        <div class="media-body">
          <div class="media-heading">
            <span class="member"><img ...> 작성자</span>
            <span class="media-info">02.16 16:51</span>
          </div>
          <div class="media-content">댓글 내용</div>
        </div>
      </div>
    """
    comments: list[Comment] = []

    # 패턴 1: div.media#c_### (헬로밤 APMS Basic 실제 구조)
    # 경계 분할: id="c_\d+"로 각 댓글 블록 분리
    boundaries = list(re.finditer(r'id="c_(\d+)"', page_source))
    for i, bm in enumerate(boundaries):
        cmt_id = bm.group(1)
        start = bm.start()
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else start + 5000
        cmt_html = page_source[start:end]

        # 작성자: span.member 안의 텍스트
        author_m = re.search(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', cmt_html)
        author = author_m.group(1).strip() if author_m else ""

        # 댓글 내용: textarea#save_comment_{id} (gnuboard 원문 저장용)
        content_m = re.search(
            rf'<textarea\s+id="save_comment_{re.escape(cmt_id)}"[^>]*>(.*?)</textarea>',
            cmt_html, re.DOTALL,
        )
        content = content_m.group(1).strip() if content_m else ""

        # 날짜: span.media-info 안의 텍스트
        date_m = re.search(r'class="media-info"[^>]*>(.*?)</span>', cmt_html, re.DOTALL)
        date = ""
        if date_m:
            date = re.sub(r'<[^>]+>', '', date_m.group(1)).strip()

        if not author and not content:
            continue

        comments.append(Comment(
            id=cmt_id,
            post_id=post_id,
            author=author,
            content=content,
            date=date,
        ))

    # 패턴 2: article#comment_### (gnuboard 표준)
    if not comments:
        for m in re.finditer(
            r'<article\s+id="comment_(\d+)"[^>]*>(.*?)</article>',
            page_source,
            re.DOTALL,
        ):
            cmt_id = m.group(1)
            cmt_html = m.group(2)

            author_m = re.search(r'class="member"[^>]*>(?:<img[^>]*>)?\s*([^<]+)', cmt_html)
            author = author_m.group(1).strip() if author_m else ""

            content_m = re.search(r'class="cmt_contents[^"]*"[^>]*>(.*?)</div>', cmt_html, re.DOTALL)
            if not content_m:
                content_m = re.search(r'class="cmt_textbox[^"]*"[^>]*>(.*?)</div>', cmt_html, re.DOTALL)
            content = re.sub(r'<[^>]+>', '', content_m.group(1)).strip() if content_m else ""

            date_m = re.search(r'class="cmt_date[^"]*"[^>]*>([^<]+)', cmt_html)
            date = date_m.group(1).strip() if date_m else ""

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
