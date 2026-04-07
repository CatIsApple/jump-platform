"""오피매니아 사이트 전용 HTML 파싱 함수.

opstar 커스텀 테마 기반 gnuboard.
- 게시판 목록: <a href="...?bo_table=XXX">게시판명</a> 패턴
- 게시글 목록: <table class="list"> tbody tr 테이블 형식
- 댓글: <table class="comment"> tr.reply_tr (data-idx, data-level)
- 프로필: /bbs/mypage.php 회원 정보 영역
"""
from __future__ import annotations

import re

from ...types import Board, Comment, Post, Profile


def parse_profile(page_source: str) -> Profile:
    """마이페이지 HTML에서 프로필 정보 추출.

    opstar 테마 마이페이지:
    - 닉네임: <title>OOO님 마이페이지</title> 또는 user_nick attr
    - 레벨: level 이미지 파일명 level_S_X.gif / level_S_1_X.gif
    - 포인트: 보유포인트 숫자
    """
    nickname = _extract(r"<title>(.+?)님\s*마이페이지", page_source)
    if not nickname:
        nickname = _extract(r'class="user_nick"[^>]*nick="([^"]*)"', page_source)
    if not nickname:
        # toolbar 로그아웃 근처 닉네임: "OOO님" 패턴
        nickname = _extract(r'class="log"[^>]*>.*?([^<>\s]+)\s*님', page_source)
    if not nickname:
        # mb_nick hidden input
        nickname = _extract(r'name="mb_nick"[^>]*value="([^"]*)"', page_source)
    if not nickname:
        # 마이페이지 닉네임 영역
        nickname = _extract(r'닉네임[^<]*<[^>]*>\s*([^<]+)', page_source)

    # 레벨: 현재 레벨까지 이미지가 순서대로 표시됨 (마지막 이미지 = 현재 레벨)
    # 레벨 1: level_S_1_1.gif / 레벨 2: ..._1_2.gif / 레벨 3: ..._1_3.gif
    # → 마지막으로 등장하는 이미지의 두 번째 숫자 그룹이 실제 레벨
    level_m = None
    for m in re.finditer(r'level_[SL]_\d+_(\d+)\.gif', page_source):
        level_m = m  # 마지막 매치
    if level_m:
        level = level_m.group(1)
    else:
        level = _extract(r'level_[SL]_(\d+)\.gif', page_source)
    if not level:
        level = _extract(r"Lv\.?\s*(\d+)", page_source)

    # opstar 테마: <span class="point">point</span><span class="point_count">110</span>
    point = _extract_int(r'class="point_count"[^>]*>\s*([0-9,]+)', page_source)
    if not point:
        point = _extract_int(r"포인트[^<]*?<[^>]*>\s*([0-9,]+)", page_source)
    if not point:
        point = _extract_int(r"보유\s*포인트[^0-9]*([0-9,]+)", page_source)
    if not point:
        point = _extract_int(r"포인트.*?([0-9,]+)\s*(?:점|P)", page_source)

    # opstar 테마: "업체 후기" / "업체 댓글" + <td class="mp_num">N</td>
    post_count = _extract_int(r"업체\s*후기.*?mp_num[^>]*>\s*([0-9,]+)", page_source)
    if not post_count:
        post_count = _extract_int(r"작성글[^<]*?<[^>]*>\s*([0-9,]+)", page_source)
    comment_count = _extract_int(r"업체\s*댓글.*?mp_num[^>]*>\s*([0-9,]+)", page_source)
    if not comment_count:
        comment_count = _extract_int(r"작성댓글[^<]*?<[^>]*>\s*([0-9,]+)", page_source)

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
    )


def parse_boards(page_source: str) -> list[Board]:
    """페이지에서 게시판 목록 추출.

    <a href="/bbs/board.php?bo_table=notice">공지사항</a> 패턴.
    wr_id 가 포함된 개별 게시글 링크는 제외.
    """
    boards: list[Board] = []
    seen: set[str] = set()

    for m in re.finditer(
        r'<a\s+[^>]*href="[^"]*[?&]bo_table=([^"&]+)"[^>]*>(.*?)</a>',
        page_source,
        re.DOTALL,
    ):
        full_match = m.group(0)
        # 개별 게시글 링크 제외
        if "wr_id=" in full_match:
            continue

        bo_table = m.group(1).strip()
        name = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        # HTML 엔티티 디코딩
        name = name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')

        # 빈 이름, 숫자만, 페이지네이션 기호 제외
        if not name or re.match(r"^[\d<>]+$", name):
            continue
        if bo_table in seen:
            continue

        seen.add(bo_table)
        boards.append(Board(id=bo_table, name=name))

    return boards


