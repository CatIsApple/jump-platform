"""BusanbibigiSite - 부산비비기 (busanb37.net, gnuboard 기반)."""

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
)
from ...gnuboard_base import GnuboardSite
from ...types import JumpResult


class BusanbibigiSite(GnuboardSite):
    SITE_NAME = "부산비비기"

    COOKIE_KEYS = ["PHPSESSID"]
    # 메인 페이지의 outlogin 폼을 사용 (로그인 페이지 별도 없음)
    LOGIN_URL_PATH = "/"
    LOGIN_CHECK_TEXT = "로그아웃"
    LOGIN_ID_SELECTOR = "#outlogin_mb_id"
    LOGIN_PW_SELECTOR = "#outlogin_mb_password"
    LOGIN_SUBMIT_SELECTOR = "#basic_outlogin button[type='submit']"
    LOGIN_PRE_SUBMIT_DELAY = 0.5
    LOGIN_POST_SUBMIT_DELAY = 2.0

    # ── Jump ──

    def jump(self) -> JumpResult:
        """메인 페이지에서 제휴점프 링크를 찾아 실행 + 전/후 횟수 비교로 성공 판정."""
        # 1. 메인 페이지 이동 + 현재 남은 점프 횟수 및 jumpup URL 확인
        try:
            self.driver.get(f"{self.base_url}/")
            time.sleep(1.5)
            self.require_human_check()
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"메인 페이지 접속 실패: {exc}",
            )

        if not self.page_contains("로그아웃"):
            return JumpResult(
                status=STATUS_LOGIN_REQUIRED,
                message="로그인 상태가 아닙니다.",
            )

        before_count, jump_url = self._parse_jump_button()
        if jump_url is None:
            return JumpResult(
                status=STATUS_FAILED,
                message="제휴점프 링크를 찾을 수 없습니다. (업소회원이 아니거나 페이지 구조 변경)",
            )

        self.emit(
            f"[부산비비기] 점프 시작 (남은 횟수: {before_count})",
            "INFO",
        )

        if before_count == 0:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="오늘 사용가능한 점프를 모두 사용하였습니다.",
            )

        # 2. 점프 URL로 직접 이동 (confirm() JS는 브라우저단, 실제는 GET)
        try:
            self.driver.get(jump_url)
            time.sleep(1.5)
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"점프 URL 이동 실패: {exc}",
            )

        # 3. alert 처리 (초과 시 "오늘 사용가능한 점프를 모두 사용하였습니다")
        alert_msg = ""
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_msg = alert.text or ""
            alert.accept()
            time.sleep(0.5)
        except Exception:
            pass

        if alert_msg:
            if "모두 사용" in alert_msg or "초과" in alert_msg:
                return JumpResult(status=STATUS_COOLDOWN, message=alert_msg)
            if "로그인" in alert_msg or "회원" in alert_msg:
                return JumpResult(status=STATUS_LOGIN_REQUIRED, message=alert_msg)
            return JumpResult(status=STATUS_FAILED, message=alert_msg)

        # 4. 메인 페이지 재방문하여 횟수 비교
        try:
            self.driver.get(f"{self.base_url}/")
            time.sleep(1.5)
        except Exception:
            pass

        after_count, _ = self._parse_jump_button()

        self.emit(
            f"[부산비비기] 점프 후 남은 횟수: {after_count} (이전: {before_count})",
            "INFO",
        )
        self.save_cookies(self.COOKIE_KEYS)

        if after_count is not None and before_count is not None:
            if after_count < before_count:
                return JumpResult(
                    status=STATUS_SUCCESS,
                    message=f"점프 완료 (남은 횟수: {after_count})",
                    remaining_count=after_count,
                )
            if after_count == 0:
                return JumpResult(
                    status=STATUS_COOLDOWN,
                    message="점프 횟수 소진",
                )
            return JumpResult(
                status=STATUS_FAILED,
                message=f"횟수 변화 없음 (전/후 모두 {before_count})",
            )

        return JumpResult(
            status=STATUS_SUCCESS,
            message="점프 실행 (횟수 확인 불가)",
        )

    def _parse_jump_button(self) -> tuple[int | None, str | None]:
        """메인 페이지의 제휴점프 링크에서 (남은 횟수, href) 추출.

        HTML 예시:
          <a onclick="return confirm('점프하시겠습니까?');"
             href="https://busanb37.net/bbs/jumpup.php?bo_table=b_chul&wr_id=7953"
             class="...">
             제휴점프 <b class="orangered">45</b>
          </a>
        """
        try:
            links = self.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='jumpup.php']"
            )
            for link in links:
                href = link.get_attribute("href") or ""
                text = (link.text or "").strip()
                if "jumpup.php" not in href:
                    continue
                # 링크 안의 숫자 추출 (우선순위: <b> 태그 → 링크 텍스트)
                count: int | None = None
                try:
                    num_el = link.find_element(By.CSS_SELECTOR, "b")
                    num_text = (num_el.text or "").strip()
                    if num_text.isdigit():
                        count = int(num_text)
                except Exception:
                    pass
                if count is None:
                    m = re.search(r"(\d+)", text)
                    if m:
                        count = int(m.group(1))
                return count, href
        except Exception:
            pass
        return None, None
