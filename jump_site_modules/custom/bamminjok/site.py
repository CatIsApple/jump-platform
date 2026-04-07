"""BamminjokSite - 밤의민족 (Vue.js + Fastify)."""

from __future__ import annotations

import re
import time
from typing import Any

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    BaseSite,
)
from ...types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    Profile,
    WriteResult,
)


class BamminjokSite(BaseSite):
    SITE_NAME = "밤의민족"

    # ──────────────────────────────────────────────
    #  헬퍼
    # ──────────────────────────────────────────────

    def _dismiss_popups(self) -> None:
        """SweetAlert / JS alert 닫기."""
        try:
            self.driver.execute_script("""
                var btn = document.querySelector('.swal2-confirm');
                if (btn) { btn.click(); return; }
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (t === '확인' || t === 'OK' || t === 'ok') { buttons[i].click(); return; }
                }
            """)
            time.sleep(0.2)
        except Exception:
            pass
        try:
            alert = self.driver.switch_to.alert
            alert.accept()
            time.sleep(0.2)
        except Exception:
            pass

    # ──────────────────────────────────────────────
    #  로그인
    # ──────────────────────────────────────────────

    def login(self) -> LoginResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        _acct = {"mb_id": self.username, "mb_password": self.password}

        self.naver_warmup(sleep_s=0.5)

        # Clear cookies to prevent stale proxy sessions from interfering
        try:
            self.driver.delete_all_cookies()
            self.emit("[밤의민족] 쿠키 초기화 완료")
        except Exception:
            pass

        self.goto(self.base_url)
        self.require_human_check()

        # Pre-check: if already logged in (proxy IP session, etc.), skip form.
        try:
            _src = self.driver.page_source or ""
            if "로그아웃" in _src or "/logout" in _src:
                self.emit("[밤의민족] 이미 로그인 상태 — 폼 로그인 건너뜀")
                return LoginResult(success=True, method="session", message="이미 로그인됨", account=_acct)
        except Exception:
            pass

        # Clear cookies AGAIN after base page visit to prevent /login redirect
        try:
            self.driver.delete_all_cookies()
        except Exception:
            pass

        # Login retries: this site often has unstable Vue render timing.
        id_selectors = [
            "input[name='id']",
            "input#id",
            "input[name='mb_id']",
            "input[type='text']",
        ]
        pw_selectors = [
            "input[name='password']",
            "input#password",
            "input[name='mb_password']",
            "input[type='password']",
        ]
        submit_selectors = [
            "#login_form_submit",
            "button[type='submit']",
            "input[type='submit']",
            "button.btn-primary",
            "button.btn",
        ]

        def _pick_first(selector_list: list[str]):
            for sel in selector_list:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        return els[0]
                except Exception:
                    continue
            return None

        max_attempts = 3
        last_msg = "로그인 실패"

        for attempt in range(1, max_attempts + 1):
            self.goto("/login")
            time.sleep(1.0)
            self.require_human_check()
            self._dismiss_popups()

            try:
                # Wait for password field — unique to login form (avoids search bar false match)
                WebDriverWait(self.driver, 30).until(
                    lambda d: any(d.find_elements(By.CSS_SELECTOR, s) for s in pw_selectors)
                )
            except Exception:
                last_msg = "로그인 폼 로드 실패"
                if attempt < max_attempts:
                    self.emit(f"[밤의민족] 로그인 재시도 {attempt + 1}/{max_attempts} - {last_msg}", "WARN")
                    continue
                return LoginResult(success=False, method="form", message=last_msg, account=_acct)

            try:
                id_el = _pick_first(id_selectors)
                pw_el = _pick_first(pw_selectors)
                if not id_el or not pw_el:
                    raise RuntimeError("login input not found")

                id_el.clear()
                id_el.send_keys(self.username)
                pw_el.clear()
                pw_el.send_keys(self.password)

                submitted = False
                for sel in submit_selectors:
                    try:
                        btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                        self.driver.execute_script("arguments[0].click();", btn)
                        submitted = True
                        break
                    except Exception:
                        continue

                if not submitted:
                    submitted = bool(
                        self.driver.execute_script(
                            """
                            var f = document.querySelector('form');
                            if (!f) return false;
                            var b = f.querySelector('button[type="submit"], input[type="submit"]');
                            if (b) { b.click(); return true; }
                            if (typeof f.submit === 'function') { f.submit(); return true; }
                            return false;
                            """
                        )
                    )

                if not submitted:
                    raise RuntimeError("login submit not found")
            except Exception:
                last_msg = "로그인 폼 입력/제출 실패"
                if attempt < max_attempts:
                    self.emit(f"[밤의민족] 로그인 재시도 {attempt + 1}/{max_attempts} - {last_msg}", "WARN")
                    continue
                return LoginResult(success=False, method="form", message=last_msg, account=_acct)

            time.sleep(1.2)
            self.require_human_check()
            self._dismiss_popups()

            try:
                cur_url = self.driver.current_url or ""
            except Exception:
                cur_url = ""
            try:
                src = self.driver.page_source or ""
            except Exception:
                src = ""

            is_logged_in = False
            try:
                els = self.driver.find_elements(
                    By.XPATH,
                    "//*[contains(@href,'/logout') or contains(text(),'로그아웃')]",
                )
                is_logged_in = len(els) > 0
            except Exception:
                pass
            if not is_logged_in and ("로그아웃" in src or "/logout" in src):
                is_logged_in = True
            if not is_logged_in and "/login" not in cur_url and "/join" not in cur_url:
                is_logged_in = True

            if is_logged_in:
                return LoginResult(success=True, method="form", message="로그인 성공", account=_acct)

            try:
                msg = self.driver.execute_script(
                    """
                    var el = document.querySelector('.swal2-html-container, .alert-danger, .text-danger, .invalid-feedback');
                    return el ? el.textContent.trim() : '';
                    """
                ) or ""
            except Exception:
                msg = ""
            last_msg = f"로그인 실패: {msg}" if msg else "로그인 실패"
            if attempt < max_attempts:
                self.emit(f"[밤의민족] 로그인 재시도 {attempt + 1}/{max_attempts} - {last_msg}", "WARN")
                continue

        return LoginResult(success=False, method="form", message=last_msg, account=_acct)

    # ──────────────────────────────────────────────
    #  점프
    # ──────────────────────────────────────────────

    def jump(self) -> JumpResult:
        import json

        # Get store_id
        self.goto("/page/broker/store")
        time.sleep(0.7)
        self.require_human_check()

        try:
            store_id = self.driver.execute_script("""
                const link = document.querySelector('a[href*="/store/"][href*="#jump"]');
                if (link) {
                    const m = link.href.match(/\\/store\\/([a-f0-9]+)/);
                    return m ? m[1] : null;
                }
                const el = document.querySelector('#kt_content');
                if (el && el.__vue__ && el.__vue__.storeList) {
                    const stores = el.__vue__.storeList;
                    if (stores.length > 0) return stores[0]._id;
                }
                return null;
            """)
        except Exception:
            store_id = None

        if not store_id:
            return JumpResult(status=STATUS_FAILED, message="업소(store_id)를 찾을 수 없습니다")

        # POST jump
        try:
            result = self.driver.execute_script(
                """
                return (async () => {
                    const r = await fetch('/page/broker/store/"""
                + store_id
                + """/jump', {
                        method: 'POST',
                        headers: { 'X-Requested-With': 'XMLHttpRequest' }
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                })();
                """
            )
        except Exception as exc:
            return JumpResult(status=STATUS_FAILED, message=f"점프 API 요청 실패: {exc}")

        if not result or not isinstance(result, dict):
            return JumpResult(status=STATUS_FAILED, message="점프 응답 파싱 실패")

        status_code = result.get("status", 0)
        body = (result.get("body") or "").strip()

        self.emit(f"[밤의민족] 점프 응답: HTTP {status_code}, body={body[:200]}", "DEBUG")

        if status_code == 200 and body == "true":
            return JumpResult(status=STATUS_SUCCESS, message="점프 완료")

        # JSON response
        try:
            data = json.loads(body)
            msg = data.get("message") or data.get("msg") or data.get("error") or ""
            if msg:
                status, result_msg = self.classify_result(msg)
                if status != STATUS_UNKNOWN:
                    return JumpResult(status=status, message=result_msg)
                return JumpResult(status=STATUS_FAILED, message=msg)
        except Exception:
            pass

        if body == "false":
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="점프 실패 (서버 응답: false - 횟수 소진 또는 대기)",
            )

        body_status, body_msg = self.classify_result(body)
        if body_status != STATUS_UNKNOWN:
            return JumpResult(status=body_status, message=body_msg)

        return JumpResult(
            status=STATUS_FAILED,
            message=f"점프 실패 (HTTP {status_code}, body={body[:100]})",
        )

    # ──────────────────────────────────────────────
    #  회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """밤의민족 회원가입.

        /join → /join/user (일반회원)
        필드: id, password, password_check, email, nickname, sector, area, sub_
        캡차: SVG 기반 이미지 캡차 (input[name='captcha'])
        Select2 + Vue.js 연동 필수.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_email = kwargs.get("mb_email", "")
        mb_name = kwargs.get("mb_name", mb_nick)
        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_nick)",
                account=acct,
            )

        # 일반회원 가입 페이지로 이동
        self.goto("/join/user")
        time.sleep(0.5)
        self.require_human_check()
        self._dismiss_popups()

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='id']")
                )
            )
        except Exception:
            return LoginResult(
                success=False, method="register",
                message="회원가입 폼 로드 실패",
                account=acct,
            )

        try:
            # 아이디
            id_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='id']")
            id_el.clear()
            id_el.send_keys(mb_id)

            # 비밀번호
            pw_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            pw_el.clear()
            pw_el.send_keys(mb_password)

            # 비밀번호 확인
            pw_re = self.driver.find_element(By.CSS_SELECTOR, "input[name='password_check']")
            pw_re.clear()
            pw_re.send_keys(mb_password)

            # 이메일
            if mb_email:
                email_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='email']")
                email_el.clear()
                email_el.send_keys(mb_email)

            # 닉네임
            nick_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='nickname']")
            nick_el.clear()
            nick_el.send_keys(mb_nick)
        except Exception as exc:
            return LoginResult(
                success=False, method="register",
                message=f"폼 필드 입력 실패: {exc}",
                account=acct,
            )

        time.sleep(0.2)

        # ── 캡차 + 제출 재시도 루프 (최대 3회 - 2captcha 오답 대응) ──
        max_submit_attempts = int(kwargs.get("captcha_retry_attempts", 5))
        max_submit_attempts = max(3, min(max_submit_attempts, 8))
        for submit_attempt in range(max_submit_attempts):
            if submit_attempt > 0:
                self.emit(f"[밤의민족] 캡차 재시도 ({submit_attempt + 1}/{max_submit_attempts})...", "INFO")
                # SweetAlert 닫기
                try:
                    self.driver.execute_script("""
                        var btn = document.querySelector('.swal2-confirm');
                        if (btn) btn.click();
                    """)
                    time.sleep(0.5)
                except Exception:
                    pass
                # 캡차 새로고침
                try:
                    self.driver.execute_script("""
                        var findVue = function(el) {
                            while (el) { if (el.__vue__) return el.__vue__; el = el.parentElement; }
                            return null;
                        };
                        var el = document.querySelector('input[name="captcha"]');
                        var vm = findVue(el);
                        if (vm && vm.getCaptcha) vm.getCaptcha();
                    """)
                    time.sleep(0.5)
                except Exception:
                    pass

            # ── SVG 캡차 해결 ──
            # 첫 시도에서도 getCaptcha() 호출 (초기 렌더링 시 SVG가 비어있을 수 있음)
            if submit_attempt == 0:
                try:
                    self.driver.execute_script("""
                        var findVue = function(el) {
                            while (el) { if (el.__vue__) return el.__vue__; el = el.parentElement; }
                            return null;
                        };
                        var el = document.querySelector('input[name="captcha"]');
                        var vm = findVue(el);
                        if (vm && vm.getCaptcha) vm.getCaptcha();
                    """)
                    time.sleep(1.0)
                except Exception:
                    pass

            captcha_solved = False
            try:
                captcha_input = self.driver.find_element(By.CSS_SELECTOR, "input[name='captcha']")
                if captcha_input.is_displayed() and self._captcha_api_key:
                    captcha_input.clear()
                    captcha_solved = self._solve_svg_captcha(captcha_input)

                if not captcha_solved:
                    if self._captcha_api_key:
                        self.emit("[밤의민족] 자동 캡차 풀이 실패 - 새 캡차로 재시도", "WARN")
                        continue
                    self.emit("[밤의민족] 캡차 수동 입력 대기 (30초)...", "INFO")
                    for _ in range(60):
                        val = (captcha_input.get_attribute("value") or "").strip()
                        if len(val) == 6:
                            captcha_solved = True
                            break
                        time.sleep(0.5)
            except Exception:
                pass

            time.sleep(0.3)

            # ── getCaptcha() Vue re-render로 초기화된 텍스트 필드 재설정 ──
            try:
                self.driver.execute_script("""
                    var set = function(name, val) {
                        var el = document.querySelector('input[name="' + name + '"]');
                        if (el && el.value !== val) { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); }
                    };
                    set('id', arguments[0]);
                    set('password', arguments[1]);
                    set('password_check', arguments[1]);
                    set('email', arguments[2]);
                    set('nickname', arguments[3]);
                """, mb_id, mb_password, mb_email, mb_nick)
            except Exception:
                pass

            # ── Select2 + Vue: 선호 업종 / 활동 지역 / 상세 지역 ──
            # 캡차 해결 후에 설정해야 getCaptcha() re-render로 리셋되지 않음.
            try:
                self.driver.execute_script("""
                    (function() {
                        var $ = window.jQuery;
                        if (!$) return;
                        var formEl = document.querySelectorAll('form.form')[0]
                                  || document.querySelectorAll('form')[1];
                        if (!formEl) return;

                        $(formEl).find('select[name="sector"]')
                            .val("건마").trigger('change').trigger('change.select2');
                        $(formEl).find('select[name="area"]')
                            .val("서울(강남)").trigger('change').trigger('change.select2');

                        var findVue = function(el) {
                            while (el) { if (el.__vue__) return el.__vue__; el = el.parentElement; }
                            return null;
                        };
                        var sub = formEl.querySelector('select[name="sub_"]');
                        var vm = findVue(sub);
                        if (vm && vm.areaList) {
                            var obj = vm.areaList.find(function(a) { return a.title === "서울(강남)"; });
                            if (obj && obj.subList) vm.subList = obj.subList;
                        }
                    })();
                """)
                time.sleep(0.5)

                sub_result = self.driver.execute_script("""
                    var $ = window.jQuery;
                    var formEl = document.querySelectorAll('form.form')[0]
                              || document.querySelectorAll('form')[1];
                    if (!formEl) return 'no_form';
                    var $sub = $(formEl).find('select[name="sub_"]');
                    var opts = $sub.find('option');
                    var count = opts.length;
                    for (var i = 0; i < opts.length; i++) {
                        if (opts[i].value && opts[i].value !== '') {
                            $sub.val(opts[i].value).trigger('change').trigger('change.select2');
                            return 'set:' + opts[i].value + ' (opts=' + count + ')';
                        }
                    }
                    return 'no_valid_option (opts=' + count + ')';
                """)
                self.emit(f"[밤의민족] sub_ 설정: {sub_result}", "DEBUG")
            except Exception as exc:
                self.emit(f"[밤의민족] Select 필드 설정 실패: {exc}", "WARN")

            time.sleep(0.3)

            # ── 제출: Vue submit() 메서드 직접 호출 ──
            try:
                submit_result = self.driver.execute_script("""
                    var findVue = function(el) {
                        while (el) { if (el.__vue__) return el.__vue__; el = el.parentElement; }
                        return null;
                    };
                    var el = document.querySelector('input[name="captcha"]');
                    var vm = findVue(el);
                    if (vm && vm.submit) {
                        vm.submit();
                        return 'vue_submit';
                    }
                    var buttons = document.querySelectorAll('button[type="submit"].btn-primary');
                    for (var i = 0; i < buttons.length; i++) {
                        var t = buttons[i].textContent.trim();
                        if (t.indexOf('회원가입') !== -1) { buttons[i].click(); return 'button_click'; }
                    }
                    return 'not_found';
                """)
                self.emit(f"[밤의민족] 제출 방식: {submit_result}", "DEBUG")
            except Exception as exc:
                return LoginResult(
                    success=False, method="register",
                    message=f"회원가입 제출 실패: {exc}",
                    account=acct,
                )

            # SweetAlert 대기 (최대 15초)
            swal_text = ""
            for _ in range(30):
                time.sleep(0.5)
                try:
                    swal_text = self.driver.execute_script("""
                        var el = document.querySelector('.swal2-html-container, .swal2-content, .swal2-title');
                        return el ? el.textContent.trim() : '';
                    """) or ""
                    if swal_text:
                        break
                except Exception:
                    pass
                try:
                    cur = self.driver.current_url or ""
                    if "/join" not in cur:
                        swal_text = "페이지 이동 확인"
                        break
                except Exception:
                    pass

            self.require_human_check()

            if swal_text:
                self.emit(f"[밤의민족] 가입 결과 메시지: {swal_text}", "DEBUG")

            # 캡차 오류 → 재시도
            if (
                "불일치" in swal_text
                or "그림" in swal_text
                or "captcha" in swal_text.lower()
                or "6자리" in swal_text
            ):
                self.emit(f"[밤의민족] 캡차 오답 감지: {swal_text}", "WARN")
                continue

            # SweetAlert 성공 여부 판별
            is_success = False
            if "완료" in swal_text or "성공" in swal_text:
                is_success = True
            elif "페이지 이동" in swal_text:
                is_success = True

            # SweetAlert 확인 버튼 클릭 (있으면)
            try:
                self.driver.execute_script("""
                    var btn = document.querySelector('.swal2-confirm');
                    if (btn) btn.click();
                """)
                time.sleep(1.0)
            except Exception:
                pass

            current = self.driver.current_url or ""

            # 성공: SweetAlert 메시지 or /join에서 벗어남
            if is_success or "/join" not in current:
                return LoginResult(
                    success=True, method="register",
                    message=f"회원가입 완료 ({swal_text})",
                    account=acct,
                )

            # FV (FormValidation) 에러 확인
            fv_errors = []
            try:
                fv_errors = self.driver.execute_script("""
                    var errs = document.querySelectorAll('.fv-plugins-message-container .fv-help-block, .invalid-feedback:not(:empty)');
                    var msgs = [];
                    for (var i = 0; i < errs.length; i++) {
                        var t = errs[i].textContent.trim();
                        if (t) msgs.push(t);
                    }
                    return msgs;
                """) or []
            except Exception:
                pass

            err_detail = swal_text or ", ".join(fv_errors) or f"URL={current}"
            return LoginResult(
                success=False, method="register",
                message=f"회원가입 실패 ({err_detail})",
                account=acct,
            )

        # 모든 재시도 소진
        return LoginResult(
            success=False, method="register",
            message=f"회원가입 실패 (캡차 {max_submit_attempts}회 시도 초과)",
            account=acct,
        )

    def _solve_svg_captcha(self, captcha_input: Any) -> bool:
        """SVG 캡차를 2captcha로 해결.

        SVG를 브라우저에서 Canvas로 변환 후 PNG base64로 추출하여
        2captcha에 전송. 대소문자 구분 (caseSensitive).
        """
        try:
            import requests

            time.sleep(0.3)  # SVG 렌더링 대기

            # SVG → Canvas → PNG base64 변환 (execute_async_script 사용)
            img_b64 = self.driver.execute_async_script("""
                var done = arguments[arguments.length - 1];
                try {
                    var svg = document.querySelector('svg[viewBox="0,0,150,50"]')
                           || document.querySelector('svg[viewBox="0 0 150 50"]');
                    if (!svg) {
                        // fallback: captcha 입력 근처의 SVG 찾기
                        var container = document.querySelector('input[name="captcha"]');
                        if (container) {
                            var parent = container.closest('.row') || container.parentElement.parentElement;
                            if (parent) svg = parent.querySelector('svg');
                        }
                    }
                    if (!svg) { done(null); return; }

                    var svgData = new XMLSerializer().serializeToString(svg);
                    var canvas = document.createElement('canvas');
                    canvas.width = 300;
                    canvas.height = 100;
                    var ctx = canvas.getContext('2d');

                    // 흰색 배경
                    ctx.fillStyle = '#FFFFFF';
                    ctx.fillRect(0, 0, 300, 100);

                    var img = new Image();
                    img.onload = function() {
                        ctx.drawImage(img, 0, 0, 300, 100);
                        var dataUrl = canvas.toDataURL('image/png');
                        done(dataUrl.replace('data:image/png;base64,', ''));
                    };
                    img.onerror = function() { done(null); };
                    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
                } catch(e) {
                    done(null);
                }
            """)

            if not img_b64:
                self.emit("[밤의민족] SVG→PNG 변환 실패", "WARN")
                return False

            self.emit("[밤의민족] 2Captcha 캡차 풀이 중...", "INFO")

            resp = requests.post(
                "http://2captcha.com/in.php",
                data={
                    "key": self._captcha_api_key,
                    "method": "base64",
                    "body": img_b64,
                    "numeric": 0,
                    "min_len": 6,
                    "max_len": 6,
                    "regsense": 1,
                    "case_sensitive": 1,
                    "json": 1,
                },
                timeout=30,
            )
            req_data = resp.json()
            if req_data.get("status") == 1:
                captcha_id = req_data["request"]
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
                        code_raw = str(res_data["request"] or "")
                        code = re.sub(r"[^0-9A-Za-z]", "", code_raw).strip()
                        if len(code) != 6:
                            self.emit(f"[밤의민족] 캡차 코드 길이 비정상: raw='{code_raw}' parsed='{code}'", "WARN")
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
                            return False
                        captcha_input.clear()
                        captcha_input.send_keys(code)
                        self.emit(f"[밤의민족] 캡차 자동 해결: {code}", "INFO")
                        return True
                    if "CAPCHA_NOT_READY" not in res_data.get("request", ""):
                        self.emit(f"[밤의민족] 2Captcha 오류: {res_data}", "WARN")
                        break
            else:
                self.emit(f"[밤의민족] 2Captcha 요청 실패: {req_data}", "WARN")

        except Exception as exc:
            self.emit(f"[밤의민족] 캡차 자동 해결 실패: {exc}", "ERROR")

        return False

    # ──────────────────────────────────────────────
    #  프로필 조회
    # ──────────────────────────────────────────────

    def get_profile(self) -> Profile:
        """마이페이지에서 프로필 조회.

        밤의민족 프로필 페이지: /page/user/user
        """
        from . import parsers

        self.emit("[밤의민족] 프로필 조회 시작", "INFO")

        self.driver.get(f"{self.base_url}/page/user/user")
        time.sleep(0.8)
        self.require_human_check()

        source = self.driver.page_source or ""
        profile = parsers.parse_profile(source)
        self.emit(f"[밤의민족] 프로필: {profile.nickname} lv={profile.level} pt={profile.point}", "INFO")
        return profile

    # ──────────────────────────────────────────────
    #  게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """밤의민족 게시판 목록 가져오기."""
        from . import parsers

        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        return parsers.parse_boards(self.driver.page_source)

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
        """특정 게시판 게시글 목록 스크래핑.

        URL: /board/{board_id}/list?page={page}
        검색: search_field, search_text 쿼리 파라미터 추가.
        """
        from . import parsers

        url = f"{self.base_url}/board/{board_id}/list?page={page}"
        if search_text:
            from urllib.parse import quote
            if search_field:
                url += f"&search_field={quote(search_field)}"
            url += f"&search_text={quote(search_text)}"
        if sort_field:
            url += f"&sort_field={sort_field}"
        if sort_order:
            url += f"&sort_order={sort_order}"

        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()

        # DataTables가 로드될 때까지 대기
        for _ in range(10):
            row_count = self.driver.execute_script("""
                var t = document.querySelector('#bm_board_datatables tbody');
                return t ? t.querySelectorAll('tr').length : 0;
            """) or 0
            if row_count > 0:
                break
            time.sleep(1.0)

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기.

        URL: /board/{board_id}/view/{post_id}
        """
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'board_id:post_id' 형식)")

        url = f"{self.base_url}/board/{board_id}/view/{post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()

        return parsers.parse_comments(self.driver.page_source, post_id=post_id)

    # ──────────────────────────────────────────────
    #  게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성.

        URL: /board/{board_id}/write
        에디터: Quill (.ql-editor)
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/board/{board_id}/write"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()
        self._dismiss_popups()

        cur = self.driver.current_url or ""
        if "/login" in cur:
            return WriteResult(success=False, message="로그인이 필요합니다.")
        try:
            page_text = self.driver.execute_script(
                "return document.body ? document.body.innerText.substring(0, 300) : ''"
            ) or ""
            if "접근 권한 없음" in page_text or "부터 접근 가능" in page_text:
                return WriteResult(success=False, message=f"접근 권한 없음 (랭크 부족): {board_id}")
            if "403" in page_text[:50]:
                return WriteResult(success=False, message="접근 권한이 없습니다. (403)")
        except Exception:
            pass

        # 제목 입력
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='title'], input[name='subject']")
                )
            )
        except Exception:
            return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        subj_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='title'], input[name='subject']")
        subj_el.clear()
        subj_el.send_keys(subject)

        # 내용 입력: Quill 에디터 우선, fallback으로 textarea
        content_filled = False
        try:
            ql_editor = self.driver.find_element(By.CSS_SELECTOR, ".ql-editor")
            if ql_editor.is_displayed():
                self.driver.execute_script(
                    "arguments[0].innerHTML = arguments[1];",
                    ql_editor, f"<p>{content}</p>",
                )
                content_filled = True
        except Exception:
            pass

        if not content_filled:
            try:
                ta = self.driver.find_element(
                    By.CSS_SELECTOR, "textarea[name='content'], textarea[name='wr_content']"
                )
                ta.clear()
                ta.send_keys(content)
                content_filled = True
            except Exception:
                pass

        if not content_filled:
            return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.3)

        # 제출 버튼
        try:
            btn = self.driver.find_element(
                By.XPATH,
                "//button[contains(text(),'등록') or contains(text(),'작성') or @type='submit']"
            )
            btn.click()
        except Exception:
            return WriteResult(success=False, message="게시글 제출 버튼 클릭 실패")

        time.sleep(1.0)
        self.require_human_check()
        self._dismiss_popups()

        current = self.driver.current_url or ""

        # 성공: /view/ 페이지로 이동 또는 /list로 이동
        if "/write" not in current:
            post_id_m = re.search(r"/view/(\d+)", current)
            post_id = post_id_m.group(1) if post_id_m else ""
            return WriteResult(success=True, id=post_id, message="게시글 작성 완료")

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ──────────────────────────────────────────────
    #  댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, *, board_id: str = "") -> WriteResult:
        """특정 게시글에 댓글 작성.

        Quill 에디터 사용: #quill_reply_content .ql-editor
        폼: #reply_write_form
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'board_id:post_id' 형식)")

        url = f"{self.base_url}/board/{board_id}/view/{post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()
        self._dismiss_popups()

        # 접근 권한 체크 (랭크 부족)
        try:
            page_text = self.driver.execute_script(
                "return document.body ? document.body.innerText.substring(0, 300) : ''"
            ) or ""
            if "접근 권한 없음" in page_text or "부터 접근 가능" in page_text:
                return WriteResult(success=False, message=f"접근 권한 없음 (랭크 부족): {board_id}")
        except Exception:
            pass

        # 댓글 Quill 에디터에 내용 입력
        comment_filled = False
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#quill_reply_content .ql-editor, form#reply_write_form .ql-editor")
                )
            )
            ql_editor = self.driver.find_element(
                By.CSS_SELECTOR, "#quill_reply_content .ql-editor, form#reply_write_form .ql-editor"
            )
            self.driver.execute_script(
                "arguments[0].innerHTML = arguments[1];",
                ql_editor, f"<p>{content}</p>",
            )
            comment_filled = True
        except Exception:
            pass

        # fallback: textarea
        if not comment_filled:
            try:
                ta = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "textarea[name='content'], textarea[name='comment'], textarea.reply-input"
                )
                ta.clear()
                ta.send_keys(content)
                comment_filled = True
            except Exception:
                pass

        if not comment_filled:
            return WriteResult(success=False, message="댓글 입력 폼을 찾을 수 없습니다. (로그인 필요 가능)")

        time.sleep(0.3)

        # 제출: reply_write_form 내부 버튼 또는 "등록" 버튼
        try:
            btn = self.driver.execute_script("""
                var form = document.querySelector('form#reply_write_form');
                if (form) {
                    var btn = form.querySelector('button[type="submit"], button');
                    if (btn) return btn;
                }
                var buttons = document.querySelectorAll('button');
                for (var i = buttons.length - 1; i >= 0; i--) {
                    var t = buttons[i].textContent.trim();
                    if (t === '등록' && !buttons[i].closest('#bm_board_view_replylist div.row')) {
                        return buttons[i];
                    }
                }
                return null;
            """)
            if btn:
                btn.click()
            else:
                return WriteResult(success=False, message="댓글 제출 버튼을 찾을 수 없습니다.")
        except Exception:
            return WriteResult(success=False, message="댓글 제출 실패")

        time.sleep(1.0)
        self.require_human_check()
        self._dismiss_popups()

        # SweetAlert 내용 확인 (성공/실패 판별)
        _ERROR_KW = ("권한", "등급", "레벨", "랭크", "부족", "오류", "실패", "error", "denied", "잠시 후")
        try:
            swal = self.driver.execute_script("""
                var el = document.querySelector('.swal2-html-container, .swal2-content, .swal2-popup');
                return el ? el.textContent.trim() : '';
            """) or ""
        except Exception:
            swal = ""

        if swal:
            try:
                self.driver.execute_script("""
                    var btn = document.querySelector('.swal2-confirm');
                    if (btn) btn.click();
                """)
            except Exception:
                pass
            if any(kw in swal for kw in _ERROR_KW):
                return WriteResult(success=False, message=f"댓글 거부 (SweetAlert): {swal[:120]}")

        # 페이지 본문에서도 오류 키워드 확인
        try:
            page_text = self.driver.execute_script(
                "return document.body ? document.body.innerText.substring(0, 500) : ''"
            ) or ""
            if "접근 권한 없음" in page_text or "부터 접근 가능" in page_text:
                return WriteResult(success=False, message=f"접근 권한 없음 (랭크 부족): {board_id}")
        except Exception:
            pass

        return WriteResult(success=True, message=f"댓글 작성 완료 ({swal})" if swal else "댓글 작성 완료")

    # ──────────────────────────────────────────────
    #  출석체크
    # ──────────────────────────────────────────────

    def checkin(self) -> dict[str, Any]:
        """출석체크 (밤의민족: /page/cs/attend)."""
        try:
            self.goto("/page/cs/attend")
            time.sleep(1.5)
            self._dismiss_popups()

            # 출석 버튼 클릭
            result = self.driver.execute_script("""
                var btns = document.querySelectorAll('button, a.btn');
                for (var i = 0; i < btns.length; i++) {
                    var t = btns[i].textContent.trim();
                    if (t.includes('출석') || t.includes('체크')) {
                        btns[i].click();
                        return '출석 버튼 클릭';
                    }
                }
                return '출석 버튼 없음 (페이지 방문)';
            """)

            time.sleep(1)
            self._dismiss_popups()
            return {"success": True, "message": result or "출석 페이지 방문"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    # ──────────────────────────────────────────────
    #  등업 — 수동 (활동 기반, 관리자 판단)
    # ──────────────────────────────────────────────

    def click_levelup(self) -> dict[str, Any]:
        """밤의민족은 등업 버튼이 없음 (관리자가 활동량 보고 수동 승인).
        등업신청은 write_post(board_id='greet', ...)로 건의글 작성.
        이 메서드는 호환성을 위해 존재."""
        return {"success": False, "message": "밤의민족은 수동등업 (건의글 필요)"}
