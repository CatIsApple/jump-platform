"""OpguideSite - 오피가이드."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

from ...base import (
    STATUS_COOLDOWN,
    STATUS_INSUFFICIENT,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
)
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


class OpguideSite(GnuboardSite):
    SITE_NAME = "오피가이드"
    COOKIE_KEYS = ["__data_a"]
    LOGIN_ID_SELECTOR = "#login_id"
    LOGIN_PW_SELECTOR = "#login_pw"
    LOGIN_SUBMIT_SELECTOR = "button[type='submit'].btn-login"
    LOGIN_PRE_SUBMIT_DELAY = 1.5
    LOGIN_POST_SUBMIT_DELAY = 1.5
    NAVER_WARMUP_SLEEP = 0.3
    GOTO_VIA_SCRIPT = True
    DIRECT_LOGIN = True
    LOGIN_CHECK_VIA_SOURCE = True

    @staticmethod
    def _extract_announced_domains(text: str) -> list[str]:
        out: list[str] = []
        for d in re.findall(r"\b(opga\d+\.(?:com|net))\b", text or "", flags=re.I):
            host = d.lower().strip()
            if host and host not in out:
                out.append(host)
        return out

    def _maybe_switch_announced_domain(self) -> bool:
        """공지문에서 최신 접속 도메인을 감지하면 base_url을 전환.

        현재 도메인이 이미 공지된 도메인 목록에 포함되어 있으면
        전환하지 않는다 (ping-pong 방지).
        """
        try:
            text = self.driver.execute_script(
                "return document.body ? document.body.innerText : '';"
            ) or ""
        except Exception:
            text = ""

        if ("접속 주소" not in text and "회원가입이 불가" not in text and "메인주소를 이용" not in text):
            return False

        domains = self._extract_announced_domains(text)
        if not domains:
            return False

        current_host = (urlparse(self.base_url).hostname or "").lower().strip()

        # 현재 도메인이 이미 공지 목록에 있으면 전환 불필요 (ping-pong 방지)
        if current_host in domains:
            return False

        for host in domains:
            if host and host != current_host:
                old = self.base_url
                self.domain = host
                self.emit(f"[오피가이드] 안내문 감지: 도메인 전환 {old} -> {self.base_url}", "WARN")
                return True
        return False

    def _warmup_site(self) -> None:
        """메인 페이지를 먼저 로드하여 CF 쿠키를 설정하고 도메인 전환을 감지."""
        self.naver_warmup(sleep_s=0.5)
        # 메인 페이지 로드 (Wix 랜딩 페이지 - CF 없음)
        self.driver.get(self.base_url)
        time.sleep(4.0)
        self.handle_alert(accept=True, timeout=1.0)
        if self._maybe_switch_announced_domain():
            self.driver.get(self.base_url)
            time.sleep(4.0)
            self.handle_alert(accept=True, timeout=1.0)

    def _navigate_to_login(self) -> None:
        self._warmup_site()
        login_url = self.url(self.LOGIN_URL_PATH)
        self.goto(login_url, via_script=True)
        time.sleep(4.0)
        self.handle_alert(accept=True, timeout=1.0)
        if self._maybe_switch_announced_domain():
            login_url = self.url(self.LOGIN_URL_PATH)
            self.goto(login_url, via_script=True)
            time.sleep(4.0)
            self.handle_alert(accept=True, timeout=1.0)
        # 로그인 폼이 보이면 Turnstile은 _fill_login_form에서 처리
        # 전체 페이지 CF 챌린지(로그인 폼 없음)만 require_human_check 처리
        try:
            has_form = self.driver.execute_script(
                "return !!document.getElementById('login_id');"
            )
        except Exception:
            has_form = False
        if not has_form:
            self.require_human_check()

    def _solve_turnstile(self) -> bool:
        """로그인 폼 내 Cloudflare Turnstile 위젯 감지 및 해결.

        1단계: Turnstile 자동 검증 대기 (최대 15초)
        2단계: 자동 검증 실패 시 2Captcha로 해결

        Returns:
            True: Turnstile 없음 또는 해결 성공
            False: 해결 실패
        """
        # Turnstile 위젯 존재 여부 확인 (iframe 또는 data-sitekey)
        has_turnstile = self.driver.execute_script("""
            return !!(
                document.querySelector('.cf-turnstile[data-sitekey]') ||
                document.querySelector('div[data-sitekey]') ||
                document.querySelector('iframe[src*="challenges.cloudflare.com"]')
            );
        """)
        if not has_turnstile:
            return True  # Turnstile 없으면 통과

        self.emit("[오피가이드] Cloudflare Turnstile 감지됨 - 자동 검증 대기", "INFO")

        # 1단계: 자동 검증 대기 (최대 15초) - Turnstile은 봇 아닌 경우 자동 통과
        for wait_i in range(30):
            token_exists = self.driver.execute_script("""
                // cf-turnstile-response hidden input에 값이 채워졌는지 확인
                var inputs = document.querySelectorAll(
                    'input[name="cf-turnstile-response"]'
                );
                for (var i = 0; i < inputs.length; i++) {
                    if (inputs[i].value && inputs[i].value.length > 10) return true;
                }
                // data-sitekey 컨테이너 내 hidden input 확인
                var containers = document.querySelectorAll('[data-sitekey]');
                for (var j = 0; j < containers.length; j++) {
                    var inp = containers[j].querySelector('input[type="hidden"]');
                    if (inp && inp.value && inp.value.length > 10) return true;
                }
                return false;
            """)
            if token_exists:
                self.emit(
                    f"[오피가이드] Turnstile 자동 검증 완료 ({wait_i * 0.5:.1f}초)",
                    "INFO",
                )
                return True
            time.sleep(0.5)

        self.emit("[오피가이드] Turnstile 자동 검증 실패 → 2Captcha 폴백", "INFO")

        # 2단계: 2Captcha로 해결
        sitekey = self.driver.execute_script("""
            var el = document.querySelector('.cf-turnstile[data-sitekey]')
                  || document.querySelector('div[data-sitekey]');
            if (el) return el.getAttribute('data-sitekey');
            var iframes = document.querySelectorAll(
                'iframe[src*="challenges.cloudflare"]'
            );
            for (var i = 0; i < iframes.length; i++) {
                var m = iframes[i].src.match(/[?&]sitekey=([^&]+)/);
                if (m) return m[1];
            }
            return null;
        """)

        if not sitekey:
            self.emit("[오피가이드] Turnstile sitekey 추출 실패 (로그인 계속 시도)", "WARN")
            return False

        if not self._captcha_api_key:
            self.emit(
                "[오피가이드] 2Captcha API 키 없음 - Turnstile 미해결 (로그인 계속 시도)",
                "WARN",
            )
            return False

        self.emit(
            f"[오피가이드] Turnstile sitekey: {sitekey[:20]}... 2Captcha 요청 중",
            "INFO",
        )

        try:
            from twocaptcha import TwoCaptcha

            solver = TwoCaptcha(self._captcha_api_key)
            result = solver.turnstile(
                sitekey=sitekey,
                url=self.driver.current_url,
            )
            token = result.get("code") if isinstance(result, dict) else result
        except Exception as exc:
            self.emit(f"[오피가이드] Turnstile 풀이 실패: {exc} (로그인 계속 시도)", "WARN")
            return False

        if not token:
            self.emit("[오피가이드] Turnstile 토큰 수신 실패 (로그인 계속 시도)", "WARN")
            return False

        self.emit("[오피가이드] Turnstile 토큰 수신 완료, 주입 중...", "INFO")

        # 토큰 주입 (여러 방법 시도)
        self.driver.execute_script(
            """
            var token = arguments[0];
            // 1) cfCallback 호출
            if (typeof window.cfCallback === 'function') {
                window.cfCallback(token);
            }
            // 2) turnstile 응답 필드에 직접 주입
            var fields = document.querySelectorAll(
                'input[name="cf-turnstile-response"],'
                + 'input[name="g-recaptcha-response"]'
            );
            fields.forEach(function(f) { f.value = token; });
            // 3) data-sitekey 컨테이너 내 hidden input
            var containers = document.querySelectorAll('[data-sitekey]');
            containers.forEach(function(c) {
                var inp = c.querySelector('input[type="hidden"]');
                if (inp) inp.value = token;
            });
            // 4) turnstile.getResponse 오버라이드
            if (window.turnstile) {
                window.turnstile.getResponse = function() { return token; };
            }
            """,
            token,
        )
        time.sleep(1.0)
        self.emit("[오피가이드] Turnstile 토큰 주입 완료", "INFO")
        return True

    def _fill_login_form(self) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id, input[name='mb_id']"))
        )

        # Cloudflare Turnstile 감지 시 자동 해결 (폼 입력 전에 먼저 처리)
        self._solve_turnstile()

        # 폼 입력 (Turnstile 해결 후 재입력 - Turnstile 자동검증 중 폼이 리셋될 수 있음)
        self.driver.execute_script("""
            var id = document.querySelector('#login_id') || document.querySelector('input[name="mb_id"]');
            var pw = document.querySelector('#login_pw') || document.querySelector('input[name="mb_password"]');
            if (id) { id.focus(); id.value = arguments[0]; id.dispatchEvent(new Event('input', {bubbles:true})); }
            if (pw) { pw.focus(); pw.value = arguments[1]; pw.dispatchEvent(new Event('input', {bubbles:true})); }
        """, self.username, self.password)
        time.sleep(0.5)
        self._dismiss_popups()

        # 제출: 여러 셀렉터 시도
        try:
            self.driver.execute_script("""
                var f = document.querySelector('form[name="flogin"]') || document.querySelector('form');
                if (!f) return;
                var btn = f.querySelector('button.btn-login, button[type="submit"], input[type="submit"]');
                if (btn) btn.click();
                else f.submit();
            """)
        except Exception:
            self.driver.find_element(
                By.CSS_SELECTOR, "button.btn-login, button[type='submit']"
            ).click()

    def _post_login_alerts(self) -> None:
        """로그인 후 alert/팝업 처리."""
        self.handle_alert(accept=True, timeout=2.0)
        self._dismiss_popups()

    def get_remaining_jumps(self) -> int:
        """Check remaining jump count from sidebar. Returns -1 if unknown."""
        try:
            self.driver.execute_script("""
                if (typeof sidebar_open === 'function') {
                    try { sidebar_open('sidebar-user'); } catch(e) {}
                }
                var links = document.querySelectorAll('a[href*="sidebar"], a[onclick*="sidebar"]');
                for (var i = 0; i < links.length; i++) {
                    try { links[i].click(); } catch(e) {}
                }
            """)
            time.sleep(1.0)

            remaining = self.driver.execute_script("""
                var all = document.body ? document.body.innerText : '';
                var m = all.match(/남은\\s*점프\\s*[:：]\\s*(\\d+)/);
                if (m) return parseInt(m[1], 10);
                var m2 = all.match(/남은\\s*점프\\s*[:：]?\\s*(\\d+)/);
                if (m2) return parseInt(m2[1], 10);
                var m3 = all.match(/잔여\\s*점프\\s*[:：]?\\s*(\\d+)/);
                if (m3) return parseInt(m3[1], 10);
                var sidebar = document.querySelector('#sidebar-user, .sidebar-user, [class*="sidebar"]');
                if (sidebar) {
                    var st = sidebar.innerText || '';
                    var m4 = st.match(/남은\\s*점프\\s*[:：]?\\s*(\\d+)/);
                    if (m4) return parseInt(m4[1], 10);
                }
                return -1;
            """)
            return remaining if isinstance(remaining, int) else -1
        except Exception:
            return -1

    def jump(self) -> JumpResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # Check remaining jumps
        remaining = self.get_remaining_jumps()
        self.emit(f"[오피가이드] 남은점프 확인 결과: {remaining}", "DEBUG")
        if isinstance(remaining, int) and remaining == 0:
            self.save_cookies(self.COOKIE_KEYS)
            return JumpResult(
                status=STATUS_INSUFFICIENT,
                message="남은 점프 횟수가 0입니다",
                remaining_count=0,
            )

        # Execute jump: fnJump() with 5-step fallback
        result_text = ""
        try:
            self.driver.execute_script("fnJump();")
            time.sleep(1.0)

            # 1) native confirm/alert
            try:
                WebDriverWait(self.driver, 2).until(EC.alert_is_present())
                a = self.driver.switch_to.alert
                a.accept()
                time.sleep(1.0)
            except Exception:
                pass

            # 2) SweetAlert confirm
            try:
                swal_btn = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(@class, 'swal2-confirm')]")
                    )
                )
                swal_btn.click()
                time.sleep(1.5)
            except Exception:
                pass

            # 3) result SweetAlert text
            result_text = self.handle_swal(click_confirm=False)

            # 4) native alert result
            if not result_text:
                try:
                    WebDriverWait(self.driver, 2).until(EC.alert_is_present())
                    a = self.driver.switch_to.alert
                    result_text = a.text
                    a.accept()
                except Exception:
                    pass

            # 5) window.__fnJumpResult fallback
            if not result_text:
                try:
                    result_text = self.driver.execute_script(
                        "return window.__fnJumpResult || '';"
                    ) or ""
                except Exception:
                    pass

        except Exception as exc:
            try:
                a = self.driver.switch_to.alert
                result_text = a.text
                a.accept()
            except Exception:
                pass
            if not result_text:
                return JumpResult(status="failed", message=f"fnJump 실행 실패: {exc}")

        # Close remaining SweetAlert
        self.handle_swal(click_confirm=True)
        self.save_cookies(self.COOKIE_KEYS)

        if result_text:
            self.emit(f"[오피가이드] 결과: {result_text}", "DEBUG")
            status, msg = self.classify_result(result_text)
            if status != STATUS_UNKNOWN:
                return JumpResult(status=status, message=msg)

        # Countdown check
        timer = self.wait_for_countdown(timeout=6.0)
        if timer:
            return JumpResult(status=STATUS_COOLDOWN, message=timer)

        return JumpResult(status=STATUS_SUCCESS, message="점프 실행")

    # ──────────────────────────────────────────────
    #  팝업 배너 닫기 헬퍼
    # ──────────────────────────────────────────────

    def _dismiss_popups(self) -> None:
        """팝업 배너 제거 (클릭 차단 방지)."""
        try:
            self.driver.execute_script("""
                document.querySelectorAll(
                    '.popup_banner, .popup_banner1, .pop-layer, .dim-layer'
                ).forEach(function(el) { el.style.display = 'none'; });
            """)
        except Exception:
            pass

    def _safe_navigate(self, url: str, wait: float = 2.0) -> None:
        """Navigate to URL with alert handling."""
        self.goto(url, via_script=True)
        time.sleep(wait)
        self.handle_alert(accept=True, timeout=1.0)
        self._dismiss_popups()
        self.require_human_check()

    def _has_register_form(self) -> bool:
        try:
            return bool(
                self.driver.execute_script(
                    """
                    return !!(
                        document.querySelector('#reg_mb_id')
                        || document.querySelector('input[name="mb_id"]')
                        || document.querySelector('form#fregisterform')
                        || document.querySelector('form[name="fregisterform"]')
                    );
                    """
                )
            )
        except Exception:
            return False

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """오피가이드 회원가입.

        kwargs:
            mb_id, mb_password, mb_nick
            (오피가이드 register_form.php - mb_name 필드 없음)
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
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_nick)",
                account=acct,
            )

        # 메인 페이지 로드 (CF 쿠키 설정 + 도메인 전환 감지) 후 회원가입 폼 진입
        self._warmup_site()
        form_ready = False
        for entry_path in (
            "/bbs/register_form.php",
            "/bbs/register_form_1.php",
            "/bbs/register.php?w=r",
            "/bbs/register.php",
        ):
            self._safe_navigate(self.base_url + entry_path, wait=3.0)
            self._dismiss_popups()

            # 도메인 공지문 감지 시 최신 주소로 즉시 재시도
            if self._maybe_switch_announced_domain():
                self._safe_navigate(self.base_url + entry_path, wait=3.0)
                self._dismiss_popups()

            if self._has_register_form():
                form_ready = True
                break

            # 일부 프록시/도메인 상태에서는 루트(/)로 튕긴 뒤 멈추는 경우가 있어 강제 재진입
            try:
                cur = (self.driver.current_url or "").rstrip("/")
            except Exception:
                cur = ""
            if cur == self.base_url.rstrip("/"):
                try:
                    self.driver.get(self.base_url + entry_path)
                    time.sleep(3.5)
                    self.handle_alert(accept=True, timeout=1.0)
                    self._dismiss_popups()
                except Exception:
                    pass
                if self._has_register_form():
                    form_ready = True
                    break

            try:
                # 약관 단계에서만 동의 버튼 처리 (작성 폼에서는 불필요 클릭 금지)
                self.driver.execute_script(
                    """
                    var path = (location.pathname || '').toLowerCase();
                    var hasRegField = !!document.querySelector('#reg_mb_id, input[name="mb_id"]');
                    if (hasRegField) return;
                    if (path.indexOf('/register_form') !== -1) return;
                    if (path.indexOf('/register.php') === -1) return;
                    if (!document.querySelector('#agree11, #agree12, input[name="agree"]')) return;
                    var checks = document.querySelectorAll('input[type="checkbox"]');
                    checks.forEach(function(c){
                        c.checked = true;
                        c.dispatchEvent(new Event('change', {bubbles:true}));
                    });
                    var btn = document.querySelector('#btn_agree')
                           || document.querySelector('form[action*="register_form"] button[type="submit"]')
                           || document.querySelector('form[action*="register_form"] input[type="submit"]');
                    if (btn) {
                        btn.click();
                    }
                    """
                )
            except Exception:
                pass

            wait_ok = False
            try:
                WebDriverWait(self.driver, 35).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                    )
                )
                wait_ok = True
            except Exception:
                wait_ok = False

            if not wait_ok:
                try:
                    src = self.driver.page_source or ""
                    if 'name="mb_id"' in src and "fregisterform" in src:
                        wait_ok = True
                except Exception:
                    pass

            if not wait_ok and self._has_register_form():
                wait_ok = True

            if wait_ok:
                form_ready = True
                break

        if not form_ready and self._has_register_form():
            form_ready = True

        if not form_ready:
            cur = ""
            info = ""
            try:
                cur = self.driver.current_url or ""
            except Exception:
                pass
            try:
                info = self.driver.execute_script(
                    "return document.body ? document.body.innerText : '';"
                ) or ""
            except Exception:
                info = ""
            if "회원가입이 불가" in info or "메인주소를 이용" in info:
                msg = f"회원가입 차단 공지 감지 (URL={cur})"
            else:
                msg = f"회원가입 폼 로드 실패 (URL={cur})"
            return LoginResult(
                success=False, method="register", message=msg,
                account=acct,
            )

        # 캡차 + 제출 재시도 루프 (최대 3회 - 2captcha 오답 대응)
        result_alert = ""
        max_captcha_attempts = 3

        for captcha_attempt in range(max_captcha_attempts):
            if captcha_attempt > 0:
                self.emit(
                    f"[오피가이드] 캡차 재시도 ({captcha_attempt + 1}/{max_captcha_attempts})...",
                    "INFO",
                )
                # 새 폼으로 재이동 (서버 상태 초기화)
                self._safe_navigate(self.base_url + "/bbs/register_form.php", wait=3.0)
                try:
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "#reg_mb_id, input[name='mb_id']")
                        )
                    )
                except Exception:
                    continue

            # JS로 모든 필드 값 설정
            self.driver.execute_script(
                """
                var f = document.getElementById('fregisterform');
                var idEl = document.getElementById('reg_mb_id') || f.querySelector('input[name="mb_id"]');
                var pwEl = document.getElementById('reg_mb_password') || f.querySelector('input[name="mb_password"]');
                var pwReEl = document.getElementById('reg_mb_password_re') || f.querySelector('input[name="mb_password_re"]');
                var nickEl = document.getElementById('reg_mb_nick') || f.querySelector('input[name="mb_nick"]');
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
                captcha_el.clear()

                if self._captcha_api_key:
                    import requests

                    # JS canvas로 캡차 이미지를 base64 추출
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
                        # canvas 실패 시 screenshot 폴백
                        try:
                            captcha_img = self.driver.find_element(
                                By.CSS_SELECTOR, "#captcha_img"
                            )
                            img_b64 = captcha_img.screenshot_as_base64
                        except Exception:
                            pass

                    if img_b64 and len(img_b64) >= 100:
                        self.emit(
                            f"[오피가이드] 2Captcha 캡차 풀이 중... (시도 {captcha_attempt + 1}, 이미지 {len(img_b64)}B)",
                            "INFO",
                        )

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
                                if "CAPCHA_NOT_READY" not in res_data.get(
                                    "request", ""
                                ):
                                    self.emit(
                                        f"[오피가이드] 캡차 풀이 실패: {res_data}",
                                        "ERROR",
                                    )
                                    break

                            # 응답 검증: 4~6자리 숫자
                            if code and len(code) >= 4 and code.isdigit():
                                captcha_el.clear()
                                captcha_el.send_keys(code)
                                captcha_solved = True
                                self.emit(
                                    f"[오피가이드] 캡차 자동 해결: {code}",
                                    "INFO",
                                )
                            else:
                                self.emit(
                                    f"[오피가이드] 캡차 응답 부적절: '{code}'",
                                    "WARNING",
                                )
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
                        else:
                            self.emit(
                                f"[오피가이드] 캡차 전송 실패: {req_data}",
                                "ERROR",
                            )

                if not captcha_solved:
                    self.emit("[오피가이드] 캡차를 입력해주세요...", "INFO")
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

            self._dismiss_popups()

            # reg_mb_id_check / reg_mb_nick_check AJAX 호출 (서버 상태 설정)
            try:
                ajax_result = self.driver.execute_script("""
                    var results = [];
                    try {
                        var msg1 = reg_mb_id_check();
                        results.push('id_check=' + (msg1 || 'OK'));
                    } catch(e) {
                        results.push('id_check_err=' + e.message);
                    }
                    try {
                        var msg2 = reg_mb_nick_check();
                        results.push('nick_check=' + (msg2 || 'OK'));
                    } catch(e) {
                        results.push('nick_check_err=' + e.message);
                    }
                    var btn = document.getElementById('btn_submit');
                    if (btn) { btn.disabled = false; btn.removeAttribute('disabled'); }
                    return results.join(', ');
                """)
                self.emit(f"[오피가이드] AJAX 체크: {ajax_result}", "DEBUG")
            except Exception as exc:
                self.emit(f"[오피가이드] AJAX 체크 실패: {exc}", "ERROR")
                self.handle_alert(accept=True, timeout=1.0)

            # AJAX 체크 실패 시에도 버튼 강제 활성화
            try:
                self.driver.execute_script("""
                    var btn = document.getElementById('btn_submit');
                    if (btn) { btn.disabled = false; btn.removeAttribute('disabled'); }
                """)
            except Exception:
                pass

            time.sleep(0.5)
            self._dismiss_popups()

            # 폼 제출: 버튼 클릭
            try:
                submit_btn = self.driver.find_element(
                    By.CSS_SELECTOR, "#btn_submit, button[type='submit'], input[type='submit']"
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView(true);", submit_btn
                )
                time.sleep(0.3)
                submit_btn.click()
            except Exception:
                try:
                    clicked = self.driver.execute_script(
                        """
                        var btn = document.getElementById('btn_submit')
                               || document.querySelector('button[type="submit"], input[type="submit"]');
                        if (btn) { btn.click(); return true; }
                        var f = document.getElementById('fregisterform') || document.querySelector('form');
                        if (f && typeof f.submit === 'function') { f.submit(); return true; }
                        return false;
                        """
                    )
                    if not clicked:
                        raise RuntimeError("submit target not found")
                except Exception:
                    return LoginResult(
                        success=False, method="register",
                        message="회원가입 제출 버튼 클릭 실패",
                        account=acct,
                    )

            # alert 폴링 (서버 응답 캐치)
            result_alert = ""
            for i in range(20):
                time.sleep(0.5)
                try:
                    alert = self.driver.switch_to.alert
                    alert_text = alert.text or ""
                    alert.accept()
                    self.emit(f"[오피가이드] Alert #{i}: {alert_text}", "DEBUG")
                    if "검색어" in alert_text:
                        continue
                    result_alert = alert_text
                    break
                except Exception:
                    pass
                try:
                    cur = self.driver.current_url
                    if "register_form" not in cur and "register" in cur:
                        result_alert = "__url_changed__"
                        break
                    if "register" not in cur:
                        result_alert = "__url_changed__"
                        break
                except Exception:
                    pass

            self.emit(
                f"[오피가이드] 제출 후 (시도 {captcha_attempt + 1}): "
                f"alert={result_alert}, url={self.driver.current_url}",
                "DEBUG",
            )

            # 캡차 오류면 재시도
            if "자동등록방지" in result_alert:
                if captcha_attempt < max_captcha_attempts - 1:
                    continue
            break

        # 결과 판단
        if result_alert == "__url_changed__":
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (페이지 이동 확인)",
                account=acct,
            )

        if result_alert:
            success_kw = ["완료", "축하", "가입"]
            fail_kw = ["올바른", "실패", "오류", "중복", "이미", "사용중", "입력", "자동등록방지", "맞지 않"]
            if any(kw in result_alert for kw in success_kw):
                return LoginResult(
                    success=True, method="register",
                    message=f"회원가입 성공: {result_alert}",
                    account=acct,
                )
            if any(kw in result_alert for kw in fail_kw):
                return LoginResult(
                    success=False, method="register",
                    message=f"회원가입 실패: {result_alert}",
                    account=acct,
                )

        # 최종 URL/소스 확인
        time.sleep(1.0)
        self.handle_alert(accept=True, timeout=1.0)
        current = self.driver.current_url or ""
        try:
            src = self.driver.page_source or ""
        except Exception:
            src = ""

        if "register_result" in current:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (register_result)",
                account=acct,
            )
        if "register" not in current:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (페이지 이동)",
                account=acct,
            )
        if "로그아웃" in src:
            return LoginResult(
                success=True, method="register",
                message="회원가입 완료 (로그인 상태)",
                account=acct,
            )

        return LoginResult(
            success=False, method="register", message="회원가입 결과 확인 불가",
            account=acct,
        )

    # ──────────────────────────────────────────────
    #  2. 프로필 조회
    # ──────────────────────────────────────────────

    def get_profile(self) -> Profile:
        """마이페이지에서 프로필 정보 조회."""
        from . import parsers

        self._safe_navigate(self.base_url + "/bbs/mypage.php", wait=2.0)
        return parsers.parse_profile(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """오피가이드 게시판 목록 가져오기 (네비게이션에서 추출)."""
        from . import parsers

        # 메인 페이지는 인트로 전용이므로, 게시판 페이지에서 네비게이션 수집
        self._safe_navigate(
            self.base_url + "/bbs/board.php?bo_table=basic", wait=2.0
        )
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

        Args:
            board_id: 게시판 ID (bo_table)
            page: 페이지 번호 (기본값 1)
            search_field: 검색 필드 (sfl) - e.g. 'wr_subject'
            search_text: 검색어 (stx)
            sort_field: 정렬 필드 (sst)
            sort_order: 정렬 순서 (sod)
        """
        from . import parsers

        # 검색 요청 시: 게시판 페이지에서 XHR로 검색 (Cloudflare 우회)
        if search_field and search_text:
            from urllib.parse import quote

            # 먼저 게시판 페이지로 이동 (Cloudflare 통과된 상태 확보)
            board_url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
            if sort_field:
                board_url += f"&sst={sort_field}"
            if sort_order:
                board_url += f"&sod={sort_order}"
            self._safe_navigate(board_url, wait=2.5)

            # 게시판 페이지에서 XHR로 검색 결과 가져와서 페이지 교체
            search_path = f"/bbs/board.php?bo_table={board_id}&page={page}&sfl={search_field}&stx={quote(search_text)}&sop=and"
            try:
                self.driver.execute_script("""
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', arguments[0], false);
                    xhr.withCredentials = true;
                    xhr.send();
                    if (xhr.status === 200 && xhr.responseText.length > 500) {
                        document.open();
                        document.write(xhr.responseText);
                        document.close();
                    }
                """, search_path)
                time.sleep(1.5)
            except Exception:
                pass
        else:
            url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
            if sort_field:
                url += f"&sst={sort_field}"
            if sort_order:
                url += f"&sod={sort_order}"
            self._safe_navigate(url, wait=2.5)
        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  5. 댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기.

        Args:
            post_id: 게시글 ID (wr_id). "bo_table:wr_id" 형식도 가능.
            board_id: 게시판 ID (bo_table).
        """
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)

        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self._safe_navigate(url, wait=2.5)
        return parsers.parse_comments(self.driver.page_source, post_id=post_id)

    # ──────────────────────────────────────────────
    #  6. 게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성.

        Args:
            board_id: 게시판 ID (bo_table)
            subject: 게시글 제목
            content: 게시글 내용
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        url = f"{self.base_url}/bbs/write.php?bo_table={board_id}"
        self._safe_navigate(url, wait=2.5)

        # 네비게이션 직후 알림 재확인 (레벨/권한 제한 알림 감지)
        post_nav_alert = self.handle_alert(accept=True, timeout=1.5)
        if post_nav_alert:
            _PERM_KW = ("이상만", "권한", "등급", "레벨", "까지만", "제한", "작성이 가능", "접근")
            if any(kw in post_nav_alert for kw in _PERM_KW):
                return WriteResult(success=False, message=f"게시판 접근 제한: {post_nav_alert}")

        if "login" in self.driver.current_url:
            return WriteResult(success=False, message="로그인이 필요합니다.")

        # write.php가 아닌 페이지로 리다이렉트된 경우 (알림 후 이동)
        if "write.php" not in (self.driver.current_url or ""):
            return WriteResult(success=False, message=f"게시판 접근 제한 (리다이렉트): {post_nav_alert or self.driver.current_url}")

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='wr_subject']")
                )
            )
        except Exception:
            return WriteResult(success=False, message=f"게시글 작성 폼 로드 실패{': ' + post_nav_alert if post_nav_alert else ''}")

        # 제목 입력
        subj_el = self.driver.find_element(
            By.CSS_SELECTOR, "input[name='wr_subject']"
        )
        subj_el.clear()
        subj_el.send_keys(subject)

        # 내용 입력: CKEditor JS API → iframe → textarea 폴백
        content_filled = False

        # 방법 1: CKEditor JS API
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
                    return true;
                }
                return false;
                """,
                content,
            )
            if result:
                content_filled = True
        except Exception:
            pass

        # 방법 2: CKEditor iframe
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

        # 방법 4: 숨겨진 textarea JS 설정
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
        self._dismiss_popups()

        # 작성완료 버튼 클릭 (write 폼 내 버튼만 정확히 타겟)
        submitted = self.driver.execute_script("""
            // fwrite 폼 찾기
            var f = document.getElementById('fwrite')
                 || document.querySelector('form[name="fwrite"]')
                 || document.querySelector('form[action*="write_update"]');
            if (!f) return 'no_form';

            // 폼 내 제출 버튼 찾기
            var btn = f.querySelector('#btn_submit')
                   || f.querySelector('button[type="submit"]')
                   || f.querySelector('input[type="submit"]')
                   || f.querySelector('.btn_submit');
            if (btn) {
                btn.scrollIntoView(true);
                btn.click();
                return 'clicked';
            }

            // 버튼 없으면 폼 직접 제출
            f.submit();
            return 'submitted';
        """)
        self.emit(f"[오피가이드] write_post 제출: {submitted}", "DEBUG")
        if submitted == "no_form":
            return WriteResult(success=False, message="게시글 작성 폼을 찾을 수 없습니다.")

        time.sleep(2.0)

        # 여러 알림 연속 처리
        last_alert = ""
        for _ in range(5):
            alert_text = self.detect_form_result(timeout=2.0)
            if not alert_text:
                break
            last_alert = alert_text
            if "오류" in alert_text or "실패" in alert_text:
                return WriteResult(success=False, message=f"작성 실패: {alert_text}")
            time.sleep(0.5)

        self.require_human_check()

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and ("오류" in swal_text or "실패" in swal_text):
            return WriteResult(success=False, message=f"작성 실패: {swal_text}")

        time.sleep(1.0)
        self.handle_alert(accept=True, timeout=1.0)

        current = self.driver.current_url or ""
        wr_id_m = re.search(r"wr_id=(\d+)", current)
        wr_id = wr_id_m.group(1) if wr_id_m else ""

        if "write.php" not in current:
            return WriteResult(
                success=True, id=wr_id, message="게시글 작성 완료"
            )

        # write.php에 머물러 있어도 성공 알림이 있었으면 성공
        if last_alert and "오류" not in last_alert and "실패" not in last_alert:
            return WriteResult(
                success=True, id=wr_id, message=f"게시글 작성 완료: {last_alert}"
            )

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ──────────────────────────────────────────────
    #  7. 댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(
        self, post_id: str, content: str, *, board_id: str = ""
    ) -> WriteResult:
        """특정 게시글에 댓글 작성.

        Args:
            post_id: 게시글 ID (wr_id). "bo_table:wr_id" 형식도 가능.
            content: 댓글 내용.
            board_id: 게시판 ID (bo_table).
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
        # _safe_navigate 대신 직접 탐색 — alert 텍스트를 직접 캡처
        self.goto(url, via_script=True)
        time.sleep(2.5)
        comment_nav_alert = self.handle_alert(accept=True, timeout=1.5) or ""
        self._dismiss_popups()
        self.require_human_check()
        if comment_nav_alert:
            self.emit(f"[오피가이드] 게시글 접근 alert: {comment_nav_alert}", "DEBUG")
        if comment_nav_alert:
            _PERM_KW = ("이상만", "권한", "등급", "레벨", "까지만", "제한", "작성이 가능", "접근")
            if any(kw in comment_nav_alert for kw in _PERM_KW):
                return WriteResult(success=False, message=f"댓글 접근 제한: {comment_nav_alert}")
            _STALE_KW = ("존재하지 않", "삭제", "이동된", "없는 글")
            if any(kw in comment_nav_alert for kw in _STALE_KW):
                return WriteResult(success=False, message=f"존재하지 않는 글: {comment_nav_alert[:80]}")

        # 댓글 폼: #fviewcomment > textarea#wr_content
        # 제출: apms_comment_submit() JS 함수 또는 #btn_submit2 클릭
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#wr_content, textarea[name='wr_content']")
                )
            )
        except Exception:
            return WriteResult(
                success=False,
                message=f"댓글 입력 폼을 찾을 수 없습니다.{': ' + comment_nav_alert if comment_nav_alert else ' (로그인 필요 가능)'}",
            )

        ta = self.driver.find_element(
            By.CSS_SELECTOR, "#wr_content, textarea[name='wr_content']"
        )
        ta.clear()
        ta.send_keys(content)
        time.sleep(0.5)

        # 제출: apms_comment_submit() → #btn_submit2 클릭 → form submit 폴백
        try:
            self.driver.execute_script("apms_comment_submit();")
        except Exception:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, "#btn_submit2")
                btn.click()
            except Exception:
                try:
                    self.driver.execute_script(
                        "document.querySelector('#fviewcomment').submit();"
                    )
                except Exception:
                    return WriteResult(success=False, message="댓글 제출 실패")

        time.sleep(2.0)
        self.require_human_check()

        # 여러 alert 연속 처리
        _fail_kw = ("오류", "실패", "30일", "일이 지난", "작성할 수 없", "권한")
        last_alert = ""
        for _ in range(5):
            alert_text = self.detect_form_result(timeout=2.0)
            if not alert_text:
                break
            last_alert = alert_text
            if any(kw in alert_text for kw in _fail_kw):
                return WriteResult(
                    success=False, message=f"댓글 작성 실패: {alert_text}"
                )
            time.sleep(0.3)

        swal_text = self.handle_swal(click_confirm=True)
        if swal_text and any(kw in swal_text for kw in _fail_kw):
            return WriteResult(
                success=False, message=f"댓글 작성 실패: {swal_text}"
            )

        return WriteResult(success=True, message="댓글 작성 완료")

    # ──────────────────────────────────────────────
    #  출석체크
    # ──────────────────────────────────────────────

    def checkin(self) -> dict[str, Any]:
        """출석체크 (/bbs/board.php?bo_table=chulsuk)."""
        from selenium.webdriver.common.by import By

        try:
            self.driver.execute_script(
                "window.location.href = arguments[0];",
                self.base_url + "/bbs/board.php?bo_table=chulsuk",
            )
            time.sleep(2)
            self.require_human_check()

            # 출석 버튼 클릭 시도
            for sel in [
                "input[type='submit'][value*='출석']",
                "button[type='submit']",
                "#attendance_submit",
                "a[href*='attendance']",
                "a[href*='chulsuk']",
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
            swal_text = self.handle_swal(click_confirm=True) or ""
            msg = alert_text or swal_text or "출석 페이지 방문"
            return {"success": True, "message": msg}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    # ──────────────────────────────────────────────
    #  등업 버튼 클릭
    # ──────────────────────────────────────────────

    def _parse_guide_deficit(self) -> dict[str, Any] | None:
        """nextlevel 행에서 현재/필요 수치 파싱 → 부족분 계산.

        opguide guide 페이지 구조 (테이블):
          <thead>/<tr>: ... | 가입일 | 포인트 | 후기 | 게시글 | 댓글 | 레벨업
          <tr class="nextlevel">: ... | 0/0 일 | 35/500P | 0/1개 | 0/0개 | 0/0개 | [btn]

        파싱 전략:
          1차: 헤더 행의 th/td 텍스트로 컬럼 인덱스를 동적 매핑
          2차: 고정 인덱스 폴백 (3=가입일, 4=포인트, 5=후기, 6=게시글, 7=댓글)
          3차: 셀 텍스트의 단위 키워드로 필드 추론
        """
        import re as _re
        from selenium.webdriver.common.by import By

        # ── 유틸 ──
        def _parse_pair(text: str) -> tuple[int, int]:
            """'현재/필요' 형식 파싱. 예: '35/500P' → (35, 500)"""
            text = text.replace(",", "").replace(".", "").strip()
            m = _re.search(r"(-?\d+)\s*/\s*(-?\d+)", text)
            if m:
                cur, req = int(m.group(1)), int(m.group(2))
                # 음수/비정상 값 보정
                return max(0, cur), max(0, req)
            # 숫자가 하나만 있는 경우 (예: "5개")
            single = _re.search(r"(\d+)", text)
            if single:
                return int(single.group(1)), 0
            return 0, 0

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
            row = self.driver.find_element(By.CSS_SELECTOR, "tr.nextlevel")
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 4:
                return None

            # ── 1차: 헤더 기반 컬럼 매핑 ──
            col_map: dict[int, str] = {}  # {td_index: field_name}
            header_col_count = 0
            try:
                table = row.find_element(By.XPATH, "./ancestor::table[1]")
                header_rows = table.find_elements(By.CSS_SELECTOR, "thead tr, tr:first-child")
                for hr in header_rows:
                    ths = hr.find_elements(By.TAG_NAME, "th") or hr.find_elements(By.TAG_NAME, "td")
                    if len(ths) < 4:
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

            # rowspan/colspan 보정: 헤더 컬럼 수 > td 수이면 앞쪽 컬럼 누락 (아이콘 등)
            if col_map and header_col_count > len(tds):
                offset = header_col_count - len(tds)
                col_map = {idx - offset: field for idx, field in col_map.items() if idx >= offset}

            # ── 2차: 고정 인덱스 폴백 ──
            if not col_map and len(tds) >= 8:
                col_map = {3: "days", 4: "points", 5: "reviews", 6: "posts", 7: "comments"}

            # ── 파싱 실행 ──
            current: dict[str, int] = {}
            required: dict[str, int] = {}

            if col_map:
                # 매핑된 컬럼으로 파싱
                for idx, field in col_map.items():
                    if idx < len(tds):
                        cur, req = _parse_pair(tds[idx].text)
                        current[field] = cur
                        required[field] = req
            else:
                # ── 3차: 단위 키워드 기반 추론 ──
                for td in tds:
                    text = td.text.strip()
                    if not _re.search(r"\d+\s*/\s*\d+", text):
                        continue
                    field = _detect_field_from_text(text)
                    if not field:
                        continue
                    cur, req = _parse_pair(text)
                    current[field] = cur
                    required[field] = req

            if not current and not required:
                return None

            # 기본 필드 보장 (누락 시 0/0)
            for f in ("days", "points", "reviews", "posts", "comments"):
                current.setdefault(f, 0)
                required.setdefault(f, 0)

            deficit = {f: max(0, required[f] - current[f]) for f in current}

            return {"current": current, "required": required, "deficit": deficit}
        except Exception:
            return None

    def click_levelup(self) -> dict[str, Any]:
        """등업 버튼 클릭 (/bbs/page.php?hid=guide 내 레벨업 컬럼)."""
        from selenium.webdriver.common.by import By

        try:
            self.driver.execute_script(
                "window.location.href = arguments[0];",
                self.base_url + "/bbs/page.php?hid=guide",
            )
            time.sleep(2)
            self.require_human_check()

            # guide 페이지의 등급 테이블에서 "nextlevel" 행의 등업 버튼 찾기
            # opguide: tr.nextlevel 행, #btn_upgrade 버튼
            btn = None

            # 1) #btn_upgrade 버튼 (조건 충족 시 클릭 가능)
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, "#btn_upgrade")
                if el.is_displayed():
                    txt = el.text.strip()
                    if "등업조건미달" in txt:
                        deficit = self._parse_guide_deficit()
                        result = {"success": False, "message": "등업조건미달 — 조건 충족 필요"}
                        if deficit:
                            result["deficit"] = deficit
                        return result
                    btn = el
            except Exception:
                pass

            # 2) nextlevel 행 안의 버튼/링크 폴백
            if not btn:
                for sel in [
                    "tr.nextlevel a",
                    "tr.nextlevel button",
                    "tr.nextlevel input[type='submit']",
                ]:
                    try:
                        el = self.driver.find_element(By.CSS_SELECTOR, sel)
                        if el.is_displayed():
                            txt = el.text.strip()
                            if "등업조건미달" in txt:
                                deficit = self._parse_guide_deficit()
                                result = {"success": False, "message": "등업조건미달 — 조건 충족 필요"}
                                if deficit:
                                    result["deficit"] = deficit
                                return result
                            btn = el
                            break
                    except Exception:
                        continue

            # 3) XPath 폴백
            if not btn:
                for xp in [
                    "//tr[contains(@class,'nextlevel')]//a[contains(text(),'등업')]",
                    "//tr[contains(@class,'nextlevel')]//button[contains(text(),'등업')]",
                    "//a[contains(text(),'등업신청')]",
                    "//button[contains(text(),'등업신청')]",
                ]:
                    try:
                        el = self.driver.find_element(By.XPATH, xp)
                        if el.is_displayed():
                            txt = el.text.strip()
                            if "등업조건미달" in txt:
                                deficit = self._parse_guide_deficit()
                                result = {"success": False, "message": "등업조건미달 — 조건 충족 필요"}
                                if deficit:
                                    result["deficit"] = deficit
                                return result
                            btn = el
                            break
                    except Exception:
                        continue

            if btn:
                btn.click()
                time.sleep(2)
                alert_text = self.handle_alert(accept=True, timeout=3.0) or ""
                swal_text = self.handle_swal(click_confirm=True) or ""
                msg = alert_text or swal_text or "등업 버튼 클릭 완료"
                return {"success": True, "message": msg}
            else:
                return {"success": False, "message": "등업 버튼을 찾을 수 없음 (guide 페이지)"}
        except Exception as exc:
            try:
                self.driver.switch_to.alert.accept()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}
