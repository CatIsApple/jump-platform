"""밤의제국 사이트 전용 HTML 파싱 함수.

bamje48.com: NB-Basic / nariya gnuboard 테마.
- 게시판 목록: nav a[href*="bo_table="]
- 게시글 목록: div.na-item > a.na-subject
- 댓글: section#bo_vc > article[id^="c_"] > div.cmt-wrap
- 프로필: /bbs/userinfo.php 의 form-group row
"""
from __future__ import annotations

import re

from ...types import Board, Comment, Post, Profile


def parse_profile(page_source: str) -> Profile:
    """userinfo.php HTML에서 프로필 정보 추출.

    NB-Basic nariya 테마 구조:
    - 탭: "회원정보 | 내 글 | 파운드 : 100 | 스크랩 | ..."
    - 기본정보: "기본정보 노을빛42(xbmt482)"
    - 가입일: "가입일 2026-02-24 11:55:49"
    - 레벨: level 이미지 또는 tab "레벨정보"
    """
    # 닉네임: col-form-info 안의 "닉네임(아이디)" 패턴
    # HTML: <div class="col-sm-3 col-form-info"> <img ...>&nbsp;노을빛42(xbmt482) </div>
    nickname = ""
    m = re.search(
        r'col-form-info"[^>]*>\s*(?:<[^>]*>)*\s*(?:&nbsp;)?\s*([^(<\s][^(<]*?)\s*\(',
        page_source,
    )
    if m:
        nickname = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        nickname = nickname.replace('&nbsp;', '').strip()
    if not nickname:
        # fallback: sidebar sv_member 링크
        m = re.search(r'class="sv_member"[^>]*>(?:<[^>]*>)*\s*([^<]+)', page_source)
        if m:
            nickname = m.group(1).strip()

    # 포인트: "파운드 : NNN" 또는 "파운드 NNN" 또는 "포인트 NNN"
    point = 0
    m = re.search(r'파운드\s*[:\s]*<b>([0-9,]+)</b>', page_source)
    if m:
        point = int(m.group(1).replace(',', ''))
    else:
        m = re.search(r'(?:파운드|포인트)\s*[:\s]*([0-9,]+)', page_source)
        if m:
            point = int(m.group(1).replace(',', ''))

    # 레벨: level 이미지 파일명 (e.g. /level/2.png → "2")
    # 주의: 일부 이미지 파일명이 실제 레벨이 아닌 ID일 수 있음 (e.g. 1022.png)
    level = ""
    m = re.search(r'<img[^>]+src="[^"]*level[^"]*?/(\w+)\.png"', page_source)
    if m:
        raw = m.group(1)
        # 숫자이고 20 이하인 경우만 유효한 레벨로 취급
        if raw.isdigit() and int(raw) <= 20:
            level = raw
    if not level:
        m = re.search(r'Lv\.?\s*(\d+)', page_source)
        if m:
            level = m.group(1)

    # 작성글/댓글 수 (nariya 마이페이지에 미표시)
    post_count = 0
    comment_count = 0

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards(page_source: str) -> list[Board]:
    """메인/네비게이션에서 게시판 목록 추출.

    NB-Basic nariya: nav 드롭다운에 bo_table= 링크.
    """
    boards: list[Board] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*>(.*?)</a>',
        page_source,
        re.DOTALL,
    ):
        bo_table = m.group(1).strip()
        name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        # 공백/개행 정리
        name = re.sub(r'\s+', ' ', name).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))
    return boards


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 목록 추출.

    NB-Basic nariya 테마:
      <div class="na-item">
        <div class="num-cell ..."><span>82622</span></div>
        <a href="...?bo_table=greeting&wr_id=102418" class="na-subject">
          <span class="na-icon na-new"></span>
          가입합니다
        </a>
        <div class="na-info">
          <span class="count-plus orangered">3</span>
        </div>
      </div>

    참고: 목록에 작성자/날짜 미표시 (na-item에 없음).
    """
    posts: list[Post] = []
    seen: set[str] = set()

    # na-item 블록 분리
    for m in re.finditer(
        r'<div\s+class="na-item"[^>]*>(.*?)</div>\s*(?=<div\s+class="na-item"|</section|</div\s*>\s*</div\s*>\s*</div)',
        page_source,
        re.DOTALL,
    ):
        item_html = m.group(1)
        _parse_na_item(item_html, board_id, posts, seen)

    # fallback: 더 관대한 패턴
    if not posts:
        # na-subject 링크만으로 파싱
        for m in re.finditer(
            r'<a\s+href="[^"]*bo_table=(\w+)&(?:amp;)?wr_id=(\d+)"[^>]*class="na-subject"[^>]*>(.*?)</a>',
            page_source,
            re.DOTALL,
        ):
            post_board = m.group(1)
            wr_id = m.group(2)
            if wr_id in seen:
                continue
            seen.add(wr_id)
            raw_subject = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            subject = re.sub(r'\s+', ' ', raw_subject).strip()
            posts.append(Post(
                id=wr_id,
                board_id=post_board or board_id,
                subject=subject,
                author="",
                date="",
                view_count=0,
                comment_count=0,
            ))

    return posts


def _parse_na_item(item_html: str, board_id: str, posts: list[Post], seen: set[str]):
    """단일 na-item HTML에서 Post 추출."""
    # wr_id
    wr_m = re.search(r'wr_id=(\d+)', item_html)
    if not wr_m:
        return
    wr_id = wr_m.group(1)
    if wr_id in seen:
        return
    seen.add(wr_id)

    # bo_table
    bo_m = re.search(r'bo_table=(\w+)', item_html)
    post_board = bo_m.group(1) if bo_m else board_id

    # 제목: na-subject 안의 텍스트 (태그 제거)
    subj_m = re.search(r'class="na-subject"[^>]*>(.*?)</a>', item_html, re.DOTALL)
    subject = ""
    if subj_m:
        raw = subj_m.group(1)
        subject = re.sub(r'<[^>]+>', '', raw).strip()
        subject = re.sub(r'\s+', ' ', subject).strip()

    # 댓글수: count-plus
    cmt_m = re.search(r'class="count-plus[^"]*"[^>]*>\s*(\d+)', item_html)
    comment_count = int(cmt_m.group(1)) if cmt_m else 0

    posts.append(Post(
        id=wr_id,
        board_id=post_board,
        subject=subject,
        author="",
        date="",
        view_count=0,
        comment_count=comment_count,
    ))


def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    NB-Basic nariya 테마:
      <section id="bo_vc">
        <article id="c_296371">
          <div class="cmt-wrap ...">
            <header>
              <div class="clearfix ...">
                <ul>
                  <li> <a class="sv_member">bblue001</a> </li>
                  <li> <time datetime="2025-11-02T17:36:57+09:00">2025.11.02 17:36</time> </li>
                </ul>
              </div>
            </header>
            <div class="cmt-content p-3">댓글 내용</div>
          </div>
        </article>
      </section>
    """
    comments: list[Comment] = []

    # article[id^="c_"] 경계 분할
    boundaries = list(re.finditer(r'<article\s+id="c_(\d+)"', page_source))
    for i, bm in enumerate(boundaries):
        cmt_id = bm.group(1)
        start = bm.start()
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else start + 5000
        cmt_html = page_source[start:end]

        # 작성자: a.sv_member 텍스트
        author_m = re.search(r'class="sv_member"[^>]*>(?:<[^>]*>)*\s*([^<]+)', cmt_html)
        author = author_m.group(1).strip() if author_m else ""

        # 날짜: <time> 태그
        date_m = re.search(r'<time[^>]*>([^<]+)</time>', cmt_html)
        date = date_m.group(1).strip() if date_m else ""

        # 내용: div.cmt-content
        content_m = re.search(r'class="cmt-content[^"]*"[^>]*>(.*?)</div>', cmt_html, re.DOTALL)
        content = ""
        if content_m:
            content = re.sub(r'<[^>]+>', '', content_m.group(1)).strip()

        if not author and not content:
            continue

        comments.append(Comment(
            id=cmt_id,
            post_id=post_id,
            author=author,
            content=content,
            date=date,
        ))

    # fallback: div.media#c_### (APMS Basic 호환)
    if not comments:
        for m in re.finditer(r'id="c_(\d+)"', page_source):
            cmt_id = m.group(1)
            start = m.start()
            end_m = re.search(r'id="c_\d+"', page_source[start + 1:])
            end = start + 1 + end_m.start() if end_m else start + 5000
            cmt_html = page_source[start:end]

            author_m = re.search(r'class="(?:sv_member|member)"[^>]*>(?:<[^>]*>)*\s*([^<]+)', cmt_html)
            author = author_m.group(1).strip() if author_m else ""

            content_m = re.search(
                rf'<textarea\s+id="save_comment_{re.escape(cmt_id)}"[^>]*>(.*?)</textarea>',
                cmt_html, re.DOTALL,
            )
            content = content_m.group(1).strip() if content_m else ""

            date_m = re.search(r'<time[^>]*>([^<]+)</time>', cmt_html)
            if not date_m:
                date_m = re.search(r'class="media-info"[^>]*>(.*?)</span>', cmt_html, re.DOTALL)
            date = re.sub(r'<[^>]+>', '', date_m.group(1)).strip() if date_m else ""

            if not author and not content:
                continue

            comments.append(Comment(
                id=cmt_id, post_id=post_id, author=author, content=content, date=date,
            ))

    return comments


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
