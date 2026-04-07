"""OpnaraSite - 오피나라."""

from __future__ import annotations

import re
import time
from typing import Any

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_INSUFFICIENT,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
)
from ...gnuboard_base import GnuboardSite
from ...types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    WriteResult,
)


class OpnaraSite(GnuboardSite):
    SITE_NAME = "오피나라"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_ID_SELECTOR = "#login_id, input[name='mb_id']"
    LOGIN_PW_SELECTOR = "#login_pw, input[name='mb_password']"
    LOGIN_SUBMIT_SELECTOR = "button[type='submit'], input[type='submit'], .btn_submit"
    DIRECT_LOGIN = True
    LOGIN_CHECK_VIA_SOURCE = True
    LOGIN_CHECK_ALT_TEXT = "마이페이지"
    LOGIN_POST_SUBMIT_DELAY = 1.5

    def _fill_login_form(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#login_id, input[name='mb_id']")
            )
        )
        id_el = self.driver.find_element(
            By.CSS_SELECTOR, "#login_id, input[name='mb_id']"
        )
        pw_el = self.driver.find_element(
            By.CSS_SELECTOR, "#login_pw, input[name='mb_password']"
        )
        id_el.clear()
        id_el.send_keys(self.username)
        pw_el.clear()
        pw_el.send_keys(self.password)

        try:
            btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "button[type='submit'], input[type='submit'], .btn_submit",
            )
            btn.click()
        except Exception:
            self.driver.execute_script("document.querySelector('form').submit();")

    def jump(self) -> JumpResult:
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # mypage에서 wr_id 추출
        self.driver.get(f"{self.base_url}/bbs/mypage.php")
        time.sleep(2)

        wr_id = self.driver.execute_script("""
            var links = document.querySelectorAll('a[onclick*="jump"]');
            for (var i = 0; i < links.length; i++) {
                var oc = links[i].getAttribute('onclick') || '';
                var m = oc.match(/jump\\w*\\(['"]?(\\d+)['"]?\\)/);
                if (m) return m[1];
            }
            var allLinks = document.querySelectorAll('a[href]');
            for (var j = 0; j < allLinks.length; j++) {
                var a = allLinks[j];
                if (a.textContent.includes('출근부') && a.href.includes('wr_id=')) {
                    var m2 = a.href.match(/wr_id=(\\d+)/);
                    if (m2) return m2[1];
                }
            }
            return null;
        """)

        if not wr_id:
            return JumpResult(
                status=STATUS_FAILED,
                message="wr_id를 찾을 수 없습니다. (업소 등록 확인)",
            )

        self.emit(f"[오피나라] wr_id: {wr_id}", "INFO")

        # 점프 실행
        self.driver.get(f"{self.base_url}/jump.php?wr_id={wr_id}")
        time.sleep(3)

        # alert 확인
        alert_text = ""
        try:
            WebDriverWait(self.driver, 5).until(EC.alert_is_present())
            alert_obj = self.driver.switch_to.alert
            alert_text = alert_obj.text
            alert_obj.accept()
        except Exception:
            pass

        try:
            src = self.driver.page_source or ""
        except Exception:
            src = ""

        result_text = alert_text or src

        if "점프가 완료" in result_text or "완료" in result_text:
            return JumpResult(status=STATUS_SUCCESS, message="점프 완료")
        if "5분" in result_text or "분에 한번" in result_text:
            return JumpResult(status=STATUS_COOLDOWN, message="5분 대기 룰")
        if "횟수" in result_text and ("없" in result_text or "부족" in result_text):
            return JumpResult(status=STATUS_INSUFFICIENT, message="점프 횟수 부족")
        if "회원만" in result_text or "로그인" in result_text:
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message="로그인 필요")

        return JumpResult(status=STATUS_FAILED, message="점프 실패 (응답 확인 불가)")

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """오피나라 회원가입."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
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
        time.sleep(0.5)
        self.require_human_check()
        # stale alert/팝업 정리
        self.handle_alert(accept=True, timeout=0.3)
        self.check_and_dismiss_popups()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                )
            )
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 폼 로드 실패", account=acct)

        # JS로 모든 필드 한번에 설정 (팝업 오버레이/이벤트 간섭 방지)
        self.driver.execute_script("""
            var f = document.getElementById('fregisterform') || document.querySelector('form[name="fregisterform"]');
            if (!f) return;
            var set = function(name, val) {
                var el = f.querySelector('[name="' + name + '"]');
                if (el) { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }
            };
            set('mb_id', arguments[0]);
            set('mb_password', arguments[1]);
            set('mb_password_re', arguments[1]);
            set('mb_name', arguments[2]);
            set('mb_nick', arguments[3]);
        """, mb_id, mb_password, mb_name, mb_nick)
        time.sleep(0.3)
        self.handle_alert(accept=True, timeout=0.3)

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
                message="회원가입 제출 실패",
                account=acct,
            )

        time.sleep(1.5)
        self.require_human_check()

        return self._check_register_result(acct)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """오피나라 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url)
        time.sleep(0.3)
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
        time.sleep(0.5)
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
        time.sleep(0.5)
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
        time.sleep(0.5)
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

        content_set = False

        # 1) SmartEditor 2 API
        if not content_set:
            try:
                result = self.driver.execute_script(
                    "if(typeof oEditors!=='undefined' && oEditors.getById['wr_content']){"
                    "  oEditors.getById['wr_content'].exec('SET_IR', [arguments[0]]); return true;"
                    "} return false;",
                    content,
                )
                if result:
                    content_set = True
            except Exception:
                pass

        # 2) SmartEditor 2 iframe 직접 입력
        if not content_set:
            try:
                se_iframe = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "iframe[src*='SmartEditor'], iframe[src*='smarteditor']",
                )
                self.driver.switch_to.frame(se_iframe)
                se_body = self.driver.find_element(By.CSS_SELECTOR, "body, .se2_inputarea")
                se_body.click()
                se_body.clear()
                se_body.send_keys(content)
                self.driver.switch_to.default_content()
                content_set = True
            except Exception:
                self.driver.switch_to.default_content()

        # 3) CKEditor iframe
        if not content_set:
            try:
                iframe = self.driver.find_element(By.CSS_SELECTOR, ".cke_wysiwyg_frame, iframe.cke_wysiwyg_frame")
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.clear()
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_set = True
            except Exception:
                self.driver.switch_to.default_content()

        # 4) textarea 직접 (JS로 hidden이어도 강제 설정)
        if not content_set:
            try:
                self.driver.execute_script(
                    "var ta = document.querySelector('textarea[name=\"wr_content\"], #wr_content');"
                    "if(ta){ ta.style.display='block'; ta.value=arguments[0];"
                    "  ta.dispatchEvent(new Event('input',{bubbles:true})); }",
                    content,
                )
                content_set = True
            except Exception:
                return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.5)

        # SmartEditor → textarea 동기화 + stale alert 정리 + JS 제출
        self.handle_alert(accept=True, timeout=0.3)
        self.driver.execute_script("""
            // SmartEditor2 내용을 textarea로 동기화
            if (typeof oEditors !== 'undefined' && oEditors.getById['wr_content']) {
                oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD', []);
            }
            // 폼 제출
            var f = document.getElementById('fwrite') || document.querySelector('form[name="fwrite"]');
            if (f) {
                var btn = f.querySelector('#btn_submit, button[type="submit"]');
                if (btn) btn.click();
                else f.submit();
            }
        """)

        time.sleep(1.5)
        self.require_human_check()

        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text or "입력" in alert_text):
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
        time.sleep(0.5)
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

        time.sleep(1.0)
        self.require_human_check()

        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {swal_text}")

        return WriteResult(success=True, message="댓글 작성 완료")