def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 목록 추출.

    opstar 테마 <table class="list">:
      <tr class="notice">
        <td>Notice</td>
        <td class="title"><a href="...?bo_table=X&wr_id=Y">제목 <font color="red">+68</font></a></td>
        <td class="ansor"><img ...><a class="user_nick" nick="관리자"> 관리자</a></td>
        <td><font color="red">67</font></td>   ← 추천수
        <td>66,374</td>                        ← 조회수
      </tr>
    """
    posts: list[Post] = []

    for m in re.finditer(
        r"<tr(?:\s+class=\"notice\")?[^>]*>(.*?)</tr>",
        page_source,
        re.DOTALL,
    ):
        row_html = m.group(1)

        # 헤더 행 제외
        if "<th>" in row_html or "<th " in row_html:
            continue

        # wr_id 추출
        wr_id_m = re.search(r"wr_id=(\d+)", row_html)
        if not wr_id_m:
            continue
        wr_id = wr_id_m.group(1)

        # bo_table 추출
        bo_m = re.search(r"bo_table=(\w+)", row_html)
        post_board_id = bo_m.group(1) if bo_m else board_id

        # 제목: td.title 안 텍스트 (font 태그=댓글수 제거)
        title_m = re.search(
            r'<td\s+class="title"[^>]*>(.*?)</td>', row_html, re.DOTALL
        )
        subject = ""
        comment_count = 0
        if title_m:
            title_html = title_m.group(1)
            # 댓글수: <font color="red">+68</font>
            cmt_m = re.search(r"<font[^>]*>\+(\d+)</font>", title_html)
            if cmt_m:
                comment_count = int(cmt_m.group(1))
            # 태그 제거 → 순수 텍스트
            subject = re.sub(r"<font[^>]*>[^<]*</font>", "", title_html)
            subject = re.sub(r"<img[^>]*>", "", subject)
            subject = re.sub(r"<[^>]+>", "", subject).strip()
            subject = re.sub(r"\s+", " ", subject).strip()

        # 작성자: td.ansor 안 user_nick
        author = ""
        author_m = re.search(
            r'class="user_nick"[^>]*nick="([^"]*)"', row_html
        )
        if author_m:
            author = author_m.group(1).strip()
        if not author:
            author_m2 = re.search(
                r'class="user_nick"[^>]*>\s*(?:<[^>]*>)?\s*([^<]+)',
                row_html,
            )
            if author_m2:
                author = author_m2.group(1).strip()

        # 조회수: 마지막 td (콤마 포함 숫자)
        tds = list(re.finditer(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL))
        view_count = 0
        if tds:
            last_text = re.sub(r"<[^>]+>", "", tds[-1].group(1)).strip()
            last_text = last_text.replace(",", "")
            if last_text.isdigit():
                view_count = int(last_text)

        posts.append(
            Post(
                id=wr_id,
                board_id=post_board_id,
                subject=subject,
                author=author,
                date="",  # opstar 테마에 날짜 컬럼 없음
                view_count=view_count,
                comment_count=comment_count,
            )
        )

    # wr_id 중복 제거 (마지막 것 유지)
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

    opstar 테마 <table class="comment">:
      <tr class="reply_tr" data-idx="3380" data-seq="1" data-level="0">
        <td><table><tbody><tr>
          <td></td>
          <td><a class="user_nick" nick="강남짝녀"><img ...> 강남짝녀</a></td>
          <td class="content"><pre>확인 <span class="reply_btn">↳[댓글쓰기]</span></pre></td>
          <td><span>12-27 14:28</span></td>
        </tr></tbody></table></td>
      </tr>
    """
    comments: list[Comment] = []

    for m in re.finditer(
        r'<tr\s+class="reply_tr"\s+data-idx="(\d+)"\s+data-seq="\d+"\s+data-level="(\d+)"[^>]*>(.*?)</tr>\s*(?=<tr[\s>]|</tbody|</table)',
        page_source,
        re.DOTALL,
    ):
        cmt_id = m.group(1)
        level = int(m.group(2))
        cmt_html = m.group(3)

        # 작성자: nick 속성 우선
        author = ""
        nick_m = re.search(r'class="user_nick"[^>]*nick="([^"]*)"', cmt_html)
        if nick_m:
            author = nick_m.group(1).strip()
        if not author:
            nick_m2 = re.search(
                r'class="user_nick"[^>]*>\s*(?:<img[^>]*>)?\s*([^<]+)',
                cmt_html,
            )
            if nick_m2:
                author = nick_m2.group(1).strip()

        # 내용: <pre> 태그에서 reply_btn span 제거
        content = ""
        pre_m = re.search(r"<pre>(.*?)</pre>", cmt_html, re.DOTALL)
        if pre_m:
            raw = pre_m.group(1)
            raw = re.sub(
                r'<span\s+class="reply_btn"[^>]*>.*?</span>',
                "",
                raw,
                flags=re.DOTALL,
            )
            content = re.sub(r"<[^>]+>", "", raw).strip()

        # 날짜: 마지막 td 안 <span>
        date = ""
        date_m = re.search(
            r"<td[^>]*>\s*<span>([^<]+)</span>\s*</td>", cmt_html
        )
        if date_m:
            date = date_m.group(1).strip()

        if not author and not content:
            continue

        # 대댓글 부모 찾기: level > 0 이면 직전 댓글을 부모로
        parent_id = ""
        if level > 0 and comments:
            parent_id = comments[-1].id

        comments.append(
            Comment(
                id=cmt_id,
                post_id=post_id,
                parent_id=parent_id if level > 0 else "",
                author=author,
                content=content,
                date=date,
                is_reply=level > 0,
            )
        )

    return comments


# ─── helpers ───


def _extract(pattern: str, html: str) -> str:
    m = re.search(pattern, html, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_int(pattern: str, html: str) -> int:
    s = _extract(pattern, html)
    if not s:
        return 0
    return int(s.replace(",", ""))


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
