"""DaegubamSite - 대구의밤 (eorn3.com, Laravel 기반)."""

from __future__ import annotations

import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    BaseSite,
)
from ...types import JumpResult, LoginResult


class DaegubamSite(BaseSite):
    SITE_NAME = "대구의밤"

    COOKIE_KEYS = ["laravel_session", "XSRF-TOKEN"]

    # ── 로그인 상태 판별 ──

    def _is_logged_in(self) -> bool:
        """DOM 기반 로그인 상태 판별."""
        # 1) URL에 /login 이 있으면 비로그인
        try:
            cur = self.driver.current_url or ""
            if "/login" in cur:
                return False
        except Exception:
            pass
        # 2) 로그아웃 form 또는 /merchant 관련 페이지 접근 가능 여부
        try:
            logout_forms = self.driver.find_elements(
                By.CSS_SELECTOR, "form[action*='/logout']"
            )
            if logout_forms:
                return True
        except Exception:
            pass
        # 3) 폼에 로그인 input 존재 → 비로그인
        try:
            login_inputs = self.driver.find_elements(
                By.CSS_SELECTOR, "input[name='username'], input[id='username']"
            )
            if login_inputs:
                return False
        except Exception:
            pass
        return False

    # ── Login ──

    def login(self) -> LoginResult:
        """POST /login 을 Selenium으로 폼 제출. CSRF(_token)는 폼에서 자동 전송됨."""
        self.emit(
            f"[대구의밤] 로그인 시작: {self.base_url} (ID: {self.username})", "INFO"
        )
        _acct = {"username": self.username, "password": self.password}

        try:
            self.driver.get(f"{self.base_url}/login")
            time.sleep(1.5)
            self.require_human_check()
        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"로그인 페이지 접속 실패: {exc}", account=_acct,
            )

        # 이미 로그인 상태라면 /merchant 등으로 리디렉션됨
        if self._is_logged_in():
            self.emit("[대구의밤] 이미 로그인 상태입니다.", "INFO")
            return LoginResult(
                success=True, method="already",
                message="이미 로그인 상태", account=_acct,
            )

        try:
            uid_el = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='username']"))
            )
            pw_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
        except Exception:
            return LoginResult(
                success=False, method="form",
                message="로그인 폼을 찾을 수 없습니다.", account=_acct,
            )

        try:
            uid_el.clear()
            uid_el.send_keys(self.username)
            pw_el.clear()
            pw_el.send_keys(self.password)
            time.sleep(0.3)

            # form.submit() 으로 제출 (CSRF _token hidden input 자동 포함)
            self.driver.execute_script(
                "arguments[0].closest('form').submit();", pw_el
            )
            time.sleep(2.5)
        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"로그인 제출 오류: {exc}", account=_acct,
            )

        # 검증
        if self._is_logged_in():
            self.emit("[대구의밤] 로그인 성공.", "INFO")
            return LoginResult(
                success=True, method="form",
                message="로그인 성공", account=_acct,
            )

        # 실패 메시지 추출 (Laravel은 session flash 또는 inline error)
        err = self._extract_login_error()
        return LoginResult(
            success=False, method="form",
            message=err or "로그인 실패 (ID/PW 확인)",
            account=_acct,
        )

    def _extract_login_error(self) -> str:
        try:
            el = self.driver.find_element(
                By.CSS_SELECTOR, ".alert-danger, .text-red-500, .text-red-600, .text-danger"
            )
            t = (el.text or "").strip()
            if t:
                return t
        except Exception:
            pass
        try:
            src = self.driver.page_source or ""
            m = re.search(
                r"(아이디|비밀번호|일치하지|잘못|오류|차단|승인).{0,60}", src
            )
            if m:
                return m.group(0).strip()
        except Exception:
            pass
        return ""

    # ── Jump ──

    def jump(self) -> JumpResult:
        """/merchant/jump 페이지에서 점프 폼 제출 + 결과 파싱."""
        try:
            self.driver.get(f"{self.base_url}/merchant/jump")
            time.sleep(1.5)
            self.require_human_check()
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"점프 페이지 접속 실패: {exc}",
            )

        if not self._is_logged_in():
            return JumpResult(
                status=STATUS_LOGIN_REQUIRED,
                message="로그인 상태가 아닙니다.",
            )

        # 점프 폼 확인 (없으면 이미 소진 상태)
        form = None
        try:
            forms = self.driver.find_elements(
                By.CSS_SELECTOR, "form[action*='/merchant/jump/execute']"
            )
            if forms:
                form = forms[0]
        except Exception:
            pass

        # 남은 횟수 파싱
        before_count = self._parse_remaining_count()
        self.emit(
            f"[대구의밤] 현재 남은 횟수: {before_count if before_count is not None else '확인 불가'}",
            "INFO",
        )

        # 폼이 없고 "모두 사용" 텍스트가 있으면 COOLDOWN
        if form is None:
            if self._page_says_exhausted():
                return JumpResult(
                    status=STATUS_COOLDOWN,
                    message="오늘 점프 가능 횟수를 모두 사용했습니다.",
                )
            return JumpResult(
                status=STATUS_FAILED,
                message="점프 폼을 찾을 수 없습니다. (업소회원 여부 확인)",
            )

        if before_count == 0:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="오늘 점프 가능 횟수를 모두 사용했습니다.",
            )

        # confirm() 우회 후 폼 제출 (confirm을 true로 고정)
        try:
            self.driver.execute_script("window.confirm = function() { return true; };")
            self.driver.execute_script("arguments[0].submit();", form)
            time.sleep(2.5)
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"점프 제출 오류: {exc}",
            )

        # 결과 파싱: 성공 문구 or 소진 문구
        try:
            src = self.driver.page_source or ""
        except Exception:
            src = ""

        if "점프 완료" in src or "최상단으로 이동" in src:
            m = re.search(r"남은\s*횟수\s*[:：]\s*(\d+)", src)
            remain = int(m.group(1)) if m else -1
            return JumpResult(
                status=STATUS_SUCCESS,
                message=f"점프 완료 (남은 횟수: {remain if remain >= 0 else '확인 불가'})",
                remaining_count=remain if remain >= 0 else -1,
            )

        if "모두 사용" in src or "내일 다시" in src:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="오늘 점프 가능 횟수를 모두 사용했습니다.",
            )

        # 횟수 비교 fallback
        after_count = self._parse_remaining_count()
        if (
            before_count is not None
            and after_count is not None
            and after_count < before_count
        ):
            return JumpResult(
                status=STATUS_SUCCESS,
                message=f"점프 완료 (남은 횟수: {after_count})",
                remaining_count=after_count,
            )

        return JumpResult(
            status=STATUS_FAILED,
            message="점프 결과를 확인할 수 없습니다.",
        )

    def _parse_remaining_count(self) -> int | None:
        """'오늘 남은 횟수' 카드의 숫자 파싱."""
        try:
            src = self.driver.page_source or ""
        except Exception:
            return None

        # 1) 버튼 텍스트 "점프하기 (30회 남음)" 에서 추출
        m = re.search(r"\((\d+)회\s*남음\)", src)
        if m:
            return int(m.group(1))

        # 2) 카드 "오늘 남은 횟수" 주변 숫자
        m = re.search(
            r"오늘\s*남은\s*횟수.*?(\d+)",
            src,
            flags=re.DOTALL,
        )
        if m:
            return int(m.group(1))

        return None

    def _page_says_exhausted(self) -> bool:
        try:
            src = self.driver.page_source or ""
            return "모두 사용" in src or "내일 다시 점프할 수 있습니다" in src
        except Exception:
            return False
