"""BamjeSite - 밤의제국.

bamje48.com: NB-Basic / nariya gnuboard 테마.
- 로그인: #login_id / #login_pw → login_check.php
- 회원가입: fregisterform → register_form_update582.php (약관 동의 없음)
- 게시판: /bbs/board.php?bo_table=X
- 글쓰기: /bbs/write.php?bo_table=X (SmartEditor)
- 댓글: form#fviewcomment → fviewcomment_submit (nariya)
- 프로필: /bbs/userinfo.php
"""

from __future__ import annotations

import random
import re
import time
from typing import Any

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    BaseSite,
)
from ...types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    WriteResult,
)


class BamjeSite(BaseSite):
    SITE_NAME = "밤의제국"
    COOKIE_KEYS = ["PHPSESSID"]

    # ──────────────────────────────────────────────
    #  로그인
    # ──────────────────────────────────────────────

    def login(self) -> LoginResult:
        """밤의제국 로그인 (nariya NB-Basic 테마)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        _acct = {"mb_id": self.username, "mb_password": self.password}

        self.naver_warmup(sleep_s=0.5)
        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        # Cookie injection
        for k in self.COOKIE_KEYS:
            try:
                self.driver.delete_cookie(k)
            except Exception:
                pass
        try:
            self.driver.refresh()
        except Exception:
            pass

        self.load_cookies(self.COOKIE_KEYS)
        try:
            self.driver.refresh()
        except Exception:
            pass
        self.require_human_check()

        if self.wait_for_text("로그아웃", timeout=2.0):
            return LoginResult(success=True, method="cookie", message="쿠키 로그인 성공", account=_acct)

        # Form login - /bbs/login.php
        for k in self.COOKIE_KEYS:
            try:
                self.driver.delete_cookie(k)
            except Exception:
                pass
        try:
            self.driver.refresh()
        except Exception:
            pass

        self.goto(f"{self.base_url}/bbs/login.php")
        time.sleep(0.5)
        self.require_human_check()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#login_id, #outlogin_mb_id, input[name='mb_id']")
                )
            )

            # ID 입력 (nariya: #login_id, sidebar: #outlogin_mb_id)
            try:
                id_el = self.driver.find_element(By.CSS_SELECTOR, "#login_id")
            except Exception:
                try:
                    id_el = self.driver.find_element(By.CSS_SELECTOR, "#outlogin_mb_id")
                except Exception:
                    id_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='mb_id']")
            id_el.clear()
            id_el.send_keys(self.username)

            # PW 입력
            try:
                pw_el = self.driver.find_element(By.CSS_SELECTOR, "#login_pw")
            except Exception:
                try:
                    pw_el = self.driver.find_element(By.CSS_SELECTOR, "#outlogin_mb_password")
                except Exception:
                    pw_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='mb_password']")
            pw_el.clear()
            pw_el.send_keys(self.password)

            # 제출
            try:
                form = id_el.find_element(By.XPATH, "./ancestor::form")
                btn = form.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
            except Exception:
                btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            btn.click()
        except Exception:
            return LoginResult(success=False, method="form", message="로그인 폼 입력/제출 실패", account=_acct)

        time.sleep(1.0)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text:
            self.emit(f"[{self.SITE_NAME}] 로그인 alert: {alert_text}", "INFO")

        if not self.wait_for_text("로그아웃", timeout=4.0):
            if not self.page_contains("로그아웃"):
                return LoginResult(
                    success=False, method="form",
                    message=f"로그인 실패(로그아웃 표시 없음) {alert_text}".strip(),
                    account=_acct,
                )

        self.save_cookies(self.COOKIE_KEYS)
        return LoginResult(success=True, method="form", message="로그인 성공", account=_acct)

    # ──────────────────────────────────────────────
    #  점프
    # ──────────────────────────────────────────────

    def jump(self) -> JumpResult:
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        self.driver.get(f"{self.base_url}/bbs/jump.php")

        alert_text = ""
        try:
            WebDriverWait(self.driver, 10).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            self.emit(f"[{self.SITE_NAME}] Alert: {alert_text}", "INFO")
        except Exception:
            time.sleep(2)
            try:
                src = self.driver.page_source or ""
            except Exception:
                src = ""
            m = re.search(r'alert\(["\'](.+?)["\']\)', src)
            alert_text = m.group(1) if m else ""

        if "완료" in alert_text:
            return JumpResult(status=STATUS_SUCCESS, message="점프 완료")
        if "5분" in alert_text or "분에 한번" in alert_text:
            return JumpResult(status=STATUS_COOLDOWN, message=alert_text)
        if "본인" in alert_text:
            return JumpResult(status=STATUS_FAILED, message=alert_text)
        if "회원" in alert_text or "로그인" in alert_text:
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message=alert_text)

        return JumpResult(
            status=STATUS_FAILED,
            message=f"점프 실패 (응답: {alert_text or '확인불가'})",
        )

    # ──────────────────────────────────────────────
    #  회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """밤의제국 회원가입.

        nariya NB-Basic: 약관 동의 없이 바로 폼 표시.
        fregisterform → register_form_update582.php.
        필드: #reg_mb_nick, #reg_mb_id, #reg_mb_password, #reg_mb_password_re,
              #reg_mb_name(hidden), mb_1 radio(남/여).
        #btn_submit 초기 disabled → JS로 해제 필요.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_name = kwargs.get("mb_name", mb_nick)

        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_nick)",
                account=acct,
            )

        self.goto(f"{self.base_url}/bbs/register.php")
        time.sleep(0.5)
        self.require_human_check()

        # 폼 대기
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#reg_mb_id"))
            )
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 폼 로드 실패", account=acct)

        # 필드 입력 (JS)
        self._fill_gnuboard_form_js(mb_id, mb_password, mb_name, mb_nick)

        # mb_name (hidden 필드 - 별도 설정)
        try:
            self.driver.execute_script(
                "var el = document.querySelector('#reg_mb_name'); "
                "if (el) el.value = arguments[0];",
                mb_name,
            )
        except Exception:
            pass

        # 성별 라디오 (남)
        try:
            self.driver.execute_script(
                "var el = document.querySelector('#reg_mb_1'); if (el) el.click();"
            )
        except Exception:
            pass

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

        # 제출 (nariya: btn_submit disabled → _gnuboard_submit_form이 해제)
        if not self._gnuboard_submit_form():
            # nariya fallback: fregisterform_submit()
            try:
                self.driver.execute_script("fregisterform_submit(document.fregisterform);")
            except Exception:
                return LoginResult(success=False, method="register", message="회원가입 제출 실패", account=acct)

        time.sleep(1.5)
        self.require_human_check()

        return self._check_register_result(acct)

    # ──────────────────────────────────────────────
    #  프로필
    # ──────────────────────────────────────────────

    def get_profile(self):
        """로그인된 계정의 프로필 정보 가져오기."""
        from . import parsers

        # nariya: /bbs/userinfo.php (mypage.php는 404)
        self.goto(f"{self.base_url}/bbs/userinfo.php")
        time.sleep(0.5)
        self.require_human_check()
        profile = parsers.parse_profile(self.driver.page_source)
        if profile.nickname:
            return profile

        # fallback: 메인 페이지 사이드바에서 추출
        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()
        return parsers.parse_profile(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """밤의제국 게시판 목록."""
        from . import parsers

        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        boards = parsers.parse_boards(self.driver.page_source)
        if boards:
            return boards

        # JS fallback: nav에서 bo_table 링크 수집
        try:
            nav_data = self.driver.execute_script("""
                var results = [];
                var seen = {};
                var links = document.querySelectorAll('nav a, .me-ul a, .dropdown-menu a, .sidebar-menu a');
                for (var i = 0; i < links.length; i++) {
                    var a = links[i];
                    var href = a.getAttribute('href') || '';
                    var m = href.match(/bo_table=([\\w]+)/);
                    if (m && !seen[m[1]]) {
                        seen[m[1]] = 1;
                        var text = a.textContent.trim().replace(/\\s+/g, ' ');
                        if (text && text.length <= 30) {
                            results.push({id: m[1], name: text});
                        }
                    }
                }
                return results;
            """)
            if nav_data:
                for item in nav_data:
                    boards.append(Board(
                        id=item.get("id", ""),
                        name=item.get("name", ""),
                    ))
        except Exception:
            pass

        return boards

    # ──────────────────────────────────────────────
    #  게시글 목록
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
        """특정 게시판 게시글 목록."""
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

        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text and "로그인" in alert_text:
            self.emit(f"[{self.SITE_NAME}] 게시판 접근 alert: {alert_text}", "WARNING")

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기."""
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        self.handle_alert(accept=True, timeout=1.0)

        return parsers.parse_comments(self.driver.page_source, post_id=post_id)

    # ──────────────────────────────────────────────
    #  게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성 (SmartEditor)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text and "로그인" in alert_text:
            return WriteResult(success=False, message=f"로그인이 필요합니다: {alert_text}")

        if "login" in self.driver.current_url:
            return WriteResult(success=False, message="로그인이 필요합니다.")

        self.handle_alert(accept=True, timeout=1.0)

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='wr_subject']"))
            )
        except Exception:
            # 재시도: 페이지 새로고침
            self.driver.get(url)
            time.sleep(1.0)
            self.handle_alert(accept=True, timeout=1.0)
            self.require_human_check()
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='wr_subject']"))
                )
            except Exception:
                return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        # 제목 입력
        subj_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='wr_subject']")
        subj_el.clear()
        subj_el.send_keys(subject)

        # 카테고리 선택 (ca_name select가 있으면 — required 필드)
        try:
            ca_options = self.driver.execute_script("""
                var sel = document.querySelector('select[name="ca_name"]');
                if (!sel) return null;
                var opts = [];
                for (var i = 0; i < sel.options.length; i++) {
                    if (sel.options[i].value) opts.push(sel.options[i].value);
                }
                return opts;
            """)
            if ca_options:
                pick = random.choice(["일상", "유머", "꿀팁"]) if any(
                    v in ca_options for v in ["일상", "유머", "꿀팁"]
                ) else random.choice(ca_options)
                self.driver.execute_script(
                    "document.querySelector('select[name=\"ca_name\"]').value = arguments[0];",
                    pick,
                )
                self.emit(f"카테고리 선택: {pick}", "DEBUG")
        except Exception:
            pass

        # 내용: SmartEditor → CKEditor → iframe → textarea fallback
        content_set = False
        try:
            content_set = self.driver.execute_script("""
                var html = arguments[0];
                // 1) SmartEditor 2.0
                if (typeof oEditors !== 'undefined' && oEditors.getById && oEditors.getById['wr_content']) {
                    var ed = oEditors.getById['wr_content'];
                    ed.exec('SET_IR', [html]);
                    ed.exec('UPDATE_CONTENTS_FIELD', []);
                    var ta = document.getElementById('wr_content');
                    if (ta && ta.value && ta.value.trim()) return 'smarteditor';
                }
                // 2) CKEditor 4/5
                if (typeof CKEDITOR !== 'undefined') {
                    var inst = CKEDITOR.instances['wr_content'] || CKEDITOR.instances[Object.keys(CKEDITOR.instances)[0]];
                    if (inst) { inst.setData(html); return 'ckeditor'; }
                }
                // 3) iframe 기반 에디터 (SmartEditor iframe)
                var iframe = document.querySelector('iframe[id*="wr_content"], iframe.se2_input_wysiwyg, iframe[src*="smart_editor"]');
                if (iframe && iframe.contentDocument) {
                    var body = iframe.contentDocument.body;
                    if (body) { body.innerHTML = html; return 'iframe'; }
                }
                // 4) textarea 직접 설정
                var ta = document.getElementById('wr_content') || document.querySelector('textarea[name="wr_content"]');
                if (ta) {
                    ta.value = html;
                    ta.dispatchEvent(new Event('input', {bubbles:true}));
                    ta.dispatchEvent(new Event('change', {bubbles:true}));
                    return 'textarea';
                }
                return '';
            """, content)
        except Exception:
            content_set = False

        if not content_set:
            return WriteResult(success=False, message="내용 입력 실패")

        self.emit(f"내용 입력 방식: {content_set}", "DEBUG")
        time.sleep(0.3)

        # SmartEditor UPDATE_CONTENTS_FIELD 재실행 + textarea 최종 확인
        try:
            self.driver.execute_script("""
                // SmartEditor sync
                if (typeof oEditors !== 'undefined' && oEditors.getById && oEditors.getById['wr_content']) {
                    oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD', []);
                }
                // 최종 fallback: textarea 값이 비어있으면 강제 설정
                var ta = document.getElementById('wr_content') || document.querySelector('textarea[name="wr_content"]');
                if (ta && (!ta.value || !ta.value.trim())) {
                    ta.value = arguments[0];
                }
            """, content)
        except Exception:
            pass

        # 제출
        try:
            self.driver.execute_script("""
                var btn = document.querySelector('#btn_submit');
                if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); }
                else {
                    var f = document.querySelector('#fwrite, form[name="fwrite"]');
                    if (f) f.submit();
                }
            """)
        except Exception:
            return WriteResult(success=False, message="게시글 제출 실패")

        time.sleep(1.2)
        self.require_human_check()

        # 종합 팝업/alert 감지
        popup = self.detect_popup(dismiss=True, log=True)
        if popup["type"]:
            txt = popup["text"]
            # 입력 관련 에러 키워드
            fail_kw = ["입력", "오류", "실패", "필수", "비어", "내용을"]
            if any(k in txt for k in fail_kw):
                return WriteResult(success=False, message=f"작성 실패: {txt}")

        current = self.driver.current_url
        wr_id_m = re.search(r"wr_id=(\d+)", current)
        wr_id = wr_id_m.group(1) if wr_id_m else ""

        # write.php 벗어났으면 성공
        if "write.php" not in current:
            # wr_id 없으면 게시판 목록에서 첫 글 ID 추출
            if not wr_id:
                try:
                    wr_id = self.driver.execute_script("""
                        var link = document.querySelector('.na-item a.na-subject, .list-item a.item-subject');
                        if (link) {
                            var m = link.href.match(/wr_id=(\\d+)/);
                            return m ? m[1] : '';
                        }
                        return '';
                    """) or ""
                except Exception:
                    pass
            return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ──────────────────────────────────────────────
    #  댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, *, board_id: str = "") -> WriteResult:
        """특정 게시글에 댓글 작성.

        nariya: form#fviewcomment → fviewcomment_submit(this).
        textarea#wr_content (name="wr_content").
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(0.5)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text:
            if any(kw in alert_text for kw in ("존재하지 않", "삭제", "이동된", "없는 글")):
                return WriteResult(success=False, message=f"존재하지 않는 글: {alert_text[:80]}")
            if "로그인" in alert_text or "회원" in alert_text:
                return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        # 댓글 폼 찾기: form#fviewcomment 의 textarea#wr_content
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#fviewcomment textarea#wr_content, textarea#wr_content, textarea[name='wr_content']")
                )
            )
        except Exception:
            return WriteResult(success=False, message="댓글 입력 폼을 찾을 수 없습니다.")

        # textarea 찾기 (fviewcomment 내부 우선)
        try:
            ta = self.driver.find_element(By.CSS_SELECTOR, "#fviewcomment textarea#wr_content")
        except Exception:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "#fviewcomment textarea[name='wr_content']")
            except Exception:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea#wr_content")

        ta.clear()
        ta.send_keys(content)
        time.sleep(0.3)

        # nariya 테마: send_keys 후 JS 이벤트를 수동 dispatch해야 버튼 활성화됨
        # (disabled 속성이 input/keyup 이벤트 핸들러에서 제거되는 구조)
        self.driver.execute_script("""
            var ta = arguments[0];
            ['input', 'keyup', 'keydown', 'change', 'keypress'].forEach(function(evt) {
                ta.dispatchEvent(new Event(evt, {bubbles: true}));
            });
            // 안전장치: disabled 속성 직접 제거
            var btn = document.getElementById('btn_submit');
            if (btn) btn.removeAttribute('disabled');
        """, ta)
        time.sleep(0.5)

        # 제출: nariya는 na_comment('viewcomment') 함수로 댓글 제출
        submitted = False
        for attempt in range(3):
            try:
                # 1차: na_comment (nariya 전용)
                self.driver.execute_script("na_comment('viewcomment');")
                time.sleep(1.0)
                alert_mid = self.handle_alert(accept=True, timeout=0.5)
                if alert_mid:
                    self.emit(f"comment submit alert (attempt {attempt+1}): {alert_mid}", "DEBUG")
                    if "오류" in alert_mid or "실패" in alert_mid or "로그인" in alert_mid:
                        return WriteResult(success=False, message=f"댓글 작성 실패: {alert_mid}")
                submitted = True
                break
            except Exception:
                pass
            try:
                # 2차: fviewcomment_submit (구 gnuboard)
                self.driver.execute_script(
                    "fviewcomment_submit(document.getElementById('fviewcomment'));"
                )
                time.sleep(0.8)
                alert_mid = self.handle_alert(accept=True, timeout=0.5)
                if alert_mid:
                    self.emit(f"comment submit alert (attempt {attempt+1}): {alert_mid}", "DEBUG")
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            # fallback: 버튼 직접 클릭 (disabled 이미 제거했으므로)
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, "#btn_submit")
                self.driver.execute_script("arguments[0].removeAttribute('disabled');", btn)
                btn.click()
                submitted = True
            except Exception:
                pass
            if not submitted:
                try:
                    self.driver.execute_script(
                        "var f = document.querySelector('#fviewcomment'); if(f) f.submit();"
                    )
                except Exception:
                    return WriteResult(success=False, message="댓글 제출 실패")

        time.sleep(1.5)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        # 댓글 등록 확인: 페이지에서 방금 작성한 내용이 있는지 검증
        try:
            # 페이지 새로고침하여 댓글 실제 등록 여부 확인
            self.driver.get(url)
            time.sleep(1.0)
            self.require_human_check()
            self.handle_alert(accept=True, timeout=0.5)

            page_src = self.driver.page_source or ""
            # 댓글 내용의 앞 20자로 확인 (HTML 인코딩 감안)
            check_text = content[:20].strip()
            if check_text and check_text in page_src:
                return WriteResult(success=True, message="댓글 작성 완료 (확인됨)")

            # fallback: 댓글 영역에서 현재 유저 닉네임 확인
            has_my_comment = self.driver.execute_script("""
                var comments = document.querySelectorAll('.comment-item, .cmt-content, .na-comment, .comment_box');
                var text = '';
                for (var i = 0; i < comments.length; i++) text += comments[i].textContent;
                return text;
            """) or ""
            if check_text in has_my_comment:
                return WriteResult(success=True, message="댓글 작성 완료 (확인됨)")

            # 내용 매칭 실패해도 댓글 수가 있으면 성공 처리 (HTML 인코딩 차이)
            self.emit(f"댓글 내용 확인 실패 (제출은 완료), check_text='{check_text[:15]}'", "WARNING")
            return WriteResult(success=True, message="댓글 제출 완료 (내용 확인 불가)")
        except Exception:
            # 확인 실패해도 제출은 됐으므로 성공 처리
            return WriteResult(success=True, message="댓글 제출 완료 (확인 스킵)")

    # ──────────────────────────────────────────────
    #  출석체크
    # ──────────────────────────────────────────────

    def checkin(self) -> dict[str, Any]:
        """출석체크 (gnuboard 출석 플러그인)."""
        from selenium.webdriver.common.by import By

        try:
            self.driver.get(f"{self.base_url}/plugin/attendance/attendance.php")
            time.sleep(2)
            self.require_human_check()

            for sel in [
                "input[type='submit'][value*='출석']",
                "button[type='submit']",
                "#attendance_submit",
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue

            alert_text = self.handle_alert(accept=True, timeout=2.0) or ""
            return {"success": True, "message": alert_text or "출석 페이지 방문"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    # ──────────────────────────────────────────────
    #  등업 버튼 클릭
    # ──────────────────────────────────────────────

    def _parse_guide_deficit(self) -> dict[str, Any] | None:
        """next 행에서 현재/필요 수치 파싱 → 부족분 계산.

        bamje guide.php 구조 (nariya gnuboard 테마):
          <thead>/<tr>: ... | 가입일 | 파운드 | 게시글 | 댓글 | 등업
          <tr class="next">: ... | 0/0 일 | 0/100P | 0/3개 | 0/5개 | [btn]

        파싱 전략:
          1차: 헤더 행 th/td 텍스트로 컬럼 인덱스를 동적 매핑
          2차: 셀 텍스트의 단위 키워드로 필드 추론
          3차: 숫자 pair가 있는 셀을 위치 순서대로 기본 필드에 매핑
        """
        import re as _re
        from selenium.webdriver.common.by import By

        # ── 유틸 ──
        def _parse_pair(text: str) -> tuple[int, int] | None:
            """'현재/필요' 형식 파싱. 예: '35/500P' → (35, 500). 없으면 None."""
            text = text.replace(",", "").replace(".", "").strip()
            m = _re.search(r"(-?\d+)\s*/\s*(-?\d+)", text)
            if m:
                cur, req = int(m.group(1)), int(m.group(2))
                return max(0, cur), max(0, req)
            return None

        _HEADER_MAP = {
            "가입": "days", "일수": "days", "기간": "days",
            "포인트": "points", "파운드": "points", "점수": "points",
            "후기": "reviews", "리뷰": "reviews",
            "게시": "posts", "글수": "posts", "작성글": "posts",
            "댓글": "comments", "코멘트": "comments",
        }

        _UNIT_MAP = {
            "일": "days",
            "P": "points", "p": "points", "파운드": "points", "포인트": "points",
            "후기": "reviews", "리뷰": "reviews",
            "게시": "posts",
            "댓글": "comments",
        }

        def _detect_field_from_text(text: str) -> str:
            """셀 텍스트의 단위 키워드로 필드 추론."""
            for keyword, field in _UNIT_MAP.items():
                if keyword in text:
                    return field
            return ""

        try:
            # tr.next 행 찾기 (bamje: class="next")
            row = None
            for sel in ["tr.next", "tr.nextlevel", "tr[class*='next']"]:
                try:
                    row = self.driver.find_element(By.CSS_SELECTOR, sel)
                    break
                except Exception:
                    continue
            if row is None:
                return None

            # td + th 모두 DOM 순서대로 수집 (header index와 정확히 일치시키기 위해)
            cells = row.find_elements(By.CSS_SELECTOR, "td, th")
            if len(cells) < 3:
                return None

            # ── 1차: 헤더 기반 컬럼 매핑 ──
            col_map: dict[int, str] = {}
            header_col_count = 0
            try:
                table = row.find_element(By.XPATH, "./ancestor::table[1]")
                header_rows = table.find_elements(By.CSS_SELECTOR, "thead tr, tr:first-child")
                for hr in header_rows:
                    ths = hr.find_elements(By.TAG_NAME, "th") or hr.find_elements(By.TAG_NAME, "td")
                    if len(ths) < 3:
                        continue
                    header_col_count = len(ths)
                    for idx, th in enumerate(ths):
                        th_text = th.text.strip()
                        for keyword, field in _HEADER_MAP.items():
                            if keyword in th_text:
                                col_map[idx] = field
                                break
                    if col_map:
                        break
            except Exception:
                pass

            # rowspan/colspan 보정: 헤더 컬럼 수 > 셀 수이면 앞쪽 컬럼 누락 (아이콘 등)
            if col_map and header_col_count > len(cells):
                offset = header_col_count - len(cells)
                col_map = {idx - offset: field for idx, field in col_map.items() if idx >= offset}

            # ── 파싱: 각 셀에서 현재/필요 pair 추출 ──
            current: dict[str, int] = {}
            required: dict[str, int] = {}
            # col_map이 있으면 매핑 기반, 없으면 키워드+위치 기반
            unnamed_pairs: list[tuple[int, int]] = []  # 키워드 매칭 실패한 pair

            for idx, cell in enumerate(cells):
                text = cell.text.strip()
                pair = _parse_pair(text)
                if pair is None:
                    continue

                cur, req = pair

                # 1) 헤더 매핑으로 필드 결정
                field = col_map.get(idx, "")

                # 2) 헤더 매핑 없으면 셀 텍스트 키워드로 추론
                if not field:
                    field = _detect_field_from_text(text)

                if field:
                    current[field] = cur
                    required[field] = req
                else:
                    unnamed_pairs.append((cur, req))

            # ── 3차: 미매핑 pair를 위치 순서로 기본 필드에 배정 ──
            default_order = ["days", "points", "posts", "comments", "reviews"]
            available_fields = [f for f in default_order if f not in current]
            for i, (cur, req) in enumerate(unnamed_pairs):
                if i < len(available_fields):
                    field = available_fields[i]
                    current[field] = cur
                    required[field] = req

            if not current and not required:
                return None

            # 기본 필드 보장 (누락 시 0/0)
            for f in ("days", "points", "posts", "comments"):
                current.setdefault(f, 0)
                required.setdefault(f, 0)

            deficit = {f: max(0, required[f] - current[f]) for f in current}

            return {"current": current, "required": required, "deficit": deficit}
        except Exception:
            return None

    def click_levelup(self) -> dict[str, Any]:
        """등업 버튼 클릭 (/page/guide.php 내 레벨업 컬럼)."""
        from selenium.webdriver.common.by import By

        try:
            self.driver.get(f"{self.base_url}/page/guide.php")
            time.sleep(2)
            self.require_human_check()

            # guide.php 의 등급 테이블에서 class="next" 행의 레벨업 버튼 찾기
            btn = None

            # 1) "next" 행 안의 버튼/링크 (조건 충족 시 생성됨)
            for sel in [
                "tr.next th a",
                "tr.next th button",
                "tr.next th input[type='submit']",
                "tr.next td:last-child a",
                "tr.next td:last-child button",
            ]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if el.is_displayed():
                        btn = el
                        break
                except Exception:
                    continue

            # 2) XPath 폴백 — 등업/레벨업 텍스트 버튼
            if not btn:
                for xp in [
                    "//tr[contains(@class,'next')]//a[contains(text(),'등업')]",
                    "//tr[contains(@class,'next')]//a[contains(text(),'레벨업')]",
                    "//tr[contains(@class,'next')]//button[contains(text(),'등업')]",
                    "//a[contains(text(),'등업신청')]",
                    "//button[contains(text(),'등업신청')]",
                ]:
                    try:
                        el = self.driver.find_element(By.XPATH, xp)
                        if el.is_displayed():
                            btn = el
                            break
                    except Exception:
                        continue

            if btn:
                btn.click()
                time.sleep(2)
                alert_text = self.handle_alert(accept=True, timeout=3.0) or ""
                return {"success": True, "message": alert_text or "등업 버튼 클릭 완료"}
            else:
                # 조건 미달 — deficit 파싱 시도
                deficit = self._parse_guide_deficit()
                result: dict[str, Any] = {"success": False}
                try:
                    self.driver.find_element(By.XPATH, "//tr[contains(@class,'next')]//span[contains(text(),'등업조건미달')]")
                    result["message"] = "등업조건미달 — 조건 충족 필요"
                except Exception:
                    result["message"] = "등업 버튼을 찾을 수 없음 (guide.php)"
                if deficit:
                    result["deficit"] = deficit
                return result
        except Exception as exc:
            try:
                self.driver.switch_to.alert.accept()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    # ──────────────────────────────────────────────
    #  사이트 안내 확인 (+100P)
    # ──────────────────────────────────────────────

    def confirm_site_guide(self) -> dict[str, Any]:
        """사이트 안내 페이지 방문/확인 (밤의제국 전용, +100 파운드).

        /page/guide.php 를 방문하면 자동으로 포인트가 지급되는 구조.
        확인 버튼이 있으면 클릭, 없으면 방문만으로 완료 처리.
        """
        from selenium.webdriver.common.by import By

        try:
            self.driver.get(f"{self.base_url}/page/guide.php")
            time.sleep(2)
            self.require_human_check()

            # 확인 버튼이 있으면 클릭
            for xp in [
                "//button[contains(text(),'확인')]",
                "//a[contains(text(),'확인')]",
                "//input[contains(@value,'확인')]",
                "//button[contains(text(),'사이트안내')]",
            ]:
                try:
                    el = self.driver.find_element(By.XPATH, xp)
                    if el.is_displayed():
                        el.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue

            alert_text = self.handle_alert(accept=True, timeout=2.0) or ""
            return {"success": True, "message": alert_text or "사이트 안내 확인 완료"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}
