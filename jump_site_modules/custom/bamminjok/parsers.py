"""밤의민족 사이트 전용 HTML 파싱 함수.

Vue.js + Fastify 기반. Metronic/KT 테마.
게시판: DataTables 서버사이드 렌더링 (#bm_board_datatables).
네비게이션: #kt_header_menu 드롭다운.
게시글 URL 패턴: /board/{board_id}/view/{index}
댓글: #bm_board_view_replylist > div.row, Quill 에디터로 렌더링.
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

    밤의민족 /page/user/user 구조:
      <div class="row mb-7">
        <label class="col-lg-4 fw-bold text-muted">포인트</label>
        <div class="col-lg-8">
          <span class="fw-bolder fs-6 text-gray-800">8,800 포인트</span>
        </div>
      </div>
    랭크 값은 <span class="badge badge-light-dark rank">3</span> 형태.
    """
    soup = BeautifulSoup(page_source, "html.parser")

    nickname = ""
    level = ""
    point = 0
    extra: dict[str, str] = {}

    # label→value 맵 구성 (row.mb-7 구조)
    field: dict[str, str] = {}
    for row in soup.select("div.row.mb-7, div.row"):
        lbl_el = row.select_one("label.col-lg-4, label.fw-bold")
        val_el = row.select_one("div.col-lg-8, div.col-lg-8 span.fw-bolder")
        if not lbl_el or not val_el:
            continue
        lbl = _text(lbl_el).strip()
        val = _text(val_el).strip()
        if lbl:
            field[lbl] = val

    # 닉네임
    nickname = field.get("닉네임", "")

    # 랭크 — badge 숫자 우선, 없으면 field 텍스트에서 추출
    rank_el = soup.select_one("span.badge.rank")
    if rank_el:
        nums = re.findall(r"\d+", _text(rank_el))
        if nums:
            level = nums[0]
    if not level:
        rank_raw = field.get("랭크", "")
        nums = re.findall(r"\d+", rank_raw)
        if nums:
            level = nums[0]

    # 포인트: "8,800 포인트" → 8800
    point_raw = field.get("포인트", "")
    nums = re.findall(r"[\d,]+", point_raw)
    if nums:
        val = _parse_int(nums[0])
        if val < 10_000_000:
            point = val

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

    # #kt_header_menu 내의 /board/ 링크
    menu = soup.select_one("#kt_header_menu")
    if menu is None:
        menu = soup  # fallback: 전체 문서에서 검색

    for a_tag in menu.select("a[href]"):
        href = a_tag.get("href", "")
        if not isinstance(href, str):
            continue
        # /board/{board_id}/list 패턴
        m = re.search(r"/board/([^/]+)/list", href)
        if m:
            board_id = m.group(1)
            if board_id in seen:
                continue
            seen.add(board_id)
            name = _text(a_tag)
            boards.append(Board(id=board_id, name=name or board_id))

    return boards


# ──────────────────────────────────────────────────────────
#  parse_posts
# ──────────────────────────────────────────────────────────

def parse_posts(page_source: str, board_id: str = "") -> list[Post]:
    """게시판 목록 페이지에서 게시글 추출.

    DataTables로 렌더링된 #bm_board_datatables 테이블을 파싱.
    컬럼 순서: #, 제목, 글쓴이, 좋아요, 조회수, 날짜

    글쓴이 컬럼: <span class="badge rank">6</span> 닉네임
    제목 컬럼: <span class="d-inline-block">제목</span> <span class="badge">댓글수</span> <span class="badge">N</span>
    """
    soup = BeautifulSoup(page_source, "html.parser")
    table = soup.select_one("#bm_board_datatables")
    if table is None:
        return []

    posts: list[Post] = []
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if len(tds) < 6:
            continue

        # 첫 번째 컬럼: 게시글 번호 또는 "공지"
        num_text = _text(tds[0])

        # 공지글은 건너뛰기
        post_id = ""
        if num_text.isdigit():
            post_id = num_text
        elif num_text == "공지":
            continue
        else:
            continue

        # 제목: span.d-inline-block 내 텍스트
        title_span = tds[1].select_one("span.d-inline-block")
        subject = _text(title_span) if title_span else _text(tds[1])

        # 댓글 수: badge-light-primary 또는 badge-light-info 내 숫자
        comment_count = 0
        for badge in tds[1].select("span.badge"):
            badge_text = _text(badge)
            if badge_text == "N":
                continue
            if badge_text.isdigit():
                comment_count = int(badge_text)

        # 글쓴이: rank 배지 제외한 텍스트
        author_cell = tds[2]
        # rank 배지 제거 후 텍스트
        for rank_badge in author_cell.select("span.badge.rank"):
            rank_badge.decompose()
        author = _text(author_cell).strip()

        # 조회수: 5번째 컬럼 (index 4)
        view_count = _parse_int(_text(tds[4]))

        # 날짜: 6번째 컬럼 (index 5)
        date = _text(tds[5])

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

    밤의민족 댓글 구조:
    #bm_board_view_replylist > div.row
      .col-12.mb-3:
        span.badge.rank → 레벨
        span.me-2 → 작성자 닉네임
        span.ms-2 → 날짜
      .col-12.ps-5:
        div[id^="quill_reply_content_"] .ql-editor → 댓글 내용
        div[id^="quill_subreply_content_"] .ql-editor → 대댓글 내용

    댓글 ID: quill div id에서 추출 (MongoDB ObjectId)
    """
    soup = BeautifulSoup(page_source, "html.parser")
    comments: list[Comment] = []

    reply_list = soup.select_one("#bm_board_view_replylist")
    if not reply_list:
        return comments

    # 각 댓글 행: div.row (border-bottom 스타일)
    for row in reply_list.select(":scope > div.row"):
        # 작성자: span.me-2
        author_el = row.select_one("span.me-2")
        author = _text(author_el) if author_el else ""

        # 날짜: span.ms-2 (첫 번째 .col-12 내부)
        date_el = row.select_one(".col-12.mb-3 span.ms-2")
        date = _text(date_el) if date_el else ""

        # 댓글 ID 및 내용: Quill 에디터에서 추출
        # 일반 댓글: quill_reply_content_{id}
        # 대댓글 (관리자 자동댓글 등): quill_subreply_content_{id}
        quill_div = row.select_one(
            "div[id^='quill_reply_content_'], div[id^='quill_subreply_content_']"
        )
        if not quill_div:
            continue

        # 내용: .ql-editor 내 텍스트
        editor = quill_div.select_one(".ql-editor")
        content = _text(editor) if editor else ""
        if not content:
            continue

        # ID 추출: quill_reply_content_699c43... → 699c43...
        quill_id = quill_div.get("id", "")
        if isinstance(quill_id, list):
            quill_id = quill_id[0] if quill_id else ""
        cid = re.sub(r"^quill_(sub)?reply_content_", "", str(quill_id))

        # 대댓글 여부
        is_reply = "subreply" in str(quill_id)

        comments.append(Comment(
            id=cid,
            post_id=post_id,
            author=author,
            content=content,
            date=date,
            is_reply=is_reply,
        ))

    return comments


__all__ = ["parse_profile", "parse_boards", "parse_posts", "parse_comments"]
