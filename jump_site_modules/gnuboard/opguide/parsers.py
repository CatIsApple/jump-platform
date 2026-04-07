"""오피가이드 사이트 전용 HTML 파싱 함수.

APMS Op 테마 기반 gnuboard. 커스텀 레이아웃.
- 게시판 목록: nav의 a[href*="bo_table="] + .sub-1da 서브메뉴
- 게시글 목록(업체): tr.partner > td.list-subject > a.list_op_title
- 게시글 목록(커뮤): li.list-item > a.item-subject
- 댓글: section#bo_vc > div.media#c_XXXX (textarea#save_comment_XXXX)
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ...types import Board, Comment, Post, Profile


def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출.

    오피가이드 mypage.php 테이블 구조:
      <tr class="active">
        <th>현재레벨</th><th>가입일</th><th>보유 포인트</th>
        <th>후기 작성개수</th><th>게시글 작성개수</th><th>댓글 작성개수</th>
      </tr>
      <tr>
        <th><img ...> 이병 Lv.2</th>
        <td> 5 일</td><td> 621P</td><td> 0개</td><td> 1개</td><td> 10개</td>
      </tr>
    """
    nickname = _extract(r"<title>(.+?)님\s*마이", page_source)
    if not nickname:
        nickname = _extract(r'class="member"[^>]*>(?:<[^>]*>)*\s*([^<]+)', page_source)
    if not nickname:
        nickname = _extract(r'닉네임[^<]*<[^>]*>\s*([^<]+)', page_source)

    # 레벨/포인트/게시글/댓글: 테이블 헤더-값 매칭으로 추출
    level, point, post_count, comment_count = None, 0, 0, 0
    table_m = re.search(
        r'<tr\s+class="active">\s*(.*?)</tr>\s*<tr>\s*(.*?)</tr>',
        page_source, re.DOTALL,
    )
    if table_m:
        headers = re.findall(r'<th[^>]*>(.*?)</th>', table_m.group(1), re.DOTALL)
        # 값 행: 첫 번째는 <th> (레벨 셀), 나머지는 <td>
        value_row = table_m.group(2)
        values = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', value_row, re.DOTALL)
        header_names = [re.sub(r'<[^>]+>', '', h).strip() for h in headers]
        value_texts = [re.sub(r'<[^>]+>', '', v).strip() for v in values]

        for i, hdr in enumerate(header_names):
            if i >= len(value_texts):
                break
            val = value_texts[i]
            if '레벨' in hdr:
                # "이병 Lv.2" → 2
                lv_m = re.search(r'Lv\.?\s*(\d+)', val)
                level = lv_m.group(1) if lv_m else None
            elif '포인트' in hdr:
                point = _parse_num(val)
            elif '게시글' in hdr:
                post_count = _parse_num(val)
            elif '댓글' in hdr:
                comment_count = _parse_num(val)

    # 테이블에서 레벨 못 찾은 경우 폴백
    if not level:
        level = _extract(r"Lv\.?\s*(\d+)", page_source)

    # 테이블 파싱 실패 시 폴백
    if not point:
        point = _extract_int(r"포인트\s*([0-9,]+)", page_source)
    if not point:
        point = _extract_int(r"보유\s*포인트[^0-9]*([0-9,]+)", page_source)

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards(page_source: str) -> list[Board]:
    """네비게이션/사이드바에서 게시판 목록 추출.

    오피가이드 nav 구조:
      <a href="/bbs/board.php?bo_table=freetalk" class="sub-1da">
        <i class="fa fa-users"></i> 자유게시판
      </a>
      <a class="menu-a" href="/bbs/board.php?bo_table=chulsuk">커뮤니티</a>
    """
    boards: list[Board] = []
    seen: set[str] = set()

    # sub-1da 링크 먼저 (하위 메뉴, 가장 정확)
    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*class="[^"]*sub-1da[^"]*"[^>]*>'
        r'\s*(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    ):
        bo_table = m.group(1).strip()
        name = m.group(2).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))

    # menu-a 링크 (상위 메뉴) - sub-1da에 없는 것만
    for m in re.finditer(
        r'<a\s+[^>]*class="[^"]*menu-a[^"]*"[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*>'
        r'\s*(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    ):
        bo_table = m.group(1).strip()
        name = m.group(2).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))

    # href 속성이 class 앞에 오는 패턴 (menu-a)
    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*class="[^"]*menu-a[^"]*"[^>]*>'
        r'\s*(?:<[^>]*>)*\s*([^<]+)',
        page_source,
    ):
        bo_table = m.group(1).strip()
        name = m.group(2).strip()
        if bo_table in seen or not name:
            continue
        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))

    # 일반 bo_table 링크 (btn-nav 등) - 위에서 안 잡힌 것만
    if not boards:
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


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 목록 추출.

    오피가이드는 두 가지 게시판 레이아웃 사용:

    1) 업체 게시판 (op_partner_posting):
       <tr class="partner premiumtop">
         <td class="wide40 list-subject">
           <a class="list_op_title" href="...wr_id=123&...">
             <span class="list_op_head">[지역-업체명]</span> 설명...
             <span class="cnt_cmt">726</span>
           </a>
         </td>
         <td class="list_name"><b><a ...><span class="member">업체명</span></a></b></td>
       </tr>

    2) 커뮤니티 게시판 (freetalk 등):
       <li class="list-item">
         <div class="wr-num hidden-xs">64349</div>
         <div class="wr-subject">
           <a href="...wr_id=XXX..." class="item-subject">
             [카테고리] 제목
             <span class="count orangered hidden-xs">9</span>
           </a>
         </div>
         <div class="wr-name hidden-xs">
           <a ...><span class="member">... 닉네임</span></a>
         </div>
         <div class="wr-date hidden-xs">17:42</div>
         <div class="wr-hit hidden-xs">143</div>
       </li>
    """
    posts: list[Post] = []

    # 패턴 1: 업체 게시판 (tr.partner)
    for m in re.finditer(
        r'<tr\s+class="partner[^"]*"[^>]*>(.*?)</tr>',
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

        # 제목: list_op_head 안의 텍스트 + 나머지 title_area 텍스트
        subj_m = re.search(
            r'class="list_op_title"[^>]*>(.*?)</a>',
            row_html,
            re.DOTALL,
        )
        subject = ""
        if subj_m:
            raw = subj_m.group(1)
            # 댓글수/sound_only span 제거
            raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
            raw = re.sub(r'<span\s+class="sound_only"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
            subject = re.sub(r'<[^>]+>', '', raw).strip()
            subject = re.sub(r'\s+', ' ', subject).strip()

        # 작성자: td.list_name > b > a > span.member 내부 텍스트
        author = _extract_member_name(row_html)

        # 댓글수
        cmt_m = re.search(r'class="cnt_cmt"[^>]*>(\d+)', row_html)
        comment_count = int(cmt_m.group(1)) if cmt_m else 0

        posts.append(Post(
            id=wr_id,
            board_id=post_board_id,
            subject=subject,
            author=author,
            comment_count=comment_count,
        ))

    # 패턴 2: 커뮤니티 게시판 (li.list-item - APMS 표준)
    if not posts:
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

            # 제목: a.item-subject 내부 텍스트 (댓글수 span 제거)
            subj_m = re.search(r'class="item-subject"[^>]*>\s*(.*?)\s*</a>', item_html, re.DOTALL)
            subject = ""
            if subj_m:
                raw = subj_m.group(1)
                # 모바일 댓글 span 제거
                raw = re.sub(r'<span\s+class="[^"]*wr-comment[^"]*"[^>]*>.*?</span>', '', raw, flags=re.DOTALL)
                # 데스크탑 댓글수 span 제거
                raw = re.sub(r'<span\s+class="count[^"]*"[^>]*>[^<]*</span>', '', raw)
                subject = re.sub(r'<[^>]+>', '', raw).strip()
                subject = re.sub(r'\s+', ' ', subject).strip()

            # 작성자: div.wr-name > a > span.member 내부 텍스트
            # wr-name 영역만 추출 (hidden-xs 첫번째가 작성자)
            name_m = re.search(r'<div\s+class="wr-name[^"]*"[^>]*>(.*?)</div>', item_html, re.DOTALL)
            author = ""
            if name_m:
                author = _extract_member_name(name_m.group(1))

            # 날짜
            date_m = re.search(r'<div\s+class="wr-date[^"]*"[^>]*>\s*(?:<[^>]*>)*\s*([^<]+)', item_html)
            date = date_m.group(1).strip() if date_m else ""

            # 조회수: 첫번째 wr-hit
            hit_m = re.search(r'<div\s+class="wr-hit[^"]*"[^>]*>\s*([0-9,]+)', item_html)
            view_count = int(hit_m.group(1).replace(',', '')) if hit_m else 0

            # 댓글수: span.count
            cmt_m = re.search(r'<span\s+class="count[^"]*"[^>]*>\s*(\d+)', item_html)
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

    # wr_id 중복 제거
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

    오피가이드 댓글 구조 (APMS Op 테마):
      <section id="bo_vc" class="comment-media">
        <div class="media" id="c_2532">
          <div class="media-body">
            <div class="media-heading">
              <b><a onclick="showSideView(this, 'userid', '닉네임', ...)">
                <span class="member">... 닉네임</span>
              </a></b>
              <span class="media-info">
                <i class="fa fa-clock-o"></i>
                2017.12.03 22:26
              </span>
            </div>
            <div class="media-content">
              텍스트 내용
              <textarea id="save_comment_2532" style="display:none">텍스트 내용</textarea>
            </div>
          </div>
        </div>
        ...
      </section>
    """
    comments: list[Comment] = []

    # div.media#c_XXXX 패턴으로 각 댓글 추출
    for m in re.finditer(
        r'<div\s+class="media"\s+id="c_(\d+)"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        cmt_html = m.group(2)

        # 작성자: span.member 내부 텍스트
        author = _extract_member_name(cmt_html)

        # 내용: textarea#save_comment_XXXX (가장 정확)
        content_m = re.search(
            r'<textarea\s+id="save_comment_' + re.escape(cmt_id) + r'"[^>]*>(.*?)</textarea>',
            page_source,
            re.DOTALL,
        )
        content = ""
        if content_m:
            content = content_m.group(1).strip()
        else:
            # 폴백: div.media-content 텍스트
            mc_m = re.search(r'class="media-content"[^>]*>(.*?)<(?:span|textarea)', cmt_html, re.DOTALL)
            if mc_m:
                content = re.sub(r'<[^>]+>', '', mc_m.group(1)).strip()

        # 날짜: span.media-info 안의 텍스트
        date_m = re.search(
            r'class="media-info"[^>]*>\s*(?:<[^>]*>)*\s*([0-9]{4}\.[0-9]{2}\.[0-9]{2}\s+[0-9]{2}:[0-9]{2})',
            cmt_html,
        )
        date = date_m.group(1).strip() if date_m else ""

        if not content and not author:
            continue

        comments.append(Comment(
            id=cmt_id,
            post_id=post_id,
            author=author,
            content=content,
            date=date,
        ))

    return comments


# ─── helpers ───

def _extract_member_name(html: str) -> str:
    """span.member 내부에서 실제 닉네임 텍스트 추출.

    HTML 구조:
      <a ...><span class="member">
        <span style="..."></span>
        <img src=".../m2.png"> 닉네임
      </span></a>

    member span은 항상 </span></a> 로 닫힌다.
    닉네임은 member span 안의 마지막 텍스트 노드.
    """
    # member span ~ </span></a> 범위로 한정 (greedy가 다른 </span>으로 넘어가지 않도록)
    member_m = re.search(r'class="member"[^>]*>(.*?)</span>\s*</a>', html, re.DOTALL)
    if not member_m:
        # 폴백: </span></b> 패턴
        member_m = re.search(r'class="member"[^>]*>(.*?)</span>\s*</b>', html, re.DOTALL)
    if not member_m:
        return ""

    inner = member_m.group(1)
    # > 뒤의 텍스트 노드 전부 찾아서 마지막 비어있지 않은 것
    texts = re.findall(r'>([^<]+)', inner)
    for t in reversed(texts):
        t = t.strip()
        if t:
            return t
    return ""


def _parse_num(text: str) -> int:
    """'621P', '10개', '5 일' 등에서 숫자만 추출."""
    m = re.search(r'([0-9,]+)', text)
    return int(m.group(1).replace(',', '')) if m else 0


def _extract(pattern: str, html: str) -> str:
    m = re.search(pattern, html, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_int(pattern: str, html: str) -> int:
    s = _extract(pattern, html)
    if not s:
        return 0
    return int(s.replace(',', ''))


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
