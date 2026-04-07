"""OpmaniaSite - 오피매니아 (opstar 커스텀 테마)."""

from __future__ import annotations

import re
import time
from typing import Any

from ...base import STATUS_SUCCESS, STATUS_UNKNOWN
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


class OpmaniaSite(GnuboardSite):
    SITE_NAME = "오피매니아"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_FORM_CONTAINER = "#flogin"
    LOGIN_ID_SELECTOR = "input[name='mb_id']"
    LOGIN_PW_SELECTOR = "input[name='mb_password']"
    GOTO_VIA_SCRIPT = True
    DIRECT_LOGIN = True
    LOGIN_CHECK_VIA_SOURCE = True
    LOGIN_POST_SUBMIT_DELAY = 1.0

    # ══════════════════════════════════════════════
    #  로그인 훅 (GnuboardSite Template Method)
    # ══════════════════════════════════════════════

    def _warmup(self) -> None:
        self.driver.get("https://naver.com")
        time.sleep(0.5)
        self.driver.execute_script(
            "window.location.href = arguments[0];", self.base_url
        )
        time.sleep(2)
        self.require_human_check()

    def _navigate_to_login(self) -> None:
        self.driver.execute_script(
            "window.location.href = arguments[0];",
            self.base_url + "/bbs/login.php",
        )
        time.sleep(2)
        self.require_human_check()

    def _fill_login_form(self) -> None:
        """JS value 설정으로 로그인 폼 입력 후 submit."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(self.driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "#flogin input[name='mb_id']")
            )
        )
        self.driver.execute_script(
            """
            var f = document.getElementById('flogin');
            f.querySelector('input[name="mb_id"]').value = arguments[0];
            f.querySelector('input[name="mb_password"]').value = arguments[1];
            f.submit();
            """,
            self.username,
            self.password,
        )

    def _post_login_alerts(self) -> str:
        """로그인 후 alert 텍스트 캡처 후 dismiss. 텍스트 반환."""
        from selenium.webdriver.common.alert import Alert
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            alert = Alert(self.driver)
            text = alert.text or ""
            alert.accept()
            return text
        except Exception:
            return ""

    # ══════════════════════════════════════════════
    #  점프
    # ══════════════════════════════════════════════

    def jump(self) -> JumpResult:
        from selenium.webdriver.common.alert import Alert
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            self.driver.execute_script(
                "window.location.href = arguments[0];",
                self.base_url + "/bbs/jump_company.php",
            )
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#manual_btn"))
            )
            self.driver.find_element(By.CSS_SELECTOR, "#manual_btn").click()

            # confirm alert
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            Alert(self.driver).accept()

            # result alert
            msg = ""
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                a2 = Alert(self.driver)
                msg = a2.text
                a2.accept()
                self.emit(f"[오피매니아] 결과 alert: {msg}", "DEBUG")
            except Exception:
                pass
        except Exception as exc:
            return JumpResult(status="failed", message=f"점프 실행 실패: {exc}")

        if msg:
            status, result_msg = self.classify_result(msg)
            if status != STATUS_UNKNOWN:
                return JumpResult(status=status, message=result_msg)

        return JumpResult(status=STATUS_SUCCESS, message="점프 실행")

    # ══════════════════════════════════════════════
    #  1. 회원가입
    # ══════════════════════════════════════════════

    def register(self, **kwargs: Any) -> LoginResult:
        """오피매니아 회원가입.

        필수: mb_id, mb_password, mb_nick
        (opstar 테마 - mb_name 필드 없음, register_form_1.php 사용)
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_name = kwargs.get("mb_name", "")
        mb_nick = kwargs.get("mb_nick", "")

        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_nick]):
            return LoginResult(
                success=False,
                method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_nick)",
                account=acct,
            )

        # naver 워밍업 후 register_form_1.php 이동
        self.driver.get("https://naver.com")
        time.sleep(0.5)
        self.driver.execute_script(
            "window.location.href = arguments[0];",
            self.base_url + "/bbs/register_form_1.php",
        )
        time.sleep(2)
        self.require_human_check()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#mb_id, input[name='mb_id']")
                )
            )
        except Exception:
            return LoginResult(
                success=False, method="register", message="회원가입 폼 로드 실패",
                account=acct,
            )

        # JS로 모든 필드 값 설정 (send_keys 불안정 방지)
        self.driver.execute_script(
            """
            var f = document.getElementById('fregisterform');
            var idEl = document.getElementById('mb_id') || f.querySelector('input[name="mb_id"]');
            var pwEl = document.getElementById('reg_mb_password') || f.querySelector('input[name="mb_password"]');
            var pwReEl = document.getElementById('reg_mb_password_re') || f.querySelector('input[name="mb_password_re"]');
            var nickEl = document.getElementById('mb_nick') || f.querySelector('input[name="mb_nick"]');
            if (idEl) { idEl.focus(); idEl.value = arguments[0]; idEl.dispatchEvent(new Event('input', {bubbles:true})); idEl.dispatchEvent(new Event('change', {bubbles:true})); }
            if (pwEl) { pwEl.value = arguments[1]; }
            if (pwReEl) { pwReEl.value = arguments[1]; }
            if (nickEl) { nickEl.value = arguments[2]; }
            """,
            mb_id,
            mb_password,
            mb_nick,
        )
        time.sleep(1.0)

        # kcaptcha 자동 해결 또는 수동 입력 대기
        captcha_solved = False
        try:
            captcha_el = self.driver.find_element(
                By.CSS_SELECTOR, "#captcha_key"
            )
            # 2captcha API로 이미지 캡차 자동 해결 (최대 3회 재시도)
            if self._captcha_api_key:
                import requests

                for captcha_try in range(3):
                    try:
                        if captcha_try > 0:
                            # 캡차 이미지 새로고침
                            try:
                                self.driver.find_element(
                                    By.CSS_SELECTOR, "#captcha_reload"
                                ).click()
                                time.sleep(1.0)
                            except Exception:
                                self.driver.execute_script(
                                    "var img=document.getElementById('captcha_img');"
                                    "if(img)img.src=img.src.split('?')[0]+'?t='+Date.now();"
                                )
                                time.sleep(1.0)

                        # JS canvas로 캡차 이미지를 base64 추출 (screenshot보다 정확)
                        img_b64 = None
                        try:
                            img_b64 = self.driver.execute_script("""
                                var img = document.getElementById('captcha_img');
                                if (!img) return null;
                                // 이미지 로딩 대기
                                if (!img.complete || img.naturalWidth === 0) return null;
                                var canvas = document.createElement('canvas');
                                canvas.width = img.naturalWidth;
                                canvas.height = img.naturalHeight;
                                var ctx = canvas.getContext('2d');
                                ctx.drawImage(img, 0, 0);
                                return canvas.toDataURL('image/png').split(',')[1];
                            """)
                        except Exception:
                            img_b64 = None

                        if not img_b64:
                            # canvas 실패 시 screenshot 폴백
                            try:
                                captcha_img = self.driver.find_element(
                                    By.CSS_SELECTOR, "#captcha_img"
                                )
                                img_b64 = captcha_img.screenshot_as_base64
                            except Exception:
                                pass

                        if not img_b64 or len(img_b64) < 100:
                            self.emit(
                                f"[오피매니아] 캡차 이미지 캡처 실패 (시도 {captcha_try + 1})",
                                "ERROR",
                            )
                            continue

                        self.emit(
                            f"[오피매니아] 2Captcha 캡차 풀이 중... (시도 {captcha_try + 1}, 이미지 {len(img_b64)}B)",
                            "INFO",
                        )

                        # 1) 캡차 이미지 전송
                        resp = requests.post(
                            "http://2captcha.com/in.php",
                            data={
                                "key": self._captcha_api_key,
                                "method": "base64",
                                "body": img_b64,
                                "numeric": 1,
                                "min_len": 4,
                                "max_len": 6,
                                "json": 1,
                            },
                            timeout=30,
                        )
                        req_data = resp.json()
                        if req_data.get("status") != 1:
                            self.emit(
                                f"[오피매니아] 캡차 전송 실패: {req_data}", "ERROR"
                            )
                            continue

                        captcha_id = req_data["request"]

                        # 2) 결과 폴링 (최대 60초)
                        code = None
                        for _ in range(30):
                            time.sleep(2)
                            res = requests.get(
                                "http://2captcha.com/res.php",
                                params={
                                    "key": self._captcha_api_key,
                                    "action": "get",
                                    "id": captcha_id,
                                    "json": 1,
                                },
                                timeout=15,
                            )
                            res_data = res.json()
                            if res_data.get("status") == 1:
                                code = res_data["request"]
                                break
                            if "CAPCHA_NOT_READY" not in res_data.get(
                                "request", ""
                            ):
                                self.emit(
                                    f"[오피매니아] 캡차 풀이 실패: {res_data}",
                                    "ERROR",
                                )
                                break

                        # 3) 응답 검증: 4~6자리 숫자여야 함
                        if code and len(code) >= 4 and code.isdigit():
                            captcha_el.clear()
                            captcha_el.send_keys(code)
                            captcha_solved = True
                            self.emit(
                                f"[오피매니아] 캡차 자동 해결: {code}", "INFO"
                            )
                            break
                        else:
                            self.emit(
                                f"[오피매니아] 캡차 응답 부적절: '{code}' (4~6자리 숫자 필요). 재시도...",
                                "WARNING",
                            )
                            # 오답 신고 (2captcha 크레딧 환불)
                            if captcha_id:
                                try:
                                    requests.get(
                                        "http://2captcha.com/res.php",
                                        params={
                                            "key": self._captcha_api_key,
                                            "action": "reportbad",
                                            "id": captcha_id,
                                        },
                                        timeout=5,
                                    )
                                except Exception:
                                    pass
                            continue

                    except Exception as exc:
                        self.emit(
                            f"[오피매니아] 캡차 자동 해결 실패: {exc}", "ERROR"
                        )

            if not captcha_solved:
                # 수동 입력 대기: captcha_key 필드가 채워질 때까지 최대 120초
                self.emit("[오피매니아] 캡차를 입력해주세요...", "INFO")
                filled = False
                for _ in range(240):
                    val = captcha_el.get_attribute("value") or ""
                    if len(val) >= 4:
                        filled = True
                        break
                    time.sleep(0.5)
                if not filled:
                    return LoginResult(
                        success=False,
                        method="register",
                        message="캡차 미입력 (수동 입력 필요)",
                        account=acct,
                    )
        except Exception:
            pass  # 캡차 필드 없으면 무시

        self.require_human_check()

        # 팝업 배너 닫기 (클릭 차단 방지)
        try:
            self.driver.execute_script(
                """
                document.querySelectorAll('.popup_banner, .popup_banner1').forEach(
                    function(el) { el.style.display = 'none'; }
                );
                """
            )
        except Exception:
            pass

        # AJAX 중복 체크 + 버튼 활성화
        self._gnuboard_ajax_check()

        try:
            submit_btn = self.driver.find_element(
                By.CSS_SELECTOR, "#btn_submit, button[type='submit']"
            )
            self.driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
            time.sleep(0.3)
            try:
                submit_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", submit_btn)
        except Exception:
            # JS form submit 폴백
            try:
                submitted = self.driver.execute_script(
                    """
                    var f = document.getElementById('fregisterform')
                         || document.querySelector('form#fregisterform')
                         || document.querySelector('form');
                    if (!f) return false;
                    var btn = f.querySelector('#btn_submit, button[type="submit"], input[type="submit"]');
                    if (btn) { btn.click(); return true; }
                    if (typeof f.submit === 'function') { f.submit(); return true; }
                    return false;
                    """
                )
                if not submitted:
                    raise RuntimeError("submit target not found")
            except Exception:
                return LoginResult(
                    success=False, method="register", message="회원가입 제출 버튼 클릭 실패",
                    account=acct,
                )

        time.sleep(1.5)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=2.0)
        if alert_text:
            if "완료" in alert_text or "가입" in alert_text:
                return LoginResult(
                    success=True, method="register",
                    message=f"회원가입 성공: {alert_text}",
                    account=acct,
                )
            return LoginResult(
                success=False, method="register",
                message=f"회원가입 실패: {alert_text}",
                account=acct,
            )

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text:
            if "완료" in swal_text or "가입" in swal_text:
                return LoginResult(
                    success=True, method="register",
                    message=f"회원가입 성공: {swal_text}",
                    account=acct,
                )
            return LoginResult(
                success=False, method="register",
                message=f"회원가입 실패: {swal_text}",
                account=acct,
            )

        current = self.driver.current_url
        src = self.driver.page_source or ""

        # 로그인 페이지로 이동 or register 페이지 벗어남 → 성공
        if "login" in current or "register" not in current:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (페이지 이동 확인)",
                account=acct,
            )

        # 페이지 소스에서 로그인 상태 확인 (toolbar에 로그아웃 링크)
        if "logout" in src or "로그아웃" in src:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (로그인 상태 확인)",
                account=acct,
            )

        # 페이지 소스에서 가입 완료 메시지 확인
        if "가입을 축하" in src or "회원가입이 완료" in src or "가입이 완료" in src:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료",
                account=acct,
            )

        return LoginResult(
            success=False, method="register", message="회원가입 결과 확인 불가",
            account=acct,
        )

    # ══════════════════════════════════════════════
    #  2. 프로필 조회
    # ══════════════════════════════════════════════

    def get_profile(self) -> Profile:
        """마이페이지에서 프로필 정보 조회."""
        from . import parsers

        self.goto(self.base_url + "/bbs/mypage.php", via_script=True)
        time.sleep(1.5)
        self.require_human_check()

        return parsers.parse_profile(self.driver.page_source)

    # ══════════════════════════════════════════════
    #  3. 게시판 목록
    # ══════════════════════════════════════════════

    def get_boards(self) -> list[Board]:
        """오피매니아 게시판 목록 (네비게이션에서 추출)."""
        from . import parsers

        # 게시판 페이지에서 사이드 네비게이션 링크 수집
        self.goto(
            self.base_url + "/bbs/board.php?bo_table=notice", via_script=True
        )
        time.sleep(1.5)
        self.require_human_check()

        return parsers.parse_boards(self.driver.page_source)

    # ══════════════════════════════════════════════
    #  4. 게시글 목록
    # ══════════════════════════════════════════════

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
        time.sleep(2)
        self.require_human_check()

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ══════════════════════════════════════════════
    #  5. 댓글 가져오기
    # ══════════════════════════════════════════════

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기."""
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError(
                "board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)"
            )

        url = (
            f"{self.base_url}/bbs/board.php"
            f"?bo_table={board_id}&wr_id={post_id}"
        )
        self.goto(url, via_script=True)
        time.sleep(2)
        self.require_human_check()

        return parsers.parse_comments(
            self.driver.page_source, post_id=post_id
        )

    # ══════════════════════════════════════════════
    #  6. 게시글 작성
    # ══════════════════════════════════════════════

    def write_post(
        self, board_id: str, subject: str, content: str
    ) -> WriteResult:
        """게시글 작성."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self.goto(url, via_script=True)
        time.sleep(2)
        self.require_human_check()

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

        # 내용 입력: CKEditor JS API → iframe → textarea 폴백
        content_filled = False

        # CKEditor 완전 로딩 대기 (사이트 기본 템플릿이 먼저 채워진 후 덮어쓰기)
        time.sleep(3)  # 사이트 템플릿 로딩 대기

        # 방법 1: CKEditor JS API — 로딩 대기 후 setData + 검증
        try:
            result = self.driver.execute_script(
                """
                var editor = null;
                if (typeof CKEDITOR !== 'undefined') {
                    for (var name in CKEDITOR.instances) {
                        editor = CKEDITOR.instances[name];
                        break;
                    }
                }
                if (editor) {
                    editor.setData(arguments[0]);
                    return 'ok';
                }
                return 'no_editor';
                """,
                content,
            )
            if result == "ok":
                # 검증: setData 후 잠시 대기하고 다시 한번 덮어쓰기 (사이트 JS 경쟁 방지)
                time.sleep(1)
                self.driver.execute_script(
                    """
                    var editor = null;
                    if (typeof CKEDITOR !== 'undefined') {
                        for (var name in CKEDITOR.instances) {
                            editor = CKEDITOR.instances[name];
                            break;
                        }
                    }
                    if (editor) {
                        var cur = editor.getData();
                        if (cur.indexOf('OO') !== -1 || cur.indexOf(arguments[0].substring(0, 10)) === -1) {
                            editor.setData(arguments[0]);
                        }
                    }
                    """,
                    content,
                )
                content_filled = True
        except Exception:
            pass

        # 방법 2: CKEditor iframe 직접 접근
        if not content_filled:
            try:
                iframe = self.driver.find_element(
                    By.CSS_SELECTOR,
                    ".cke_wysiwyg_frame, iframe.cke_wysiwyg_frame",
                )
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.clear()
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_filled = True
            except Exception:
                self.driver.switch_to.default_content()

        # 방법 3: textarea 직접 입력
        if not content_filled:
            try:
                ta = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "textarea[name='wr_content'], #wr_content",
                )
                self.driver.execute_script(
                    "arguments[0].style.display='block'; arguments[0].value=arguments[1];",
                    ta,
                    content,
                )
                content_filled = True
            except Exception:
                pass

        # 방법 4: 숨겨진 textarea에 JS로 값 설정
        if not content_filled:
            try:
                self.driver.execute_script(
                    """
                    var ta = document.querySelector('textarea[name="wr_content"]')
                        || document.getElementById('wr_content');
                    if (ta) { ta.value = arguments[0]; return true; }
                    return false;
                    """,
                    content,
                )
                content_filled = True
            except Exception:
                pass

        if not content_filled:
            return WriteResult(
                success=False, message="내용 입력 영역을 찾을 수 없습니다."
            )

        time.sleep(0.5)

        # 팝업 배너 닫기 (클릭 차단 방지)
        try:
            self.driver.execute_script(
                """
                document.querySelectorAll('.popup_banner, .popup_banner1').forEach(
                    function(el) { el.style.display = 'none'; }
                );
                """
            )
        except Exception:
            pass

        try:
            btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "button[type='submit'], input[type='submit'], "
                "input[type='image'], "
                "#btn_submit, .btn_submit, .btn_confirm, "
                "a.btn_submit, a.write_submit",
            )
            self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
            time.sleep(0.3)
            try:
                btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", btn)
        except Exception:
            # JS form submit 폴백
            try:
                self.driver.execute_script(
                    """
                    var f = document.getElementById('fwrite')
                        || document.querySelector('form[name="fwrite"]')
                        || document.querySelector('form[action*="write_update"]');
                    if (f) { f.submit(); return true; }
                    return false;
                    """
                )
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
            return WriteResult(
                success=True, id=wr_id, message="게시글 작성 완료"
            )

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ══════════════════════════════════════════════
    #  7. 댓글 작성
    # ══════════════════════════════════════════════

    def write_comment(
        self, post_id: str, content: str, *, board_id: str = ""
    ) -> WriteResult:
        """특정 게시글에 댓글 작성.

        opstar 테마는 AJAX 기반 댓글 시스템 사용:
        - 새 댓글: textarea#comment + fnCommentSubmit(0, 0)
        - 대댓글: textarea#comment_{wr_id} + fnCommentSubmit(0, wr_id)
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError(
                "board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)"
            )

        url = (
            f"{self.base_url}/bbs/board.php"
            f"?bo_table={board_id}&wr_id={post_id}"
        )
        self.goto(url, via_script=True)
        time.sleep(2)
        self.require_human_check()

        # 방법 1: textarea#comment (opstar AJAX 댓글)
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "textarea#comment")
                )
            )
            ta = self.driver.find_element(By.CSS_SELECTOR, "textarea#comment")
            ta.clear()
            ta.send_keys(content)
            time.sleep(0.3)

            # submit 버튼 클릭 또는 fnCommentSubmit JS 호출
            try:
                btn = self.driver.find_element(
                    By.CSS_SELECTOR, ".comment_save, .btn_comment"
                )
                btn.click()
            except Exception:
                self.driver.execute_script("fnCommentSubmit(0, 0);")

            time.sleep(2.0)
            self.require_human_check()

            alert_text = self.handle_alert(accept=True, timeout=2.0)
            if alert_text and ("오류" in alert_text or "실패" in alert_text):
                return WriteResult(
                    success=False, message=f"댓글 작성 실패: {alert_text}"
                )

            return WriteResult(success=True, message="댓글 작성 완료")
        except Exception:
            pass

        # 방법 2: 표준 gnuboard 댓글 폼 (#fviewcomment)
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        "#fviewcomment textarea[name='wr_content'], "
                        "textarea[name='wr_content']",
                    )
                )
            )
        except Exception:
            return WriteResult(
                success=False,
                message="댓글 입력 폼을 찾을 수 없습니다. (로그인 필요 가능)",
            )

        ta = self.driver.find_element(
            By.CSS_SELECTOR,
            "#fviewcomment textarea[name='wr_content'], "
            "textarea[name='wr_content']",
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
            return WriteResult(
                success=False, message=f"댓글 작성 실패: {alert_text}"
            )

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(
                success=False, message=f"댓글 작성 실패: {swal_text}"
            )

        return WriteResult(success=True, message="댓글 작성 완료")

    # ──────────────────────────────────────────────
    #  출석체크
    # ──────────────────────────────────────────────

    def checkin(self) -> dict[str, Any]:
        """출석체크 (로그인 시 자동 포인트, 별도 출석 없음 — 로그인 자체가 10P)."""
        return {"success": True, "message": "오피매니아는 로그인=출석 (10P)"}

    # ──────────────────────────────────────────────
    #  등업 — 수동 (등업신청 글 작성 필요)
    # ──────────────────────────────────────────────

    def click_levelup(self) -> dict[str, Any]:
        """오피매니아는 등업 버튼이 없음 (관리자 수동 승인).
        등업신청은 write_post(board_id='levelup', ...)로 처리.
        이 메서드는 호환성을 위해 존재."""
        return {"success": False, "message": "오피매니아는 수동등업 (등업신청 글 필요)"}
