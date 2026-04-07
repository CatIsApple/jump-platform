"""오피마트 사이트 전용 HTML 파싱 함수.

커스텀 PHP 기반. AJAX 로그인/점프.
게시판: table.list (번호, 제목, 글쓴이, 추천수, 조회수).
게시판 URL: /bbs/board_list.php?type={board_type}
게시글 URL: /bbs/board_read.php?type={board_type}&idx={id}
댓글: table.comment > tr.reply_tr[data-idx], content in td.content > pre
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
    """마이페이지 HTML에서 프로필 정보 추출.

    /member/mypage_user_info.php 페이지 구조:
    - 닉네임: .id_box > span.name
    - 레벨: .level_ico > img[src*='level_L_'] (큰 이미지)
    - 포인트: span.point_count
    - 활동: .mp_box > .mp_num (쪽지함, 업체댓글, 업체후기)
    """
    soup = BeautifulSoup(page_source, "html.parser")

    nickname = ""
    level = ""
    point = 0
    post_count = 0
    comment_count = 0
    extra: dict[str, str] = {}

    # 닉네임: .id_box > span.name (마이페이지 상단)
    nick_el = soup.select_one(".id_box span.name")
    if not nick_el:
        # fallback: input#user_nick (정보수정 폼)
        nick_input = soup.select_one("input#user_nick")
        if nick_input:
            nickname = nick_input.get("value", "") or ""
            if isinstance(nickname, list):
                nickname = nickname[0] if nickname else ""
        else:
            # 최종 fallback
            nick_el = soup.select_one("a.user_nick, .my_nick, .nick")
            if nick_el:
                nickname = _text(nick_el)
    else:
        nickname = _text(nick_el)

    # 레벨: 현재 레벨까지의 이미지가 순서대로 표시됨
    # 레벨 1: [level_L_1_1.gif]
    # 레벨 2: [level_L_1_1.gif, level_L_1_2.gif]
    # 레벨 3: [level_L_1_1.gif, level_L_1_2.gif, level_L_1_3.gif]
    # → 마지막 이미지의 두 번째 숫자 그룹이 실제 레벨
    level_imgs = soup.select(".level_ico img[src*='level_']")
    if not level_imgs:
        level_imgs = soup.select("img[src*='level_L_'], img[src*='level_S_']")
    if level_imgs:
        src = level_imgs[-1].get("src", "")  # 마지막 이미지 = 현재 레벨
        if isinstance(src, str):
            m = re.search(r"level_[LS]_\d+_(\d+)", src)
            if m:
                level = m.group(1)
            else:
                m2 = re.search(r"level_[LS]_(\d+)", src)
                if m2:
                    level = m2.group(1)

    # 포인트: span.point_count
    pt_el = soup.select_one("span.point_count")
    if pt_el:
        point = _parse_int(_text(pt_el))

    # 활동 수치: .mp_box 내부 .mp_menu 와 .mp_num
    for mp_box in soup.select(".mp_box"):
        menu_text = _text(mp_box.select_one(".mp_menu"))
        num_el = mp_box.select_one(".mp_num")
        num_val = _parse_int(_text(num_el)) if num_el else 0
        if "댓글" in menu_text:
            comment_count = num_val
        elif "후기" in menu_text:
            extra["review_count"] = str(num_val)

    # ID: .id_box > span.id
    id_el = soup.select_one(".id_box span.id")
    if id_el:
        extra["user_id"] = _text(id_el)

    return Profile(
        nickname=nickname,
        level=level,
        point=point,
        post_count=post_count,
        comment_count=comment_count,
        extra=extra,
    )


# ──────────────────────────────────────────────────────────
#  parse_boards
# ──────────────────────────────────────────────────────────

def parse_boards(page_source: str) -> list[Board]:
    """네비게이션 및 페이지에서 게시판 목록 추출.

    board_list.php?type=... 링크 + 알려진 게시판 fallback.
    커뮤니티 탭(/bbs/board_list.php?type=free&ol=y)에서 접근 가능한 게시판들.
    """
    soup = BeautifulSoup(page_source, "html.parser")
    boards: list[Board] = []
    seen: set[str] = set()

    # board_list.php?type=... 패턴
    for a_tag in soup.select("a[href*='board_list.php']"):
        href = a_tag.get("href", "")
        if not isinstance(href, str):
            continue
        m = re.search(r"type=([^&]+)", href)
        if m:
            board_id = m.group(1)
            if board_id in seen:
                continue
            seen.add(board_id)
            name = _text(a_tag) or board_id
            boards.append(Board(id=board_id, name=name))

    # 알려진 게시판 추가 (네비가 이미지 기반이라 텍스트 없을 수 있음)
    known = {
        "notice": "공지사항",
        "free": "자유게시판",
        "levelup": "등업게시판",
        "member": "회원게시판",
        "ssul": "썰게시판",
    }
    for bid, bname in known.items():
        if bid not in seen:
            boards.append(Board(id=bid, name=bname))
            seen.add(bid)

    return boards


# ──────────────────────────────────────────────────────────
#  parse_posts
# ──────────────────────────────────────────────────────────

def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 추출.

    table.list > tbody > tr 파싱.
    컬럼: 번호, 제목, 글쓴이, 추천수, 조회수
    Notice 행(tr.notice): td 첫번째가 "Notice" → 공지글 스킵
    일반 행: 첫번째 td > a[href*='idx='] → 게시글 번호 및 ID
    """
    soup = BeautifulSoup(page_source, "html.parser")
    table = soup.select_one("table.list")
    if table is None:
        return []

    posts: list[Post] = []

    for tr in table.select("tbody tr"):
        # Notice 행 스킵
        if "notice" in (tr.get("class") or []):
            continue

        tds = tr.select("td")
        if len(tds) < 3:
            continue

        # 첫 번째 컬럼: 게시글 번호 (a 태그 안에 있음)
        num_text = _text(tds[0]).strip()
        if num_text.lower() == "notice" or num_text == "공지":
            continue

        post_id = ""
        # 번호 컬럼의 a 태그에서 idx 추출
        num_link = tds[0].select_one("a[href*='idx=']")
        if num_link:
            href = num_link.get("href", "")
            if isinstance(href, str):
                m = re.search(r"idx=(\d+)", href)
                if m:
                    post_id = m.group(1)
        if not post_id and num_text.isdigit():
            post_id = num_text
        if not post_id:
            # 제목 링크에서 시도
            title_link = tr.select_one("td.title a[href*='idx=']")
            if title_link:
                href = title_link.get("href", "")
                if isinstance(href, str):
                    m = re.search(r"idx=(\d+)", href)
                    if m:
                        post_id = m.group(1)
            if not post_id:
                continue

        # 제목: td.title > a
        title_el = tr.select_one("td.title a")
        subject = _text(title_el) if title_el else ""
        # rep_cnt 제거 (댓글 수가 제목에 붙어있을 수 있음)
        rep_cnt_el = tr.select_one("td.title span.rep_cnt")
        if rep_cnt_el and subject:
            rep_text = _text(rep_cnt_el)
            subject = subject.replace(rep_text, "").strip()

        # 댓글 수: span.rep_cnt "[15]" 패턴
        comment_count = 0
        if rep_cnt_el:
            m = re.search(r"\[(\d+)\]", _text(rep_cnt_el))
            if m:
                comment_count = int(m.group(1))

        # 글쓴이: td.ansor > a.user_nick
        author_el = tr.select_one("td.ansor a.user_nick")
        author = _text(author_el) if author_el else ""
        if not author:
            ansor = tr.select_one("td.ansor")
            if ansor:
                author = _text(ansor)

        # 추천수와 조회수: 마지막 두 td
        view_count = 0
        if len(tds) >= 5:
            view_count = _parse_int(_text(tds[4]))
        elif len(tds) >= 4:
            view_count = _parse_int(_text(tds[3]))

        posts.append(Post(
            id=post_id,
            board_id=board_id,
            subject=subject,
            author=author,
            date="",
            view_count=view_count,
            comment_count=comment_count,
        ))

    return posts


