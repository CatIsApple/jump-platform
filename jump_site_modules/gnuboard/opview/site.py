"""OpviewSite - 오피뷰."""

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


class OpviewSite(GnuboardSite):
    SITE_NAME = "오피뷰"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_CHECK_TEXT = "내글보기"
    LOGIN_ID_SELECTOR = "#login_fs input[name='mb_id']"
    LOGIN_PW_SELECTOR = "#login_fs input[name='mb_password']"
    LOGIN_SUBMIT_SELECTOR = "#login_fs button[type='submit']"

    def _check_logged_in(self) -> bool:
        return self.wait_for_text("내글보기", timeout=2.0)

    def _navigate_to_login(self) -> None:
        self.goto(self.url("/bbs/login.php"))
        time.sleep(0.5)
        self.require_human_check()

    def jump(self) -> JumpResult:
        from selenium.webdriver.common.alert import Alert
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        alert_texts: list[str] = []
        try:
            element = self.driver.find_element(
                By.XPATH, "//li[@class='com_jump']//a[contains(text(), '업소 점프하기')]"
            )
            self.driver.execute_script("arguments[0].click();", element)

            # alert 1 (confirm or result)
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                a1 = Alert(self.driver)
                alert_texts.append(a1.text)
                a1.accept()
            except Exception:
                pass

            # alert 2 (result)
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                a2 = Alert(self.driver)
                alert_texts.append(a2.text)
                a2.accept()
            except Exception:
                pass
        except Exception as exc:
            return JumpResult(status="failed", message=f"점프 실행 실패: {exc}")

        self.save_cookies(self.COOKIE_KEYS)

        for txt in reversed(alert_texts):
            if txt:
                self.emit(f"[오피뷰] 결과: {txt}", "DEBUG")
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
        """오피뷰 회원가입.

        필수: mb_id, mb_password, mb_name, mb_nick
        register_form.php → mb_sex 라디오 필수 선택.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_name = kwargs.get("mb_name", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_sex = kwargs.get("mb_sex", "M")

        acct = {
            "mb_id": mb_id,
            "mb_password": mb_password,
            "mb_name": mb_name,
            "mb_nick": mb_nick,
        }

        if not all([mb_id, mb_password, mb_name, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_name, mb_nick)",
                account=acct,
            )

        # 네이버 워밍업 후 register.php 이동
        self.driver.get("https://naver.com")
        time.sleep(0.5)
        self.driver.execute_script(
            "window.location.href = arguments[0];",
            self.base_url + "/bbs/register.php",
        )
        time.sleep(0.7)
        self.require_human_check()

        # register.php → register_form.php 리다이렉트 대기
        for _ in range(3):
            if "register_form" in self.driver.current_url:
                break
            time.sleep(0.5)

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                )
            )
        except Exception:
            return LoginResult(
                success=False, method="register",
                message="회원가입 폼 로드 실패",
                account=acct,
            )

        # 팝업/배너 닫기
        self.driver.execute_script("""
            document.querySelectorAll('.modal, .popup, [class*="banner"]').forEach(function(el) {
                el.style.display = 'none';
            });
        """)

        # JS로 폼 필드 채우기
        self.driver.execute_script(
            """
            var f = document.getElementById('fregisterform') || document.forms[0];
            var idEl = document.getElementById('reg_mb_id') || f.querySelector('input[name="mb_id"]');
            var pwEl = document.getElementById('reg_mb_password') || f.querySelector('input[name="mb_password"]');
            var pwReEl = document.getElementById('reg_mb_password_re') || f.querySelector('input[name="mb_password_re"]');
            var nameEl = document.getElementById('reg_mb_name') || f.querySelector('input[name="mb_name"]');
            var nickEl = document.getElementById('reg_mb_nick') || f.querySelector('input[name="mb_nick"]');

            if (idEl) { idEl.focus(); idEl.value = arguments[0]; idEl.dispatchEvent(new Event('input', {bubbles:true})); idEl.dispatchEvent(new Event('change', {bubbles:true})); }
            if (pwEl) { pwEl.value = arguments[1]; }
            if (pwReEl) { pwReEl.value = arguments[1]; }
            if (nameEl) { nameEl.value = arguments[2]; }
            if (nickEl) { nickEl.value = arguments[3]; }

            // mb_sex 라디오 선택 (필수)
            var sexRadio = f.querySelector('input[name="mb_sex"][value="' + arguments[4] + '"]');
            if (sexRadio) { sexRadio.checked = true; sexRadio.click(); }
            """,
            mb_id,
            mb_password,
            mb_name,
            mb_nick,
            mb_sex,
        )
        time.sleep(0.5)

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
    #  2. 프로필 조회
    # ──────────────────────────────────────────────

    def get_profile(self) -> Profile:
        """마이페이지에서 프로필 정보 조회."""
        from . import parsers

        self.goto(self.base_url + "/bbs/mypage.php", via_script=True)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_profile(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """오피뷰 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url, via_script=True)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_boards(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  4. 게시글 목록
    # ──────────────────────────────────────────────

    def get_posts(
        self,
        board_id: str,
        *,
        page: int = 1,
        search_field: str = "",
        search_text: str = "",
        sort_field: str = "",
        sort_order: str = "",
    ) -> list[Post]:
        """특정 게시판 게시글 목록 스크래핑."""
        from . import parsers

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
        if search_field and search_text:
            url += f"&sfl={search_field}&stx={search_text}"
        if sort_field:
            url += f"&sst={sort_field}"
        if sort_order:
            url += f"&sod={sort_order}"
        self.goto(url, via_script=True)
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
        self.goto(url, via_script=True)
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
        self.goto(url, via_script=True)
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

        # 내용 입력: 여러 에디터 패턴 시도
        content_set = self.driver.execute_script("""
            // 1. SmartEditor2 (APMS 사이트 표준)
            if (typeof oEditors !== 'undefined' && oEditors.getById && oEditors.getById['wr_content']) {
                oEditors.getById['wr_content'].exec('SET_IR', [arguments[0]]);
                return 'smarteditor';
            }
            // 2. CKEditor API
            if (typeof CKEDITOR !== 'undefined') {
                var inst = CKEDITOR.instances;
                for (var k in inst) {
                    inst[k].setData(arguments[0]);
                    return 'ckeditor';
                }
            }
            // 3. textarea 직접
            var ta = document.querySelector('textarea[name="wr_content"], #wr_content');
            if (ta) {
                ta.style.display = 'block';
                ta.value = arguments[0];
                ta.dispatchEvent(new Event('input', {bubbles:true}));
                return 'textarea';
            }
            // 4. contenteditable div
            var ce = document.querySelector('[contenteditable="true"]');
            if (ce) {
                ce.innerHTML = arguments[0];
                return 'contenteditable';
            }
            return null;
        """, content)

        if not content_set:
            # fallback: iframe 직접 시도
            try:
                iframe = self.driver.find_element(
                    By.CSS_SELECTOR,
                    ".cke_wysiwyg_frame, iframe.cke_wysiwyg_frame, "
                    "#se2_iframe, iframe[id*='content']"
                )
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.clear()
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_set = "iframe"
            except Exception:
                self.driver.switch_to.default_content()
                return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.5)

        # SmartEditor2: textarea 동기화 후 제출
        try:
            self.driver.execute_script("""
                // SmartEditor2 → textarea 동기화
                if (typeof oEditors !== 'undefined' && oEditors.getById && oEditors.getById['wr_content']) {
                    oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD', []);
                }
                // #btn_submit 또는 폼 내 submit 버튼 클릭
                var f = document.getElementById('fwrite') || document.querySelector('form[action*="write_update"]');
                var btn = document.getElementById('btn_submit');
                if (!btn && f) btn = f.querySelector('button[type="submit"], input[type="submit"]');
                if (btn) { btn.click(); }
                else if (f) { f.submit(); }
            """)
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
        self.goto(url, via_script=True)
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
