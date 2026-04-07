"""GnuboardSite - common login logic for gnuboard-based sites."""

from __future__ import annotations

import time
from abc import abstractmethod
from typing import Any

from .base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    BaseSite,
)
from .types import JumpResult, LoginResult


class GnuboardSite(BaseSite):
    """Abstract base for gnuboard-based sites.

    모든 gnuboard 사이트는 이 클래스를 상속하고,
    **클래스 속성 + 템플릿 메서드 훅**만 오버라이드한다.
    login()을 직접 오버라이드하지 않는다.
    """

    # ══════════════════════════════════════════════════════════
    #  클래스 속성 (사이트별 오버라이드)
    # ══════════════════════════════════════════════════════════

    COOKIE_KEYS: list[str] = ["PHPSESSID"]
    LOGIN_URL_PATH: str = "/bbs/login.php"
    LOGIN_CHECK_TEXT: str = "로그아웃"
    LOGIN_ID_SELECTOR: str = "input[name='mb_id']"
    LOGIN_PW_SELECTOR: str = "input[name='mb_password']"
    LOGIN_SUBMIT_SELECTOR: str = "button[type='submit']"
    LOGIN_FORM_CONTAINER: str | None = None  # e.g. "#flogin", ".layer_login"
    LOGIN_PRE_SUBMIT_DELAY: float = 0.5
    LOGIN_POST_SUBMIT_DELAY: float = 1.0
    NAVER_WARMUP_SLEEP: float = 0.5
    GOTO_VIA_SCRIPT: bool = False

    # True → 쿠키 주입 시도 없이 바로 로그인 페이지 이동
    DIRECT_LOGIN: bool = False

    # True → page_source에서 LOGIN_CHECK_TEXT 검색
    # False → wait_for_text()로 DOM 검색 (기본)
    LOGIN_CHECK_VIA_SOURCE: bool = False

    # 로그인 확인 시 추가로 체크할 텍스트 (or 조건)
    LOGIN_CHECK_ALT_TEXT: str | None = None

    # ══════════════════════════════════════════════════════════
    #  login() — Template Method (오버라이드 금지)
    # ══════════════════════════════════════════════════════════

    def login(self) -> LoginResult:
        """통합 로그인 플로우 (Template Method).

        1. 워밍업 (선택)
        2. 사이트 이동
        3. 쿠키 주입 → 로그인 확인
        4. 로그인 페이지 이동 → 폼 입력 → 제출
        5. 로그인 후 알림/팝업 처리
        6. 최종 로그인 확인
        """
        self.emit(f"[{self.SITE_NAME}] 시작: {self.base_url} (ID: {self.username})", "INFO")

        _acct = {"mb_id": self.username, "mb_password": self.password}

        # ── Step 1: 워밍업 ──
        self._warmup()

        # ── Step 2~3: 사이트 이동 + 쿠키 로그인 시도 ──
        if not self.DIRECT_LOGIN:
            self.goto(self.base_url, via_script=self.GOTO_VIA_SCRIPT)
            self.require_human_check()

            self._delete_cookies()
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

            if self._check_logged_in():
                self.emit(f"[{self.SITE_NAME}] 쿠키 로그인 상태 확인.", "INFO")
                return LoginResult(success=True, method="cookie", message="쿠키 로그인 성공", account=_acct)

            # 쿠키 실패 → 정리
            self._delete_cookies()
            try:
                self.driver.refresh()
            except Exception:
                pass
        else:
            # DIRECT_LOGIN: 바로 로그인 페이지로
            self._navigate_to_login()
            self.require_human_check()

            if self._check_logged_in():
                self.emit(f"[{self.SITE_NAME}] 이미 로그인 상태.", "INFO")
                return LoginResult(success=True, method="already", message="이미 로그인 상태", account=_acct)

        # ── Step 4: 로그인 페이지 이동 + 폼 입력 ──
        if not self.DIRECT_LOGIN:
            self._navigate_to_login()
        time.sleep(self.LOGIN_PRE_SUBMIT_DELAY)
        self.require_human_check()

        try:
            self._fill_login_form()
        except Exception:
            return LoginResult(success=False, method="form", message="로그인 폼 입력/제출 실패", account=_acct)

        time.sleep(self.LOGIN_POST_SUBMIT_DELAY)
        self.require_human_check()

        # ── Step 5: 로그인 후 알림/팝업 처리 ──
        alert_msg = self._post_login_alerts() or ""

        # ── Step 6: 최종 로그인 확인 ──
        if not self._check_logged_in():
            fail_msg = alert_msg if alert_msg else f"로그인 실패({self.LOGIN_CHECK_TEXT} 표시 없음)"
            return LoginResult(
                success=False,
                method="form",
                message=fail_msg,
                account=_acct,
            )

        self.save_cookies(self.COOKIE_KEYS)
        self.emit(f"[{self.SITE_NAME}] 로그인 성공.", "INFO")
        return LoginResult(success=True, method="form", message="로그인 성공", account=_acct)

    # ══════════════════════════════════════════════════════════
    #  Template Method 훅 (사이트별 오버라이드 가능)
    # ══════════════════════════════════════════════════════════

    def _warmup(self) -> None:
        """네이버 워밍업. 필요 없는 사이트는 오버라이드."""
        self.naver_warmup(sleep_s=self.NAVER_WARMUP_SLEEP)

    def _check_logged_in(self) -> bool:
        """로그인 상태 확인."""
        if self.LOGIN_CHECK_VIA_SOURCE:
            result = self.page_contains(self.LOGIN_CHECK_TEXT)
            if not result and self.LOGIN_CHECK_ALT_TEXT:
                result = self.page_contains(self.LOGIN_CHECK_ALT_TEXT)
            return result
        result = self.wait_for_text(self.LOGIN_CHECK_TEXT, timeout=2.0)
        if not result and self.LOGIN_CHECK_ALT_TEXT:
            result = self.wait_for_text(self.LOGIN_CHECK_ALT_TEXT, timeout=1.0)
        return result

    def _navigate_to_login(self) -> None:
        """로그인 페이지 이동."""
        login_url = self.url(self.LOGIN_URL_PATH)
        self.goto(login_url, via_script=self.GOTO_VIA_SCRIPT)

    def _fill_login_form(self) -> None:
        """로그인 폼 입력 및 제출."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        container = self.LOGIN_FORM_CONTAINER or ""
        id_sel = f"{container} {self.LOGIN_ID_SELECTOR}".strip()
        pw_sel = f"{container} {self.LOGIN_PW_SELECTOR}".strip()
        submit_sel = f"{container} {self.LOGIN_SUBMIT_SELECTOR}".strip()

        WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, id_sel))
        )
        id_el = self.driver.find_element(By.CSS_SELECTOR, id_sel)
        pw_el = self.driver.find_element(By.CSS_SELECTOR, pw_sel)
        id_el.clear()
        id_el.send_keys(self.username)
        pw_el.clear()
        pw_el.send_keys(self.password)

        submit_el = self.driver.find_element(By.CSS_SELECTOR, submit_sel)
        submit_el.click()

    def _post_login_alerts(self) -> str:
        """로그인 후 alert/팝업 처리. alert 텍스트 반환 (없으면 빈 문자열)."""
        return ""

    # ══════════════════════════════════════════════════════════
    #  Cookie helpers
    # ══════════════════════════════════════════════════════════

    def _delete_cookies(self) -> None:
        for k in self.COOKIE_KEYS:
            try:
                self.driver.delete_cookie(k)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════
    #  get_remaining_jumps
    # ══════════════════════════════════════════════════════════

    def get_remaining_jumps(self) -> int:
        """페이지에서 남은 점프 횟수 추출. 기본 구현은 -1 (알 수 없음)."""
        return -1

    def register(self, **kwargs: Any) -> LoginResult:
        raise NotImplementedError(f"{self.SITE_NAME}: register() not implemented")

    # ══════════════════════════════════════════════════════════
    #  jump() — 사이트별 구현 필수
    # ══════════════════════════════════════════════════════════

    @abstractmethod
    def jump(self) -> JumpResult:
        """Execute the site-specific jump action."""
