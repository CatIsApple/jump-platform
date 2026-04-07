"""OpartSite - 오피아트."""

from __future__ import annotations

import random
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


class OpartSite(GnuboardSite):
    SITE_NAME = "오피아트"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_ID_SELECTOR = "#login_id"
    LOGIN_PW_SELECTOR = "#login_pw"
    LOGIN_SUBMIT_SELECTOR = "input.btn_submit[type='submit']"
    LOGIN_CHECK_VIA_SOURCE = True  # page_source에서 검색 (더 안정적)
    LOGIN_CHECK_ALT_TEXT = "logout"  # 영문 로그아웃 텍스트도 체크

    def _post_login_alerts(self) -> None:
        """Handle post-login alerts and swal popups."""
        from selenium.webdriver.common.alert import Alert
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # native alert
        try:
            WebDriverWait(self.driver, 2).until(EC.alert_is_present())
            Alert(self.driver).accept()
        except Exception:
            pass

        # swal2 popup
        try:
            swal_btn = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.swal2-confirm"))
            )
            swal_btn.click()
        except Exception:
            pass

        # close popups
        try:
            popups = self.driver.find_elements(By.CSS_SELECTOR, "button.hd_pops_close")
            for p in popups:
                try:
                    p.click()
                except Exception:
                    pass
        except Exception:
            pass

    def jump(self) -> JumpResult:
        # Navigate to main to find wr_id
        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        try:
            wr_id = self.driver.execute_script("""
                const el = document.querySelector('[onclick*="jump("]');
                if (!el) return null;
                const m = (el.getAttribute('onclick') || '').match(/jump\\((\\d+)\\)/);
                return m ? m[1] : null;
            """)
        except Exception:
            wr_id = None

        if not wr_id:
            return JumpResult(status=STATUS_FAILED, message="점프 버튼(wr_id)을 찾을 수 없습니다")

        # fetch jump via AJAX
        try:
            response_text = self.driver.execute_script(
                """
                return (async () => {
                    const r = await fetch('/bbs/list_jump.php?wr_id="""
                + str(wr_id)
                + """&page=');
                    return await r.text();
                })();
                """
            )
        except Exception as exc:
            return JumpResult(status=STATUS_FAILED, message=f"점프 요청 실패: {exc}")

        self.save_cookies(self.COOKIE_KEYS)

        if not response_text:
            return JumpResult(status=STATUS_FAILED, message="점프 응답 없음")

        # Extract alert('...') content
        alert_match = re.search(r"alert\(['\"](.+?)['\"]\)", response_text)
        alert_msg = alert_match.group(1) if alert_match else response_text.strip()

        if "완료" in alert_msg or "적용" in alert_msg:
            return JumpResult(status=STATUS_SUCCESS, message=alert_msg)
        if "중지" in alert_msg:
            return JumpResult(status=STATUS_FAILED, message=alert_msg)
        if "분에 한번" in alert_msg or "대기" in alert_msg or "10분" in alert_msg or "잠시 후" in alert_msg:
            return JumpResult(status=STATUS_COOLDOWN, message=alert_msg)
        if "횟수" in alert_msg and ("없" in alert_msg or "부족" in alert_msg):
            return JumpResult(status=STATUS_INSUFFICIENT, message=alert_msg)
        if "회원만" in alert_msg or "로그인" in alert_msg:
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message=alert_msg)

        return JumpResult(status=STATUS_FAILED, message=f"점프 결과: {alert_msg}")

    # ──────────────────────────────────────────────
    #  0. 프로필
    # ──────────────────────────────────────────────

    def get_profile(self):
        """로그인된 계정의 프로필 정보 가져오기.

        mypage.php가 외부 도메인으로 리다이렉트되는 사이트이므로,
        메인 페이지 헤더 영역에서 닉네임을 추출.
        """
        from . import parsers
        from ...types import Profile

        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()
        self._post_login_alerts()
        profile = parsers.parse_profile(self.driver.page_source)
        if profile.nickname:
            return profile

        # JS 기반 닉네임 추출 (여러 패턴 시도)
        try:
            nick = self.driver.execute_script("""
                // 1. 로그아웃 링크 근처에서 닉네임 찾기
                var logout = document.querySelector('a[href*="logout"]');
                if (logout) {
                    var parent = logout.parentElement;
                    while (parent && parent !== document.body) {
                        var texts = parent.querySelectorAll('b, strong, span.name, .member, .sv_member');
                        var skip = ['로그인', '회원가입', '모바일', '출석부', '로그아웃', '마이페이지', '쪽지'];
                        for (var i = 0; i < texts.length; i++) {
                            var t = texts[i].textContent.trim();
                            if (t && t.length < 20 && skip.indexOf(t) === -1 && !/^[a-z]+$/i.test(t)) return t;
                        }
                        // "님" 패턴
                        var allText = parent.innerText || '';
                        var nimMatch = allText.match(/([가-힣a-zA-Z0-9_]{2,15})\\s*님/);
                        if (nimMatch) return nimMatch[1];
                        parent = parent.parentElement;
                    }
                }
                // 2. .tnb 영역
                var tnb = document.querySelector('.tnb, #tnb, .head_top');
                if (tnb) {
                    var bs = tnb.querySelectorAll('b, strong');
                    var skip2 = ['로그인', '회원가입', '모바일', '출석부', '로그아웃'];
                    for (var j = 0; j < bs.length; j++) {
                        var t2 = bs[j].textContent.trim();
                        if (t2 && t2.length < 20 && skip2.indexOf(t2) === -1) return t2;
                    }
                    var lis = tnb.querySelectorAll('li, a');
                    for (var k = 0; k < lis.length; k++) {
                        var text = lis[k].textContent.trim();
                        var nimM = text.match(/^([가-힣a-zA-Z0-9_]{2,15})\\s*님/);
                        if (nimM) return nimM[1];
                    }
                }
                // 3. 헤더 전체에서 "님" 패턴
                var hd = document.querySelector('#hd, header, .header');
                if (hd) {
                    var hdText = hd.innerText || '';
                    var nimMatch2 = hdText.match(/([가-힣a-zA-Z0-9_]{2,15})\\s*님/);
                    if (nimMatch2) return nimMatch2[1];
                }
                // 4. 사이드바 프로필
                var sidebar = document.querySelector('.sidebar, #aside, .snb');
                if (sidebar) {
                    var nameEl = sidebar.querySelector('.name, .member, .nick, b, strong');
                    if (nameEl) {
                        var t3 = nameEl.textContent.trim();
                        if (t3 && t3.length < 20) return t3;
                    }
                }
                return '';
            """)
            if nick:
                return Profile(nickname=nick)
        except Exception:
            pass

        return profile

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """오피아트 회원가입 (2단계: 회원유형 선택 → 폼 입력)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_name = kwargs.get("mb_name", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_email = kwargs.get("mb_email", f"{mb_id}@gmail.com")
        mb_hp = kwargs.get("mb_hp", f"010-{random.randint(1000,9999)}-{random.randint(1000,9999)}")

        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_name, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_name, mb_nick)",
                account=acct,
            )

        # 일반회원 가입 폼 직접 이동
        self.goto("/bbs/register_form.php?gubun=1")
        time.sleep(0.5)
        self.require_human_check()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                )
            )
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 폼 로드 실패", account=acct)

        # 기본 필드 입력
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

        # 지역 선택 (mb_2) - 필수
        try:
            from selenium.webdriver.support.ui import Select
            region_el = self.driver.find_element(By.CSS_SELECTOR, "#mb_2, select[name='mb_2']")
            select = Select(region_el)
            options = [o.get_attribute("value") for o in select.options if o.get_attribute("value")]
            if options:
                select.select_by_value(random.choice(options))
        except Exception:
            pass

        nick_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_nick, input[name='mb_nick']")
        nick_el.clear()
        nick_el.send_keys(mb_nick)

        # 이메일 (필수)
        try:
            email_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_email, input[name='mb_email']")
            email_el.clear()
            email_el.send_keys(mb_email)
        except Exception:
            pass

        # 휴대폰 (필수)
        try:
            hp_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_hp, input[name='mb_hp']")
            hp_el.clear()
            hp_el.send_keys(mb_hp)
        except Exception:
            pass

        time.sleep(0.3)

        # AJAX 중복 체크 + 버튼 활성화
        self._gnuboard_ajax_check()

        # KCaptcha 처리
        max_captcha_attempts = 3
        result_alert = ""

        for captcha_attempt in range(max_captcha_attempts):
            if captcha_attempt > 0:
                self.emit(f"[{self.SITE_NAME}] 캡차 재시도 ({captcha_attempt + 1}/{max_captcha_attempts})...", "INFO")
                self.goto("/bbs/register_form.php?gubun=1")
                time.sleep(0.5)
                self.require_human_check()
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "#reg_mb_id"))
                    )
                except Exception:
                    break
                # 재입력
                for sel, val in [
                    ("#reg_mb_id", mb_id), ("#reg_mb_password", mb_password),
                    ("#reg_mb_password_re", mb_password), ("#reg_mb_name", mb_name),
                    ("#reg_mb_nick", mb_nick), ("#reg_mb_email", mb_email),
                    ("#reg_mb_hp", mb_hp),
                ]:
                    try:
                        el = self.driver.find_element(By.CSS_SELECTOR, sel)
                        el.clear()
                        el.send_keys(val)
                    except Exception:
                        pass
                try:
                    from selenium.webdriver.support.ui import Select
                    region_el = self.driver.find_element(By.CSS_SELECTOR, "#mb_2")
                    select = Select(region_el)
                    options = [o.get_attribute("value") for o in select.options if o.get_attribute("value")]
                    if options:
                        select.select_by_value(random.choice(options))
                except Exception:
                    pass
                time.sleep(0.3)

            # 캡차 풀기
            captcha_solved = False
            try:
                captcha_el = self.driver.find_element(By.CSS_SELECTOR, "#captcha_key")
                captcha_el.clear()

                if self._captcha_api_key:
                    img_b64 = None
                    try:
                        img_b64 = self.driver.execute_script("""
                            var img = document.getElementById('captcha_img');
                            if (!img || !img.complete || img.naturalWidth === 0) return null;
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
                        try:
                            captcha_img = self.driver.find_element(By.CSS_SELECTOR, "#captcha_img")
                            img_b64 = captcha_img.screenshot_as_base64
                        except Exception:
                            pass

                    if img_b64 and len(img_b64) >= 100:
                        import requests
                        self.emit(f"[{self.SITE_NAME}] 2Captcha 캡차 풀이 중...", "INFO")
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
                        if req_data.get("status") == 1:
                            captcha_id = req_data["request"]
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
                                if "CAPCHA_NOT_READY" not in res_data.get("request", ""):
                                    break

                            if code and len(code) >= 4 and code.isdigit():
                                captcha_el.clear()
                                captcha_el.send_keys(code)
                                captcha_solved = True
                                self.emit(f"[{self.SITE_NAME}] 캡차 자동 해결: {code}", "INFO")

                if not captcha_solved:
                    self.emit(f"[{self.SITE_NAME}] 캡차 자동 해결 실패", "WARNING")
            except Exception:
                pass

            # 제출
            try:
                submit_btn = self.driver.find_element(By.CSS_SELECTOR, "#btn_submit, input[type='submit']")
                submit_btn.click()
            except Exception:
                return LoginResult(success=False, method="register", message="회원가입 제출 버튼 클릭 실패", account=acct)

            time.sleep(1.0)
            self.require_human_check()

            result_alert = self.handle_alert(accept=True, timeout=2.0) or ""
            if not result_alert:
                swal_text = self.handle_swal(click_confirm=True) or ""
                if swal_text:
                    result_alert = swal_text

            if "자동등록방지" in result_alert:
                if captcha_attempt < max_captcha_attempts - 1:
                    continue
            break

        # 결과 판단
        if result_alert:
            if "완료" in result_alert or "가입" in result_alert or "환영" in result_alert:
                return LoginResult(success=True, method="register", message=f"회원가입 성공: {result_alert}", account=acct)
            if "자동등록방지" in result_alert:
                return LoginResult(success=False, method="register", message=f"회원가입 실패(캡차): {result_alert}", account=acct)
            if "이미" in result_alert or "중복" in result_alert:
                return LoginResult(success=False, method="register", message=f"회원가입 실패(중복): {result_alert}", account=acct)
            return LoginResult(success=False, method="register", message=f"회원가입 실패: {result_alert}", account=acct)

        # JS redirect 대기
        time.sleep(1.0)

        current = self.driver.current_url
        if "register" not in current:
            return LoginResult(success=True, method="register", message="회원가입 완료 (페이지 이동 확인)", account=acct)

        # 페이지 소스에서 성공 메시지 확인
        try:
            src = self.driver.page_source or ""
            if "완료" in src and "회원" in src:
                return LoginResult(success=True, method="register", message="회원가입 완료 (페이지 내 확인)", account=acct)
            if "로그인" in src and "register" not in src.lower()[:500]:
                return LoginResult(success=True, method="register", message="회원가입 완료 (로그인 페이지 이동)", account=acct)
        except Exception:
            pass

        return LoginResult(success=False, method="register", message="회원가입 결과 확인 불가", account=acct)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """오피아트 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url)
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
        """특정 게시판 게시글 목록 스크래핑.

        검색 시 폼 기반 제출 (URL 직접 접근 시 ERR_CONNECTION_RESET 방지).
        """
        from . import parsers

        if search_field and search_text:
            # 검색: 게시판 먼저 로드 후 검색 폼 제출 (URL 차단 방지)
            base_url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
            if sort_field:
                base_url += f"&sst={sort_field}"
            if sort_order:
                base_url += f"&sod={sort_order}"
            self.driver.get(base_url)
            time.sleep(0.5)
            self.require_human_check()
            self.driver.execute_script(
                """
                var form = document.querySelector('form[name="fsearch"]');
                if (form) {
                    var sfl = form.querySelector('select[name="sfl"]');
                    var stx = form.querySelector('input[name="stx"]');
                    if (sfl) sfl.value = arguments[0];
                    if (stx) stx.value = arguments[1];
                    form.submit();
                }
                """,
                search_field,
                search_text,
            )
            time.sleep(0.7)
            self.require_human_check()
        else:
            url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
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
        self._post_login_alerts()

        comments = parsers.parse_comments(self.driver.page_source, post_id=post_id)
        if comments:
            return comments

        # JS 기반 댓글 추출 fallback (HTML 파서가 실패할 경우)
        try:
            js_comments = self.driver.execute_script("""
                var results = [];
                // 패턴 1: div.media[id^="c_"] (APMS Op 테마 - opguide 스타일)
                var medias = document.querySelectorAll('div.media[id^="c_"]');
                for (var i = 0; i < medias.length; i++) {
                    var el = medias[i];
                    var id = el.id.replace(/^c_/, '');
                    var authorEl = el.querySelector('.member');
                    var author = '';
                    if (authorEl) {
                        // member span 내 마지막 텍스트 노드
                        var nodes = authorEl.childNodes;
                        for (var n = nodes.length - 1; n >= 0; n--) {
                            var txt = (nodes[n].textContent || '').trim();
                            if (txt && nodes[n].nodeType === 3) { author = txt; break; }
                            if (txt && nodes[n].tagName !== 'IMG' && nodes[n].tagName !== 'SPAN') { author = txt; break; }
                        }
                        if (!author) author = authorEl.textContent.trim();
                    }
                    // 내용: textarea#save_comment_XXX 또는 .media-content
                    var ta = document.getElementById('save_comment_' + id);
                    var content = ta ? ta.value || ta.textContent : '';
                    if (!content) {
                        var mc = el.querySelector('.media-content');
                        if (mc) {
                            // textarea 제외한 텍스트
                            var clone = mc.cloneNode(true);
                            var tas = clone.querySelectorAll('textarea');
                            for (var t = 0; t < tas.length; t++) tas[t].remove();
                            content = clone.textContent.trim();
                        }
                    }
                    content = content.trim();
                    var dateEl = el.querySelector('.media-info');
                    var date = '';
                    if (dateEl) {
                        var dm = dateEl.textContent.match(/\\d{4}[.\\-/]\\d{1,2}[.\\-/]\\d{1,2}\\s+\\d{2}:\\d{2}/);
                        date = dm ? dm[0] : dateEl.textContent.trim();
                    }
                    if (author || content) {
                        results.push({id: id, author: author, content: content, date: date});
                    }
                }
                if (results.length > 0) return results;

                // 패턴 2: id가 c_ 또는 comment_로 시작하는 요소
                var els = document.querySelectorAll('[id^="c_"], [id^="comment_"]');
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    if (el.classList.contains('media')) continue; // 이미 처리됨
                    var id = el.id.replace(/^c_|^comment_/, '');
                    var authorEl = el.querySelector('.member, .sv_member, .comment-name a, .cmt_name, b.name');
                    var author = authorEl ? authorEl.textContent.trim() : '';
                    var contentEl = el.querySelector('.cmt_contents, .cmt_textbox, .comment-cont-txt, .cmt_content, .comment-text, .media-content');
                    var content = contentEl ? contentEl.textContent.trim() : '';
                    var dateEl = el.querySelector('.cmt_date, .comment-time, .datetime, .media-info, time');
                    var date = dateEl ? dateEl.textContent.trim() : '';
                    if (author || content) {
                        results.push({id: id, author: author, content: content, date: date});
                    }
                }
                if (results.length > 0) return results;

                // 패턴 3: .comment-item 등 클래스
                var items = document.querySelectorAll('.comment-item, .cmt_item, .view-comment-item');
                for (var j = 0; j < items.length; j++) {
                    var item = items[j];
                    var idAttr = item.id || '';
                    var cid = idAttr.replace(/^c_|^comment_/, '') || String(j);
                    var a2 = item.querySelector('.member, .sv_member, .comment-name a, b.name');
                    var c2 = item.querySelector('.cmt_contents, .cmt_textbox, .comment-cont-txt');
                    var d2 = item.querySelector('.cmt_date, .comment-time, time');
                    if ((a2 && a2.textContent.trim()) || (c2 && c2.textContent.trim())) {
                        results.push({
                            id: cid,
                            author: a2 ? a2.textContent.trim() : '',
                            content: c2 ? c2.textContent.trim() : '',
                            date: d2 ? d2.textContent.trim() : ''
                        });
                    }
                }
                return results;
            """)
            if js_comments:
                for jc in js_comments:
                    comments.append(Comment(
                        id=str(jc.get("id", "")),
                        post_id=post_id,
                        author=jc.get("author", ""),
                        content=jc.get("content", ""),
                        date=jc.get("date", ""),
                    ))
        except Exception:
            pass

        return comments

    # ──────────────────────────────────────────────
    #  6. 게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str, **kwargs: Any) -> WriteResult:
        """게시글 작성.

        가입인사(join) 게시판은 별도 로직:
          분류(ca_name), 연령대(wr_1 select), 가입경로(wr_2 input) 등 추가 필드.
        일반 게시판은 CKEditor 기반.

        kwargs:
            extra_fields: dict - 추가 필드 값 (예: {"wr_1": "30대", "wr_2": "인터넷 검색"})
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        extra_fields = kwargs.get("extra_fields", {})

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()
        self._post_login_alerts()

        # 리다이렉트 체크 (외부 도메인으로 이동할 수 있음)
        current = self.driver.current_url
        if "login" in current:
            return WriteResult(success=False, message="로그인이 필요합니다.")
        if self.domain not in current:
            return WriteResult(success=False, message="외부 도메인으로 리다이렉트됨 (로그인 필요 가능)")

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='wr_subject']"))
            )
        except Exception:
            return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        # ── 모든 select 드롭다운 처리 (분류, 연령대 등) ──
        # extra_fields에 특정 값이 있으면 해당 값 사용, 없으면 첫 번째 유효 옵션
        try:
            self.driver.execute_script("""
                var extraFields = arguments[0];
                var form = document.getElementById('fwrite') || document.querySelector('form[name="fwrite"]')
                         || document.querySelector('form[action*="write_update"]');
                if (!form) return;
                var selects = form.querySelectorAll('select');
                selects.forEach(function(sel) {
                    var name = sel.name;
                    if (!name) return;
                    // extra_fields에 값이 지정된 경우
                    if (extraFields[name]) {
                        for (var i = 0; i < sel.options.length; i++) {
                            if (sel.options[i].value === extraFields[name] || sel.options[i].text === extraFields[name]) {
                                sel.selectedIndex = i;
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                return;
                            }
                        }
                    }
                    // 값 미지정 + 아직 선택 안 됨 → 첫 번째 유효 옵션
                    if (sel.value === '' || sel.selectedIndex <= 0) {
                        for (var i = 1; i < sel.options.length; i++) {
                            if (sel.options[i].value) {
                                sel.value = sel.options[i].value;
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                break;
                            }
                        }
                    }
                });
            """, extra_fields)
        except Exception:
            pass

        # ── 제목 입력 ──
        subj_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='wr_subject']")
        subj_el.clear()
        subj_el.send_keys(subject)

        # ── 추가 텍스트 필드 채우기 (가입경로 등) ──
        # 폼 내 wr_subject/wr_content 외의 text input 자동 채우기
        try:
            self.driver.execute_script("""
                var extraFields = arguments[0];
                var boardId = arguments[1];
                var form = document.getElementById('fwrite') || document.querySelector('form[name="fwrite"]')
                         || document.querySelector('form[action*="write_update"]');
                if (!form) return;
                var inputs = form.querySelectorAll('input[type="text"]');
                inputs.forEach(function(inp) {
                    var name = inp.name;
                    if (!name || name === 'wr_subject') return;
                    // extra_fields에 값이 지정된 경우
                    if (extraFields[name]) {
                        inp.value = extraFields[name];
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        return;
                    }
                    // 빈 필드에 기본값 채우기 (가입인사 게시판)
                    if (!inp.value && boardId === 'join') {
                        if (name.indexOf('wr_') === 0) {
                            // 가입경로 등 wr_N 필드
                            inp.value = '인터넷 검색';
                            inp.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                    }
                });
            """, extra_fields, board_id)
        except Exception:
            pass

        # ── 내용 입력: CKEditor JS API → iframe → textarea ──
        content_filled = False

        # 방법 1: CKEditor JS API (가장 안정적)
        try:
            result = self.driver.execute_script("""
                if (typeof CKEDITOR !== 'undefined') {
                    for (var name in CKEDITOR.instances) {
                        CKEDITOR.instances[name].setData(arguments[0]);
                        return true;
                    }
                }
                return false;
            """, content)
            if result:
                content_filled = True
        except Exception:
            pass

        # 방법 2: CKEditor iframe 직접
        if not content_filled:
            try:
                iframe = self.driver.find_element(By.CSS_SELECTOR, ".cke_wysiwyg_frame, iframe.cke_wysiwyg_frame")
                self.driver.switch_to.frame(iframe)
                body = self.driver.find_element(By.TAG_NAME, "body")
                body.clear()
                body.send_keys(content)
                self.driver.switch_to.default_content()
                content_filled = True
            except Exception:
                self.driver.switch_to.default_content()

        # 방법 3: textarea 직접 (가입인사 등 CKEditor 없는 게시판)
        if not content_filled:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea[name='wr_content'], #wr_content")
                self.driver.execute_script(
                    "arguments[0].style.display='block'; arguments[0].value=arguments[1];", ta, content
                )
                content_filled = True
            except Exception:
                pass

        if not content_filled:
            return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.3)

        # 팝업 배너 닫기 (클릭 차단 방지)
        try:
            self.driver.execute_script("""
                document.querySelectorAll('.popup_banner, .popup_banner1, .hd_pops').forEach(
                    function(el) { el.style.display = 'none'; }
                );
            """)
        except Exception:
            pass

        # 제출
        try:
            btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "button[type='submit'], input[type='submit'], "
                "#btn_submit, .btn_submit"
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
                self.driver.execute_script("""
                    var f = document.getElementById('fwrite')
                        || document.querySelector('form[name="fwrite"]')
                        || document.querySelector('form[action*="write_update"]');
                    if (f) f.submit();
                """)
            except Exception:
                return WriteResult(success=False, message="게시글 제출 버튼 클릭 실패")

        time.sleep(1.0)
        self.require_human_check()

        # alert/swal 처리 (성공 alert도 accept)
        alert_text = self.detect_form_result(timeout=2.0) or ""
        if alert_text and ("오류" in alert_text or "실패" in alert_text or "권한" in alert_text):
            return WriteResult(success=False, message=f"작성 실패: {alert_text}")

        swal_text = self.handle_swal(click_confirm=True) or ""
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"작성 실패: {swal_text}")

        # alert accept 후 페이지 이동 대기
        time.sleep(1.0)

        current = self.driver.current_url
        wr_id_m = re.search(r"wr_id=(\d+)", current)
        wr_id = wr_id_m.group(1) if wr_id_m else ""

        if "write.php" not in current:
            return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")

        # 페이지 소스에서 성공 확인
        try:
            src = self.driver.page_source or ""
            # URL에서 못 찾았으면 page source에서 wr_id 추출
            if not wr_id:
                wr_m = re.search(r'wr_id=(\d+)', src)
                if wr_m:
                    wr_id = wr_m.group(1)
            if not wr_id:
                # 현재 URL 재확인 (alert accept 후 리다이렉트)
                current2 = self.driver.current_url
                wr_m2 = re.search(r'wr_id=(\d+)', current2)
                if wr_m2:
                    wr_id = wr_m2.group(1)
                if "write.php" not in current2:
                    return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")
            if "작성" in src and "완료" in src:
                return WriteResult(success=True, id=wr_id, message="게시글 작성 완료 (페이지 내 확인)")
            if wr_id:
                return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")
        except Exception:
            pass

        return WriteResult(success=False, message=f"게시글 작성 결과 확인 불가 (alert: {alert_text})")

    # ──────────────────────────────────────────────
    #  7. 댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, *, board_id: str = "") -> WriteResult:
        """특정 게시글에 댓글 작성.

        여러 gnuboard 테마 패턴 순서대로 시도:
        1. APMS: textarea#comment + fnCommentSubmit
        2. eyoom: form[name='fviewcomment'] textarea + fviewcomment_submit
        3. 표준 gnuboard: #fviewcomment textarea[name='wr_content']
        4. JS fallback: 모든 textarea 탐색
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url)
        time.sleep(1.2)
        self.require_human_check()
        self._post_login_alerts()

        # 권한 없음 alert 처리
        alert_text = self.handle_alert(accept=True, timeout=1.0) or ""
        if alert_text and ("권한" in alert_text or "로그인" in alert_text):
            return WriteResult(success=False, message=f"접근 불가: {alert_text}")

        # 댓글 섹션으로 스크롤
        try:
            self.driver.execute_script("""
                var vc = document.querySelector('#bo_vc, .comment-media, .comment-write, form[name="fcomment"]');
                if (vc) vc.scrollIntoView({behavior: 'instant', block: 'center'});
                else window.scrollTo(0, document.body.scrollHeight);
            """)
            time.sleep(1.0)
        except Exception:
            pass

        # JS로 댓글 textarea 찾기 + 내용 입력 + 제출 (모든 패턴 통합)
        result = self.driver.execute_script("""
            var content = arguments[0];

            // CKEditor 댓글 인스턴스 확인
            if (typeof CKEDITOR !== 'undefined') {
                for (var name in CKEDITOR.instances) {
                    var inst = CKEDITOR.instances[name];
                    var el = inst.element && inst.element.$;
                    // 글쓰기 폼 제외 (댓글 폼만)
                    if (el && el.closest && el.closest('form[name="fwrite"]')) continue;
                    inst.setData(content);
                    // 해당 textarea에도 값 설정
                    if (el) el.value = content;
                    // 폼 제출
                    if (typeof fnCommentSubmit === 'function') {
                        try { fnCommentSubmit(0, 0); return 'ckeditor_fnCommentSubmit'; } catch(e) {}
                    }
                    var form = el && el.closest ? el.closest('form') : null;
                    if (form) {
                        var btn = form.querySelector('button[type="submit"], input[type="submit"]');
                        if (btn) { btn.click(); return 'ckeditor_btn'; }
                        form.submit();
                        return 'ckeditor_form';
                    }
                }
            }

            // textarea 찾기 (우선순위 순)
            var ta = document.querySelector('textarea#comment')
                  || document.querySelector('form[name="fcomment"] textarea[name="wr_content"]')
                  || document.querySelector('form[name="fcomment"] textarea')
                  || document.querySelector('form[name="fviewcomment"] textarea[name="wr_content"]')
                  || document.querySelector('#fviewcomment textarea[name="wr_content"]')
                  || document.querySelector('section#bo_vc textarea[name="wr_content"]')
                  || document.querySelector('.comment-media textarea[name="wr_content"]')
                  || document.querySelector('textarea[name="wr_content"]')
                  || document.querySelector('textarea[name="comment_content"]')
                  || document.querySelector('.comment-form textarea');

            if (!ta) {
                // 마지막 시도: 페이지의 textarea 중 댓글용 찾기
                var allTa = document.querySelectorAll('textarea');
                for (var i = 0; i < allTa.length; i++) {
                    var t = allTa[i];
                    // 글쓰기 폼의 textarea 제외
                    if (t.closest('form[name="fwrite"]') || t.closest('#fwrite')) continue;
                    // save_comment textarea 제외 (기존 댓글 수정용)
                    if (t.id && t.id.indexOf('save_comment') === 0) continue;
                    // display:none이면서 offsetHeight도 0인건 제외
                    if (t.offsetHeight === 0 && t.style.display === 'none') continue;
                    ta = t;
                    break;
                }
            }

            if (!ta) return 'no_textarea';

            // 내용 입력
            ta.value = content;
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            ta.dispatchEvent(new Event('change', {bubbles: true}));

            // 제출 시도 (우선순위 순)
            // 1. APMS fnCommentSubmit
            if (typeof fnCommentSubmit === 'function') {
                try { fnCommentSubmit(0, 0); return 'fnCommentSubmit'; } catch(e) {}
            }
            // 2. eyoom fviewcomment_submit
            if (typeof fviewcomment_submit === 'function') {
                var form = ta.closest('form');
                if (form) { try { fviewcomment_submit(form); return 'fviewcomment_submit'; } catch(e) {} }
            }
            // 3. apms_comment_submit
            if (typeof apms_comment_submit === 'function') {
                try { apms_comment_submit(); return 'apms_comment_submit'; } catch(e) {}
            }
            // 4. 댓글 폼 내 제출 버튼 클릭
            var form = ta.closest('form');
            if (form) {
                var btn = form.querySelector('button[type="submit"], input[type="submit"], #btn_submit2, .btn_comment, .comment_save');
                if (btn) { btn.click(); return 'btn_click'; }
                form.submit();
                return 'form_submit';
            }
            // 5. 버튼만이라도 클릭
            var anyBtn = document.querySelector('.comment_save, .btn_comment, #btn_comment_submit, #bo_vc button[type="submit"]');
            if (anyBtn) { anyBtn.click(); return 'any_btn_click'; }

            return 'no_submit';
        """, content)

        if result == "no_textarea":
            # 댓글 폼이 없는 경우: 여러 gnuboard 댓글 엔드포인트 시도
            self.emit(f"[{self.SITE_NAME}] 직접 POST 시도: bo_table={board_id}, wr_id={post_id}", "DEBUG")
            endpoints = [
                '/bbs/comment_update.php',
                '/bbs/ajax.comment_update.php',
                '/bbs/write_comment_update.php',
                '/plugin/apms_comment/comment_update.php',
                '/bbs/comment_update2.php',
            ]
            try:
                post_result = self.driver.execute_script("""
                    var endpoints = arguments[3];
                    var bo_table = arguments[0], wr_id = arguments[1], content = arguments[2];

                    // 페이지 JS에서 comment 관련 URL 찾기
                    var scripts = document.querySelectorAll('script');
                    for (var i = 0; i < scripts.length; i++) {
                        var txt = scripts[i].textContent || scripts[i].innerHTML;
                        var m = txt.match(/['"]([/][^'"]*comment[^'"]*update[^'"]*)['"]/i);
                        if (m) endpoints.unshift(m[1]);
                    }
                    // 모든 a/form에서 comment 관련 URL 찾기
                    var links = document.querySelectorAll('a[href*="comment"], form[action*="comment"]');
                    for (var i = 0; i < links.length; i++) {
                        var href = links[i].href || links[i].action || '';
                        if (href) {
                            var path = new URL(href, location.origin).pathname;
                            endpoints.unshift(path);
                        }
                    }

                    var results = [];
                    for (var j = 0; j < endpoints.length; j++) {
                        try {
                            var formData = new FormData();
                            formData.append('w', '');
                            formData.append('bo_table', bo_table);
                            formData.append('wr_id', wr_id);
                            formData.append('comment_id', '');
                            formData.append('wr_content', content);
                            formData.append('sca', '');
                            formData.append('sfl', '');
                            formData.append('stx', '');
                            formData.append('spt', '');
                            formData.append('page', '1');

                            var xhr = new XMLHttpRequest();
                            xhr.open('POST', endpoints[j], false);
                            xhr.withCredentials = true;
                            xhr.send(formData);
                            results.push({endpoint: endpoints[j], status: xhr.status, text: xhr.responseText.substring(0, 500)});
                            if (xhr.status === 200 && xhr.responseText.length > 10 && xhr.responseText.indexOf('File not found') < 0) {
                                return {found: true, endpoint: endpoints[j], status: xhr.status, text: xhr.responseText.substring(0, 500)};
                            }
                        } catch(e) {
                            results.push({endpoint: endpoints[j], error: e.message});
                        }
                    }
                    return {found: false, tried: results};
                """, board_id, post_id, content, endpoints)
                self.emit(f"[{self.SITE_NAME}] POST 결과: {post_result}", "DEBUG")
                if post_result and post_result.get("found"):
                    resp_text = post_result.get("text", "")
                    import re as _re
                    alert_m = _re.search(r"alert\(['\"](.+?)['\"]\)", resp_text)
                    if alert_m:
                        alert_msg = alert_m.group(1)
                        if "권한" in alert_msg or "로그인" in alert_msg or "레벨" in alert_msg:
                            return WriteResult(success=False, message=f"댓글 권한 없음: {alert_msg}")
                        return WriteResult(success=True, message=f"댓글 작성 완료 ({alert_msg})")
                    if "location.replace" in resp_text or "location.href" in resp_text:
                        return WriteResult(success=True, message="댓글 작성 완료 (리다이렉트)")
                    return WriteResult(success=True, message="댓글 작성 완료 (직접 POST)")
            except Exception as e:
                self.emit(f"[{self.SITE_NAME}] POST 예외: {e}", "DEBUG")
            return WriteResult(success=False, message="댓글 입력 폼을 찾을 수 없습니다. (댓글 비활성화 또는 로그인 필요)")

        if result == "no_submit":
            return WriteResult(success=False, message="댓글 제출 방법을 찾을 수 없습니다.")

        time.sleep(1.0)
        self.require_human_check()

        alert_text = self.detect_form_result(timeout=2.0) or ""
        if alert_text and ("오류" in alert_text or "실패" in alert_text or "권한" in alert_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        swal_text = self.handle_swal(click_confirm=True) or ""
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"댓글 작성 실패: {swal_text}")

        return WriteResult(success=True, message="댓글 작성 완료")
