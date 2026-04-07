"""LybamSite - 리밤 (외로운밤)."""

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
    WriteResult,
)


class LybamSite(GnuboardSite):
    SITE_NAME = "리밤"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_FORM_CONTAINER = ".layer_login"
    LOGIN_ID_SELECTOR = "input[name='mb_id']"
    LOGIN_PW_SELECTOR = "input[name='mb_password']"
    LOGIN_SUBMIT_SELECTOR = "input[type='submit']"

    def _navigate_to_login(self) -> None:
        """Click login link to open modal layer."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            wait = WebDriverWait(self.driver, 10)
            login_link = wait.until(
                EC.element_to_be_clickable((By.LINK_TEXT, "로그인"))
            )
            login_link.click()
            time.sleep(0.5)
            self.require_human_check()
        except Exception:
            super()._navigate_to_login()

    def _fill_login_form(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".layer_login input[name='mb_id']")
            )
        )
        self.driver.find_element(
            By.CSS_SELECTOR, ".layer_login input[name='mb_id']"
        ).send_keys(self.username)
        self.driver.find_element(
            By.CSS_SELECTOR, ".layer_login input[name='mb_password']"
        ).send_keys(self.password)
        self.driver.find_element(
            By.CSS_SELECTOR, ".layer_login input[type='submit']"
        ).click()

    def jump(self) -> JumpResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        alert_texts: list[str] = []
        try:
            jpbtn = self.driver.find_element(
                By.CSS_SELECTOR, "a.btn.btn-pink.btn-block"
            )
            self.driver.execute_script("arguments[0].scrollIntoView(true);", jpbtn)
            self.driver.execute_script("arguments[0].click();", jpbtn)

            # alert 1
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                a1 = self.driver.switch_to.alert
                alert_texts.append(a1.text)
                a1.accept()
            except Exception:
                pass
            time.sleep(0.5)

            # alert 2
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                a2 = self.driver.switch_to.alert
                alert_texts.append(a2.text)
                a2.accept()
            except Exception:
                pass
        except Exception as exc:
            return JumpResult(status="failed", message=f"점프 실행 실패: {exc}")

        self.save_cookies(self.COOKIE_KEYS)

        for txt in reversed(alert_texts):
            if txt:
                self.emit(f"[리밤] 결과: {txt}", "DEBUG")
                status, msg = self.classify_result(txt)
                if status != STATUS_UNKNOWN:
                    return JumpResult(status=status, message=msg)

        timer = self.wait_for_countdown(timeout=4.0)
        if timer:
            return JumpResult(status=STATUS_COOLDOWN, message=timer)

        return JumpResult(status=STATUS_SUCCESS, message="점프 실행")

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """리밤 회원가입."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_name = kwargs.get("mb_name", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_name = kwargs.get("mb_name", mb_nick)
        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_name, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_name, mb_nick)",
                account=acct,
            )

        self.goto("/bbs/register.php")
        time.sleep(1.0)
        self.require_human_check()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                )
            )
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 폼 로드 실패", account=acct)

        id_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
        id_el.clear()
        id_el.send_keys(mb_id)
        time.sleep(0.3)

        pw_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_password, input[name='mb_password']")
        pw_el.clear()
        pw_el.send_keys(mb_password)

        pw_re_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_password_re, input[name='mb_password_re']")
        pw_re_el.clear()
        pw_re_el.send_keys(mb_password)

        name_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_name, input[name='mb_name']")
        name_el.clear()
        name_el.send_keys(mb_name)

        nick_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_nick, input[name='mb_nick']")
        nick_el.clear()
        nick_el.send_keys(mb_nick)
        time.sleep(1.0)

        try:
            submit_btn = self.driver.find_element(By.CSS_SELECTOR, "#btn_submit, button[type='submit']")
            submit_btn.click()
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 제출 버튼 클릭 실패", account=acct)

        time.sleep(1.5)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=2.0)
        if alert_text:
            if "완료" in alert_text or "가입" in alert_text:
                return LoginResult(success=True, method="register", message=f"회원가입 성공: {alert_text}", account=acct)
            return LoginResult(success=False, method="register", message=f"회원가입 실패: {alert_text}", account=acct)

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text:
            if "완료" in swal_text or "가입" in swal_text:
                return LoginResult(success=True, method="register", message=f"회원가입 성공: {swal_text}", account=acct)
            return LoginResult(success=False, method="register", message=f"회원가입 실패: {swal_text}", account=acct)

        current = self.driver.current_url
        if "login" in current or "register" not in current:
            return LoginResult(success=True, method="register", message="회원가입 완료 (페이지 이동 확인)", account=acct)

        return LoginResult(success=False, method="register", message="회원가입 결과 확인 불가", account=acct)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """리밤 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url)
        time.sleep(1.0)
        self.require_human_check()

        return parsers.parse_boards(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  4. 게시글 목록
    # ──────────────────────────────────────────────

    def get_posts(self, board_id: str, *, page: int = 1) -> list[Post]:
        """특정 게시판 게시글 목록 스크래핑."""
        from . import parsers

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
        self.driver.get(url)
        time.sleep(1.5)
        self.require_human_check()

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  5. 댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기."""
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(1.5)
        self.require_human_check()

        return parsers.parse_comments(self.driver.page_source, post_id=post_id)

    # ──────────────────────────────────────────────
    #  6. 게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self.driver.get(url)
        time.sleep(1.5)
        self.require_human_check()

        if "login" in self.driver.current_url:
            return WriteResult(success=False, message="로그인이 필요합니다.")

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='wr_subject']"))
            )
        except Exception:
            return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        subj_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='wr_subject']")
        subj_el.clear()
        subj_el.send_keys(subject)

        try:
            iframe = self.driver.find_element(By.CSS_SELECTOR, ".cke_wysiwyg_frame, iframe.cke_wysiwyg_frame")
            self.driver.switch_to.frame(iframe)
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.clear()
            body.send_keys(content)
            self.driver.switch_to.default_content()
        except Exception:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea[name='wr_content'], #wr_content")
                ta.clear()
                ta.send_keys(content)
            except Exception:
                return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.5)

        try:
            btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
            btn.click()
        except Exception:
            return WriteResult(success=False, message="게시글 제출 버튼 클릭 실패")

        time.sleep(2.0)
        self.require_human_check()

        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"작성 실패: {alert_text}")

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"작성 실패: {swal_text}")

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
        """특정 게시글에 댓글 작성."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(1.5)
        self.require_human_check()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#fviewcomment textarea[name='wr_content'], textarea[name='wr_content']")
                )
            )
        except Exception:
            return WriteResult(success=False, message="댓글 입력 폼을 찾을 수 없습니다. (로그인 필요 가능)")

        ta = self.driver.find_element(
            By.CSS_SELECTOR, "#fviewcomment textarea[name='wr_content'], textarea[name='wr_content']"
        )
        ta.clear()
        ta.send_keys(content)
        time.sleep(0.3)

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

        time.sleep(2.0)
        self.require_human_check()

        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {swal_text}")

        return WriteResult(success=True, message="댓글 작성 완료")