# ──────────────────────────────────────────────────────────
#  parse_comments
# ──────────────────────────────────────────────────────────

def parse_comments(page_source: str, post_id: str = "") -> list[Comment]:
    """게시글 상세 페이지에서 댓글 추출.

    오피마트 댓글 구조:
    table.comment > tbody > tr.reply_tr[data-idx][data-seq][data-level]
      내부 테이블:
        td > a.user_nick → 작성자
        td.content > pre → 댓글 내용
    """
    soup = BeautifulSoup(page_source, "html.parser")
    comments: list[Comment] = []

    # tr.reply_tr 패턴 (실제 HTML 구조)
    comment_rows = soup.select("table.comment tr.reply_tr")

    for row in comment_rows:
        # data-idx: 댓글 고유 ID
        cid = row.get("data-idx", "")
        if isinstance(cid, list):
            cid = cid[0] if cid else ""
        cid = str(cid)

        # data-level: 대댓글 여부
        level = row.get("data-level", "0")
        if isinstance(level, list):
            level = level[0] if level else "0"
        is_reply = str(level) != "0"

        # 작성자: a.user_nick
        author_el = row.select_one("a.user_nick")
        author = _text(author_el) if author_el else ""

        # 내용: td.content > pre (reply_frm_area 제거 후)
        content_td = row.select_one("td.content")
        if content_td:
            # reply_frm_area 제거 (댓글쓰기 폼 영역)
            for frm_area in content_td.select(".reply_frm_area"):
                frm_area.decompose()
            content_el = content_td.select_one("pre")
            if content_el is None:
                content_el = content_td
        else:
            content_el = None
        content = _text(content_el) if content_el else ""
        # "↳[댓글쓰기]" 등 잔여 텍스트 정리
        content = re.sub(r"↳\[댓글쓰기\]", "", content).strip()

        if not content:
            continue

        comments.append(Comment(
            id=cid,
            post_id=post_id,
            author=author,
            content=content,
            date="",
            is_reply=is_reply,
        ))

    return comments


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
