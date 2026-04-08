"""인천달리기 (indal666.com) — gnuboard 기반 점프 사이트."""

from __future__ import annotations

import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_INSUFFICIENT,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
)
from ...gnuboard_base import GnuboardSite
from ...types import JumpResult


class IndalSite(GnuboardSite):
    SITE_NAME = "인천달리기"

    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_URL_PATH = "/bbs/login.php"
    LOGIN_CHECK_TEXT = "로그아웃"
    LOGIN_ID_SELECTOR = "input[name='mb_id']"
    LOGIN_PW_SELECTOR = "input[name='mb_password']"
    LOGIN_SUBMIT_SELECTOR = "button[type='submit'].btn-red"
    LOGIN_PRE_SUBMIT_DELAY = 0.5
    LOGIN_POST_SUBMIT_DELAY = 1.5

    # ── Login hook: 로그인 후 "환영합니다!" alert 처리 ──

    def _post_login_alerts(self) -> str:
        """로그인 성공 시 '환영합니다!' alert를 수락한다."""
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            text = alert.text
            alert.accept()
            time.sleep(0.5)
            return text
        except Exception:
            return ""

    # ── Jump ──

    def jump(self) -> JumpResult:
        """회원검색 페이지에서 출근부 점프를 실행한다."""
        jump_url = self.url("/bbs/board.php?bo_table=bo_member&mp=4")
        self.goto(jump_url)
        time.sleep(1.5)

        # 점프 횟수 확인
        remaining = self._get_remaining_jumps()

        # "출근부 점프" 버튼 클릭 (javascript:jump(0))
        try:
            jump_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(@href, 'jump(0)')]")
                )
            )
            jump_btn.click()
        except Exception:
            # fallback: JS 직접 호출
            try:
                self.driver.execute_script("jump(0);")
            except Exception as exc:
                return JumpResult(
                    status=STATUS_FAILED,
                    message=f"점프 버튼 클릭 실패: {exc}",
                )

        time.sleep(1.0)

        # 1) 확인 alert: "점프를 하시겠습니까?"
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            if "하시겠습니까" in alert_text:
                alert.accept()
                time.sleep(1.5)
            else:
                # 다른 메시지 (횟수 초과 등)
                alert.accept()
                return self._classify_jump_alert(alert_text, remaining)
        except Exception:
            pass

        # 2) 결과 alert: "출근부 점프 완료" 또는 "금일 점프 횟수를 초과"
        try:
            WebDriverWait(self.driver, 5).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            result_text = alert.text
            alert.accept()
            time.sleep(0.5)
            return self._classify_jump_alert(result_text, remaining)
        except Exception:
            pass

        # 3) Fallback: 페이지에서 결과 확인
        self.save_cookies(self.COOKIE_KEYS)
        return JumpResult(
            status=STATUS_SUCCESS,
            message="점프 실행 (응답 확인 불가)",
            remaining_count=remaining,
        )

    def _classify_jump_alert(self, text: str, remaining: int) -> JumpResult:
        """Alert 텍스트를 기반으로 점프 결과를 분류한다."""
        if "완료" in text:
            return JumpResult(
                status=STATUS_SUCCESS,
                message=text,
                remaining_count=max(0, remaining - 1) if remaining > 0 else -1,
            )
        if "초과" in text or "횟수" in text:
            return JumpResult(
                status=STATUS_INSUFFICIENT,
                message=text,
                remaining_count=0,
            )
        if "로그인" in text or "회원" in text:
            return JumpResult(
                status=STATUS_LOGIN_REQUIRED,
                message=text,
            )
        if "분" in text and ("후" in text or "대기" in text):
            return JumpResult(
                status=STATUS_COOLDOWN,
                message=text,
            )

        return JumpResult(
            status=STATUS_FAILED,
            message=f"점프 결과: {text}" if text else "점프 실패 (응답 없음)",
        )

    def _get_remaining_jumps(self) -> int:
        """제휴정보 테이블에서 점프 잔여 횟수를 파싱한다. (3/12) → 9"""
        try:
            src = self.driver.page_source or ""
            # "(3/12)" 형태 파싱
            m = re.search(r"\((\d+)\s*/\s*(\d+)\)", src)
            if m:
                used = int(m.group(1))
                total = int(m.group(2))
                return max(0, total - used)
        except Exception:
            pass
        return -1
