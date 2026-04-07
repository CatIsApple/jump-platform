"""BaseSite ABC - common utilities and abstract interface."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from .exceptions import CaptchaError, NavigationError
from .types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    Profile,
    WriteResult,
)

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"
STATUS_COOLDOWN = "cooldown"
STATUS_INSUFFICIENT = "insufficient"
STATUS_LOGIN_REQUIRED = "login_required"

_COUNTDOWN_FULL_RE = re.compile(r"^\d{1,2}:\d{2}$")
_COUNTDOWN_FIND_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")


class BaseSite(ABC):
    """Abstract base for all site modules.

    Parameters
    ----------
    driver : Any
        Selenium WebDriver instance.
    domain : str
        Site domain (e.g. ``"hlbam27.com"``).
    username : str
        Login ID.
    password : str
        Login password.
    emit : callable, optional
        ``emit(message, level)`` logging callback.
    captcha_api_key : str, optional
        2Captcha API key for automatic captcha solving.
    """

    # Subclass may override
    SITE_NAME: str = ""

    def __init__(
        self,
        driver: Any,
        domain: str,
        username: str,
        password: str,
        *,
        emit: Callable[[str, str], None] | None = None,
        captcha_api_key: str = "",
    ) -> None:
        self.driver = driver
        self.domain = domain.strip().rstrip("/")
        self.username = username.strip()
        self.password = password.strip()
        self._emit = emit or (lambda msg, level: None)
        self._captcha_api_key = captcha_api_key

    # ── URL helpers ──

    @property
    def base_url(self) -> str:
        d = self.domain
        if not d.startswith("http"):
            return f"https://{d}"
        return d

    def url(self, path: str) -> str:
        """Combine base_url with a relative path."""
        if path.startswith("http"):
            return path
        sep = "" if path.startswith("/") else "/"
        return f"{self.base_url}{sep}{path}"

    # ── Navigation ──

    def goto(
        self,
        url: str,
        *,
        via_script: bool = False,
        check_popup: bool = False,
        check_cf: bool = True,
    ) -> None:
        """Navigate to *url*. Optionally use ``location.href`` via JS.

        If *check_cf* is True (default), automatically detect and solve
        Cloudflare challenge pages after navigation.
        """
        target = url if url.startswith("http") else self.url(url)
        if not via_script:
            self.driver.get(target)
        else:
            try:
                self.driver.execute_script(
                    "window.location.href = arguments[0];", target
                )
            except Exception:
                self.driver.get(target)

        if check_cf:
            time.sleep(1)
            self._auto_solve_cf(target, via_script=via_script)

        if check_popup:
            time.sleep(0.5)
            self.check_and_dismiss_popups()

    def naver_warmup(self, sleep_s: float = 0.5) -> None:
        """Visit naver.com briefly before navigating to the target site."""
        try:
            self.driver.get("https://naver.com")
            time.sleep(float(sleep_s))
        except Exception:
            return

    # ── Captcha / robot check ──

    def _is_robot_page(self) -> bool:
        """현재 페이지가 Cloudflare 챌린지 페이지인지 확인."""
        try:
            from ..app.captcha_solver import is_robot_page  # type: ignore[import-untyped]
            return is_robot_page(self.driver)
        except ImportError:
            return False

    def _auto_solve_cf(
        self, target_url: str = "", *, via_script: bool = False, max_retries: int = 2
    ) -> bool:
        """Cloudflare 챌린지가 감지되면 자동으로 해결하고 원래 URL로 재이동.

        Returns True if solved or no challenge, False if failed.
        """
        if not self._is_robot_page():
            return True

        self.emit("Cloudflare 챌린지 감지. 자동 해결 시도...", "WARNING")

        for attempt in range(max_retries):
            try:
                self.require_human_check()
                # 해결 후 원래 URL로 재이동
                if target_url and not self._is_robot_page():
                    cur = ""
                    try:
                        cur = self.driver.current_url
                    except Exception:
                        pass
                    # 챌린지 해결 후 리다이렉트 안 됐으면 재이동
                    if cur and target_url not in cur:
                        time.sleep(1)
                        if not via_script:
                            self.driver.get(target_url)
                        else:
                            self.driver.execute_script(
                                "window.location.href = arguments[0];", target_url
                            )
                        time.sleep(2)
                        # 재이동 후 또 챌린지 뜨면 다시 시도
                        if self._is_robot_page():
                            continue
                return True
            except CaptchaError:
                if attempt < max_retries - 1:
                    self.emit(
                        f"CF 해결 실패 (시도 {attempt + 1}/{max_retries}). 재시도...",
                        "WARNING",
                    )
                    time.sleep(2)
                    continue
                self.emit("Cloudflare 챌린지 해결 최종 실패.", "ERROR")
                return False
        return False

    def require_human_check(self) -> None:
        """Detect robot/captcha pages and attempt 2Captcha auto-solve.

        Raises ``CaptchaError`` if auto-solve fails.
        """
        try:
            from ..app.captcha_solver import is_robot_page, robot_pass  # type: ignore[import-untyped]
        except ImportError:
            # Standalone usage without the app package
            return

        if not is_robot_page(self.driver):
            return

        if self._captcha_api_key:
            solved = robot_pass(self.driver, self._captcha_api_key, self._emit)
            if solved:
                return

        raise CaptchaError(
            "로봇/캡차 페이지가 감지되었습니다. 자동 해결에 실패했습니다."
        )

    # ── Login text detection ──

    def wait_for_text(self, text: str, timeout: float = 3.0) -> bool:
        """Wait until *text* appears in the page DOM."""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(
                    (By.XPATH, f"//*[normalize-space(text())='{text}']")
                )
            )
            return True
        except Exception:
            return False

    def page_contains(self, text: str) -> bool:
        """Check if page source contains *text*."""
        try:
            src = self.driver.page_source or ""
        except Exception:
            src = ""
        return text in src

    # ── Result classification ──

    @staticmethod
    def classify_result(text: str) -> tuple[str, str]:
        """Classify jump/action result text into (status, message)."""
        t = (text or "").strip()
        if not t:
            return STATUS_UNKNOWN, "결과 확인 불가"

        if _COUNTDOWN_FULL_RE.match(t):
            return STATUS_COOLDOWN, t

        # cooldown — 시간/대기/재시도 키워드 종합 감지
        _cd_keywords = (
            "대기", "쿨타임", "쿨다운",
            "분에 한번", "번만 가능",
            "분 후", "분후", "초 후", "초후", "시간 후", "시간후",
            "잠시 후", "잠시후",
            "기다려", "기다리",
            "5분", "10분", "15분", "20분", "30분",
        )
        if any(kw in t for kw in _cd_keywords):
            return STATUS_COOLDOWN, t
        # "최근 점프/내역" or "이미 점프/실행" — 이전에 실행한 적 있음
        if ("최근" in t or "이미" in t) and ("점프" in t or "내역" in t or "실행" in t):
            return STATUS_COOLDOWN, t
        # "다시 시도/점프" + 실패 아닌 맥락 — 재시도 유도
        if ("다시 시도" in t or "다시 점프" in t) and "실패" not in t:
            return STATUS_COOLDOWN, t

        # insufficient
        if ("횟수" in t or "회수" in t) and (
            "없" in t or "부족" in t or "초과" in t or "소진" in t
        ):
            return STATUS_INSUFFICIENT, t
        if "남은" in t and ("0회" in t or "0개" in t or "0번" in t):
            return STATUS_INSUFFICIENT, t
        if "내일" in t and ("다시" in t or "시도" in t):
            return STATUS_INSUFFICIENT, t
        if "소진" in t or "모두 사용" in t:
            return STATUS_INSUFFICIENT, t

        # stopped
        if "중지" in t or "정지" in t or "일시중지" in t or "중단" in t:
            return STATUS_FAILED, t

        # login required
        if "회원만" in t or "로그인" in t:
            return STATUS_LOGIN_REQUIRED, t

        # success
        if "완료" in t or "성공" in t or "올렸습니다" in t or "적용" in t:
            return STATUS_SUCCESS, t

        # failure
        if "실패" in t or "불가" in t or "오류" in t:
            return STATUS_FAILED, t

        return STATUS_UNKNOWN, t

    # ── Countdown detection ──

    def _find_countdown_in_clickables(self) -> str | None:
        """Scan button/link text for countdown timers (e.g. ``09:59``)."""
        from selenium.webdriver.common.by import By

        xpaths = [
            "//*[self::button or self::a]["
            "(contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'jump') "
            "or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'jump'))"
            "]",
            "//*[self::button or self::a]",
        ]
        for xp in xpaths:
            try:
                els = self.driver.find_elements(By.XPATH, xp)
            except Exception:
                continue
            for el in els:
                try:
                    if hasattr(el, "is_displayed") and not el.is_displayed():
                        continue
                    txt = (el.text or "").strip()
                except Exception:
                    continue
                if len(txt) <= 5 and _COUNTDOWN_FULL_RE.match(txt):
                    return txt
                m = _COUNTDOWN_FIND_RE.search(txt)
                if m:
                    return m.group(1)
        return None

    def wait_for_countdown(self, timeout: float = 4.0) -> str | None:
        """Poll for a countdown timer appearing on the page."""
        end = time.time() + float(timeout)
        while time.time() < end:
            v = self._find_countdown_in_clickables()
            if v:
                return v
            time.sleep(0.2)
        return None

    # ── Alert handling ──

    def handle_alert(self, *, accept: bool = True, timeout: float = 1.0) -> str:
        """Wait for a native alert, read its text, and accept/dismiss it."""
        try:
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(self.driver, timeout).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            text = alert.text or ""
            self.emit(f"[ALERT] {text}", "DEBUG")
            if accept:
                alert.accept()
            else:
                alert.dismiss()
            return text
        except Exception:
            return ""

    def handle_swal(self, *, click_confirm: bool = True) -> str:
        """Read SweetAlert2 text and optionally click the confirm button."""
        from selenium.webdriver.common.by import By

        text = ""
        for sel in (".swal2-html-container", ".swal2-title"):
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                text = (el.text or "").strip()
                if text:
                    break
            except Exception:
                continue

        if text:
            self.emit(f"[SWAL] {text}", "DEBUG")

        if click_confirm:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, ".swal2-confirm")
                if btn.is_displayed():
                    btn.click()
            except Exception:
                pass

        return text

    def detect_form_result(self, *, timeout: float = 2.0) -> str:
        """Form 제출 후 alert/swal/modal 종합 감지 (drop-in replacement for handle_alert).

        Returns: 감지된 텍스트 문자열 (없으면 빈 문자열).
        native alert → SweetAlert → Bootstrap modal → custom popup 순서로 확인.
        """
        # 1) Native alert (with wait)
        text = self.handle_alert(accept=True, timeout=timeout)
        if text:
            return text
        # 2) SweetAlert2
        text = self.handle_swal(click_confirm=True)
        if text:
            return text
        # 3) 기타 modal/custom popup
        popup = self.detect_popup(dismiss=True, log=True)
        if popup["type"]:
            return popup["text"]
        return ""

    # ── 종합 팝업/모달 감지 ──

    def detect_popup(self, *, dismiss: bool = True, log: bool = True) -> dict[str, str]:
        """모든 종류의 팝업/모달/alert를 감지하고 로깅.

        Returns dict with keys: type, text, action.
        type: 'alert', 'swal', 'modal', 'custom', '' (없음)
        """
        from selenium.webdriver.common.by import By

        result: dict[str, str] = {"type": "", "text": "", "action": ""}

        # 1. Native JS alert
        try:
            alert = self.driver.switch_to.alert
            text = alert.text or ""
            result = {"type": "alert", "text": text, "action": "detected"}
            if log:
                self.emit(f"[POPUP:alert] {text}", "WARN")
            if dismiss:
                alert.accept()
                result["action"] = "accepted"
            return result
        except Exception:
            pass

        # 2. SweetAlert2 (다양한 셀렉터)
        swal_selectors = [
            ".swal2-popup",
            ".swal2-container",
            "[class*='swal']",
        ]
        for sel in swal_selectors:
            try:
                popup = self.driver.find_element(By.CSS_SELECTOR, sel)
                if not popup.is_displayed():
                    continue
                # 텍스트 추출 (title + content)
                parts = []
                for sub in [".swal2-title", ".swal2-html-container", ".swal2-content", "h2", "p"]:
                    try:
                        el = popup.find_element(By.CSS_SELECTOR, sub)
                        t = (el.text or "").strip()
                        if t and t not in parts:
                            parts.append(t)
                    except Exception:
                        continue
                if not parts:
                    parts = [(popup.text or "").strip()]
                text = " | ".join(p for p in parts if p)
                # 아이콘 타입 감지
                icon_type = ""
                for icon_cls in ["swal2-icon--error", "swal2-icon--warning", "swal2-icon--success", "swal2-icon--info"]:
                    try:
                        popup.find_element(By.CSS_SELECTOR, f".{icon_cls}")
                        icon_type = icon_cls.split("--")[-1]
                        break
                    except Exception:
                        continue
                type_str = f"swal:{icon_type}" if icon_type else "swal"
                result = {"type": type_str, "text": text, "action": "detected"}
                if log:
                    self.emit(f"[POPUP:{type_str}] {text}", "WARN")
                if dismiss:
                    for btn_sel in [".swal2-confirm", ".swal2-close", "button"]:
                        try:
                            btn = popup.find_element(By.CSS_SELECTOR, btn_sel)
                            if btn.is_displayed():
                                btn.click()
                                result["action"] = f"clicked:{btn_sel}"
                                break
                        except Exception:
                            continue
                return result
            except Exception:
                continue

        # 3. Bootstrap 모달
        try:
            modal = self.driver.find_element(By.CSS_SELECTOR, ".modal.show, .modal.in, .modal[style*='display: block']")
            if modal.is_displayed():
                body = ""
                for sub in [".modal-body", ".modal-content"]:
                    try:
                        el = modal.find_element(By.CSS_SELECTOR, sub)
                        body = (el.text or "").strip()
                        if body:
                            break
                    except Exception:
                        continue
                result = {"type": "modal", "text": body, "action": "detected"}
                if log:
                    self.emit(f"[POPUP:modal] {body}", "WARN")
                if dismiss:
                    for btn_sel in [".modal-footer .btn-primary", ".btn-close", "[data-dismiss='modal']", "[data-bs-dismiss='modal']", "button"]:
                        try:
                            btn = modal.find_element(By.CSS_SELECTOR, btn_sel)
                            if btn.is_displayed():
                                btn.click()
                                result["action"] = f"clicked:{btn_sel}"
                                break
                        except Exception:
                            continue
                return result
        except Exception:
            pass

        # 4. 커스텀 팝업 (overlay/dialog 등)
        custom_selectors = [
            "[class*='popup'][style*='display']",
            "[class*='dialog'][style*='display']",
            "[class*='overlay'][style*='display']",
            ".layer_popup",
            "#popup",
        ]
        for sel in custom_selectors:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if not el.is_displayed():
                        continue
                    text = (el.text or "").strip()
                    if len(text) < 3:
                        continue
                    result = {"type": "custom", "text": text[:200], "action": "detected"}
                    if log:
                        self.emit(f"[POPUP:custom] {text[:200]}", "WARN")
                    if dismiss:
                        for btn_sel in ["button", "a", "[class*='close']", "[class*='btn']"]:
                            try:
                                btn = el.find_element(By.CSS_SELECTOR, btn_sel)
                                if btn.is_displayed():
                                    btn.click()
                                    result["action"] = f"clicked:{btn_sel}"
                                    break
                            except Exception:
                                continue
                    return result
            except Exception:
                continue

        return result

    def check_and_dismiss_popups(self) -> list[dict[str, str]]:
        """반복적으로 팝업을 감지하고 닫기. 모든 팝업 처리 후 리스트 반환."""
        popups: list[dict[str, str]] = []
        for _ in range(5):
            p = self.detect_popup(dismiss=True, log=True)
            if not p["type"]:
                break
            popups.append(p)
            time.sleep(0.3)
        return popups

    # ── Cookie persistence ──

    def save_cookies(self, cookie_keys: list[str] | None = None) -> bool:
        """Save driver cookies to disk via file_manager."""
        try:
            from jump_platform.jump_worker_dashboard.app.file_manager import (
                save_cookies as _save,
            )
            return _save(self.driver, self.domain, self.username, cookie_keys)
        except ImportError:
            return False

    def load_cookies(self, cookie_keys: list[str] | None = None) -> bool:
        """Load cookies from disk and inject into driver."""
        try:
            from jump_platform.jump_worker_dashboard.app.file_manager import (
                load_cookies as _load,
            )
            return _load(self.driver, self.domain, self.username, cookie_keys)
        except ImportError:
            return False

    # ── Logging ──

    def emit(self, message: str, level: str = "INFO") -> None:
        self._emit(message, level)

    # ══════════════════════════════════════════════════════════
    #  공개 API — 모든 사이트가 동일한 메서드 세트를 제공
    # ══════════════════════════════════════════════════════════

    @abstractmethod
    def login(self) -> LoginResult:
        """Authenticate with the site."""

    @abstractmethod
    def jump(self) -> JumpResult:
        """Execute jump action."""

    def get_remaining_jumps(self) -> int:
        """Return remaining jump count. -1 if unknown/unsupported."""
        return -1

    # ── Register helpers (shared by all sites) ──

    def _solve_kcaptcha(self) -> bool:
        """Solve gnuboard kcaptcha if present on page.

        Returns True if captcha was solved or not present.
        Returns False if captcha is present but could not be solved.
        """
        from selenium.webdriver.common.by import By

        try:
            captcha_el = self.driver.find_element(By.CSS_SELECTOR, "#captcha_key")
        except Exception:
            return True  # No captcha field — OK to proceed

        captcha_el.clear()

        if self._captcha_api_key:
            import requests

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
                    captcha_img = self.driver.find_element(
                        By.CSS_SELECTOR, "#captcha_img"
                    )
                    img_b64 = captcha_img.screenshot_as_base64
                except Exception:
                    pass

            if img_b64 and len(img_b64) >= 100:
                self.emit(f"[{self.SITE_NAME}] 2Captcha 캡차 풀이 중...", "INFO")
                try:
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
                            self.emit(
                                f"[{self.SITE_NAME}] 캡차 자동 해결: {code}", "INFO"
                            )
                            return True
                        else:
                            self.emit(
                                f"[{self.SITE_NAME}] 캡차 응답 부적절: '{code}'",
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
                            return False
                    else:
                        self.emit(
                            f"[{self.SITE_NAME}] 캡차 전송 실패: {req_data}", "ERROR"
                        )
                except Exception as exc:
                    self.emit(
                        f"[{self.SITE_NAME}] 캡차 자동 해결 실패: {exc}", "ERROR"
                    )

        # Manual input fallback
        self.emit(f"[{self.SITE_NAME}] 캡차를 입력해주세요...", "INFO")
        for _ in range(240):
            val = captcha_el.get_attribute("value") or ""
            if len(val) >= 4:
                return True
            time.sleep(0.5)
        return False

    def _gnuboard_ajax_check(self) -> None:
        """Call gnuboard AJAX id/nick duplicate check and enable submit button."""
        try:
            self.driver.execute_script("""
                try { reg_mb_id_check(); } catch(e) {}
                try { reg_mb_nick_check(); } catch(e) {}
                var btn = document.getElementById('btn_submit');
                if (btn) { btn.disabled = false; btn.removeAttribute('disabled'); }
            """)
            time.sleep(0.5)
        except Exception:
            pass
        self.handle_alert(accept=True, timeout=0.5)

    def _fill_gnuboard_form_js(
        self, mb_id: str, mb_password: str, mb_name: str, mb_nick: str,
    ) -> None:
        """Fill gnuboard registration form fields via JavaScript."""
        self.driver.execute_script("""
            var f = document.getElementById('fregisterform')
                 || document.querySelector('form[name="fregisterform"]')
                 || document.forms[0];
            if (!f) return;
            var set = function(name, val) {
                var el = f.querySelector('[name="' + name + '"]')
                      || document.getElementById('reg_' + name);
                if (el) {
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            };
            set('mb_id', arguments[0]);
            set('mb_password', arguments[1]);
            set('mb_password_re', arguments[1]);
            set('mb_name', arguments[2]);
            set('mb_nick', arguments[3]);
        """, mb_id, mb_password, mb_name, mb_nick)

    def _gnuboard_submit_form(self) -> bool:
        """Submit gnuboard registration form. Returns True if submit was triggered."""
        try:
            submitted = self.driver.execute_script("""
                var f = document.getElementById('fregisterform')
                     || document.querySelector('form[name="fregisterform"]');
                if (!f) return false;
                var btn = f.querySelector('#btn_submit')
                       || f.querySelector('button[type="submit"]')
                       || f.querySelector('input[type="submit"]');
                if (btn) {
                    btn.disabled = false;
                    btn.removeAttribute('disabled');
                    btn.click();
                    return true;
                }
                f.submit();
                return true;
            """)
            return bool(submitted)
        except Exception:
            return False

    def _check_register_result(self, acct: dict) -> "LoginResult":
        """Check gnuboard registration result from alerts, swal, URL, page source."""
        _ok = ["완료", "가입", "환영", "축하"]
        _fail = ["실패", "오류", "이미", "사용중", "중복", "자동등록방지"]

        alert_text = self.handle_alert(accept=True, timeout=2.0) or ""
        if alert_text:
            if any(k in alert_text for k in _ok) and not any(k in alert_text for k in _fail):
                return LoginResult(success=True, method="register", message=f"회원가입 성공: {alert_text}", account=acct)
            if any(k in alert_text for k in _fail):
                return LoginResult(success=False, method="register", message=f"회원가입 실패: {alert_text}", account=acct)

        swal_text = self.handle_swal(click_confirm=True) or ""
        if swal_text:
            if any(k in swal_text for k in _ok) and not any(k in swal_text for k in _fail):
                return LoginResult(success=True, method="register", message=f"회원가입 성공: {swal_text}", account=acct)
            if any(k in swal_text for k in _fail):
                return LoginResult(success=False, method="register", message=f"회원가입 실패: {swal_text}", account=acct)

        current = self.driver.current_url or ""
        if "register_result" in current:
            return LoginResult(success=True, method="register", message="회원가입 완료 (register_result)", account=acct)
        if "register" not in current and "login" not in current:
            return LoginResult(success=True, method="register", message="회원가입 완료 (페이지 이동 확인)", account=acct)
        if "login" in current and "register" not in current:
            return LoginResult(success=True, method="register", message="회원가입 완료 (로그인 페이지)", account=acct)

        try:
            src = self.driver.page_source or ""
            if "로그아웃" in src or "logout" in src:
                return LoginResult(success=True, method="register", message="회원가입 완료 (로그인 상태 확인)", account=acct)
            if ("회원가입" in src and "완료" in src) or "축하" in src:
                return LoginResult(success=True, method="register", message="회원가입 완료 (페이지 내 확인)", account=acct)
        except Exception:
            pass

        # Return ambiguous alert/swal text as failure if present
        leftover = alert_text or swal_text
        if leftover:
            return LoginResult(success=False, method="register", message=f"회원가입 결과: {leftover}", account=acct)

        return LoginResult(success=False, method="register", message="회원가입 결과 확인 불가", account=acct)

    def register(self, **kwargs: Any) -> LoginResult:
        raise NotImplementedError(f"{self.SITE_NAME}: register() not implemented")

    def get_profile(self) -> Profile:
        raise NotImplementedError(f"{self.SITE_NAME}: get_profile() not implemented")

    def get_boards(self) -> list[Board]:
        raise NotImplementedError(f"{self.SITE_NAME}: get_boards() not implemented")

    def get_posts(self, board_id: str, *, page: int = 1,
                  search_field: str = "", search_text: str = "",
                  sort_field: str = "", sort_order: str = "") -> list[Post]:
        raise NotImplementedError(f"{self.SITE_NAME}: get_posts() not implemented")

    def get_comments(self, post_id: str) -> list[Comment]:
        raise NotImplementedError(f"{self.SITE_NAME}: get_comments() not implemented")

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        raise NotImplementedError(f"{self.SITE_NAME}: write_post() not implemented")

    def write_comment(self, post_id: str, content: str) -> WriteResult:
        raise NotImplementedError(f"{self.SITE_NAME}: write_comment() not implemented")
