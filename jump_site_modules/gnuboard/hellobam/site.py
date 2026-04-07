"""HellobamSite - 헬로밤."""

from __future__ import annotations

import re
import time
from typing import Any

from ...base import STATUS_COOLDOWN, STATUS_SUCCESS, STATUS_UNKNOWN
from ...gnuboard_base import GnuboardSite
from ...types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    Profile,
    WriteResult,
)


class HellobamSite(GnuboardSite):
    SITE_NAME = "헬로밤"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_SUBMIT_SELECTOR = "button[type='submit'].btn_submit"

    def _navigate_to_login(self) -> None:
        """직접 로그인 페이지 이동 (LINK_TEXT 방식 제거)."""
        super()._navigate_to_login()

    def _fill_login_form(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        self.handle_alert(accept=True, timeout=1.0)

        WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id, input[name='mb_id']"))
        )

        # JS 기반 폼 입력 (더 안정적)
        self.driver.execute_script("""
            var id = document.querySelector('#login_id') || document.querySelector('input[name="mb_id"]');
            var pw = document.querySelector('#login_pw') || document.querySelector('input[name="mb_password"]');
            if (id) { id.value = arguments[0]; id.dispatchEvent(new Event('input', {bubbles:true})); }
            if (pw) { pw.value = arguments[1]; pw.dispatchEvent(new Event('input', {bubbles:true})); }
        """, self.username, self.password)
        time.sleep(0.3)

        # 제출
        try:
            self.driver.find_element(By.CSS_SELECTOR, "button.btn_submit[type='submit'], input.btn_submit[type='submit']").click()
        except Exception:
            self.driver.execute_script("""
                var f = document.querySelector('form[name="flogin"]');
                if (f) { var btn = f.querySelector('button[type="submit"], input[type="submit"]'); if (btn) btn.click(); else f.submit(); }
            """)

    def jump(self) -> JumpResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        result_text = ""
        try:
            # sidebar open
            element = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@href=\"javascript:sidebar_open('sidebar-user');\"]")
                )
            )
            element.click()
            time.sleep(0.4)

            # click jump button
            jpbtn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#jumpbtn"))
            )
            jpbtn.click()
            time.sleep(0.5)

            # SweetAlert confirm
            try:
                confirm_button = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(@class, 'swal2-confirm')]")
                    )
                )
                confirm_button.click()
            except Exception:
                pass

            time.sleep(1.5)

            # read result SweetAlert
            result_text = self.handle_swal(click_confirm=True)

        except Exception as exc:
            # SweetAlert already shown
            result_text = self.handle_swal(click_confirm=True)
            if not result_text:
                return JumpResult(status="failed", message=f"점프 실행 실패: {exc}")

        self.save_cookies(self.COOKIE_KEYS)
        status, msg = self.classify_result(result_text)

        if status == STATUS_UNKNOWN:
            timer = self.wait_for_countdown(timeout=4.0)
            if timer:
                return JumpResult(status=STATUS_COOLDOWN, message=timer)

        return JumpResult(status=status, message=msg)

    # ──────────────────────────────────────────────
    #  0. 프로필
    # ──────────────────────────────────────────────

    def get_profile(self) -> Profile:
        """로그인된 계정의 마이페이지 프로필 정보 가져오기."""
        from . import parsers

        self.driver.get(f"{self.base_url}/bbs/mypage.php")
        time.sleep(0.5)
        self.require_human_check()
        return parsers.parse_profile(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """헬로밤 회원가입.

        kwargs:
            mb_id: 아이디
            mb_password: 비밀번호
            mb_name: 이름
            mb_nick: 닉네임
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_name = kwargs.get("mb_name", "")
        mb_nick = kwargs.get("mb_nick", "")
        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_name, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_name, mb_nick)",
                account=acct,
            )
        account_info = f"ID={mb_id}, PW={mb_password}, 이름={mb_name}, 닉네임={mb_nick}"

        # 회원가입 페이지 이동
        self.goto("/bbs/register.php")
        time.sleep(0.5)
        self.require_human_check()

        self.handle_alert(accept=True, timeout=1.0)
        self.handle_alert(accept=True, timeout=1.0)

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']"))
            )
        except Exception:
            # 한번 더 시도: 페이지 새로고침
            self.goto("/bbs/register.php")
            time.sleep(1.0)
            self.handle_alert(accept=True, timeout=1.0)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']"))
                )
            except Exception:
                return LoginResult(
                    success=False, method="register",
                    message="회원가입 폼 로드 실패",
                    account=acct,
                )

        # 폼 입력 (JS)
        self._fill_gnuboard_form_js(mb_id, mb_password, mb_name, mb_nick)
        time.sleep(0.3)

        # kcaptcha 자동 해결 (있으면)
        if not self._solve_kcaptcha():
            return LoginResult(
                success=False, method="register",
                message="캡차 미입력 (수동 입력 필요)",
                account=acct,
            )

        # AJAX 중복 체크 + 버튼 활성화
        self._gnuboard_ajax_check()

        # 폼 제출
        if not self._gnuboard_submit_form():
            return LoginResult(
                success=False, method="register",
                message="회원가입 제출 버튼 클릭 실패",
                account=acct,
            )

        time.sleep(1.0)
        self.require_human_check()

        return self._check_register_result(acct)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """헬로밤 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_boards(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  4. 게시글 목록
    # ──────────────────────────────────────────────

    def get_posts(self, board_id: str, *, page: int = 1,
                  search_field: str = "", search_text: str = "",
                  sort_field: str = "", sort_order: str = "") -> list[Post]:
        """특정 게시판 게시글 목록 스크래핑.

        Args:
            board_id: 게시판 ID (bo_table). 예: "review", "notice"
            page: 페이지 번호 (기본값 1)
            search_field: 검색 필드 (sfl). 예: "wr_subject", "wr_content",
                          "wr_subject||wr_content", "mb_id", "wr_name"
            search_text: 검색어 (stx)
            sort_field: 정렬 필드 (sst). 예: "wr_datetime", "wr_hit", "wr_good"
            sort_order: 정렬 방향 (sod). "asc" 또는 "desc"
        """
        from . import parsers

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
        if search_field and search_text:
            url += f"&sfl={search_field}&stx={search_text}"
        if sort_field:
            url += f"&sst={sort_field}"
        if sort_order:
            url += f"&sod={sort_order}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  5. 댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기.

        Args:
            post_id: 게시글 ID (wr_id). "bo_table:wr_id" 형식도 가능.
            board_id: 게시판 ID (bo_table). post_id에 포함된 경우 생략 가능.
        """
        from . import parsers

        # "bo_table:wr_id" 형식 지원
        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)

        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_comments(self.driver.page_source, post_id=post_id)

    # ──────────────────────────────────────────────
    #  6. 게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성.

        Args:
            board_id: 게시판 ID (bo_table). 예: "review"
            subject: 게시글 제목
            content: 게시글 내용
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self.driver.get(url)
        time.sleep(0.5)

        # 권한 없음 alert 처리 ("글을 쓸 권한이 없습니다" 등)
        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text:
            return WriteResult(success=False, message=f"작성 불가: {alert_text}")

        self.require_human_check()

        # 로그인 리다이렉트 감지
        if "login" in self.driver.current_url:
            return WriteResult(success=False, message="로그인이 필요합니다.")

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='wr_subject']")
                )
            )
        except Exception:
            return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        # 제목 입력
        subj_el = self.driver.find_element(
            By.CSS_SELECTOR, "input[name='wr_subject']"
        )
        subj_el.clear()
        subj_el.send_keys(subject)

        # 내용 입력 (CKEditor / CHEditor iframe 또는 textarea)
        content_filled = False

        # 1) CKEditor iframe
        if not content_filled:
            try:
                iframe = self.driver.find_element(
                    By.CSS_SELECTOR, ".cke_wysiwyg_frame",
                )
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.clear()
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_filled = True
            except Exception:
                self.driver.switch_to.default_content()

        # 2) CHEditor iframe
        if not content_filled:
            try:
                iframe = self.driver.find_element(
                    By.CSS_SELECTOR, "iframe.cheditor-editarea",
                )
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.click()
                # 기존 내용 전체 선택 후 교체
                import platform
                from selenium.webdriver.common.keys import Keys
                mod = Keys.COMMAND if platform.system() == "Darwin" else Keys.CONTROL
                body.send_keys(mod + "a")
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_filled = True
            except Exception:
                self.driver.switch_to.default_content()

        # 3) textarea 직접 (visible 또는 JS로)
        if not content_filled:
            try:
                ta = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "textarea[name='wr_content'], #wr_content",
                )
                self.driver.execute_script(
                    "arguments[0].value = arguments[1];", ta, content,
                )
                content_filled = True
            except Exception:
                pass

        if not content_filled:
            return WriteResult(
                success=False, message="내용 입력 영역을 찾을 수 없습니다."
            )

        time.sleep(0.3)

        # 제출 (APMS: div#btn_submit 또는 표준 submit 버튼)
        try:
            btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "#btn_submit, button[type='submit'], input[type='submit']",
            )
            btn.click()
        except Exception:
            return WriteResult(success=False, message="게시글 제출 버튼 클릭 실패")

        time.sleep(1.0)
        self.require_human_check()

        # alert 확인
        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"작성 실패: {alert_text}")

        # SweetAlert 확인
        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"작성 실패: {swal_text}")

        # wr_id 추출 시도 (리다이렉트된 URL에서)
        current = self.driver.current_url
        wr_id_m = re.search(r"wr_id=(\d+)", current)
        wr_id = wr_id_m.group(1) if wr_id_m else ""

        if "write.php" not in current:
            return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ──────────────────────────────────────────────
    #  7. 댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, *, board_id: str = "") -> WriteResult:
        """특정 게시글에 댓글 작성.

        Args:
            post_id: 게시글 ID (wr_id). "bo_table:wr_id" 형식도 가능.
            content: 댓글 내용.
            board_id: 게시판 ID (bo_table). post_id에 포함된 경우 생략 가능.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # "bo_table:wr_id" 형식 지원
        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)

        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        # 게시글 상세 페이지 이동
        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        # 댓글 폼 찾기
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#fviewcomment textarea[name='wr_content'], textarea[name='wr_content']")
                )
            )
        except Exception:
            return WriteResult(
                success=False,
                message="댓글 입력 폼을 찾을 수 없습니다. (로그인 필요 가능)",
            )

        # 댓글 내용 입력
        ta = self.driver.find_element(
            By.CSS_SELECTOR,
            "#fviewcomment textarea[name='wr_content'], textarea[name='wr_content']",
        )
        ta.clear()
        ta.send_keys(content)
        time.sleep(0.3)

        # 제출 (APMS: div#btn_submit onclick="apms_comment_submit()")
        try:
            self.driver.execute_script("""
                var f = document.querySelector('#fviewcomment, form[name="fviewcomment"]');
                if (!f) throw new Error('no form');
                var btn = f.querySelector('#btn_submit, button[type="submit"], input[type="submit"]');
                if (btn) btn.click();
                else f.submit();
            """)
        except Exception:
            return WriteResult(success=False, message="댓글 제출 실패")

        time.sleep(1.0)
        self.require_human_check()

        # alert 확인
        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        # SweetAlert 확인
        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {swal_text}")

        return WriteResult(success=True, message="댓글 작성 완료")
