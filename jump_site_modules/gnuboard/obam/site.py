"""ObamSite - 오밤 (obam37.com, gnuboard 기반 + jump_shop JS)."""

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


class ObamSite(GnuboardSite):
    SITE_NAME = "오밤"

    COOKIE_KEYS = ["PHPSESSID"]
    # 메인 페이지의 basic_outlogin 폼을 사용
    LOGIN_URL_PATH = "/"
    LOGIN_CHECK_TEXT = "로그아웃"
    LOGIN_ID_SELECTOR = "#outlogin_mb_id"
    LOGIN_PW_SELECTOR = "#outlogin_mb_password"
    LOGIN_SUBMIT_SELECTOR = "#basic_outlogin input[type='submit'].login_btn"
    LOGIN_PRE_SUBMIT_DELAY = 0.5
    LOGIN_POST_SUBMIT_DELAY = 2.0

    # ── 팝업 처리 ──

    def _dismiss_popups(self) -> None:
        """오밤 메인 페이지의 hd_pop 팝업 제거 (로그인 버튼 가림 방지)."""
        try:
            self.driver.execute_script(
                """
                document.querySelectorAll('#hd_pop, .hd_pops, [id^="hd_pops_"]').forEach(function(el) {
                    try { el.remove(); } catch (_e) {}
                });
                // hd_pop 자체도 제거
                var root = document.getElementById('hd_pop');
                if (root) root.remove();
                """
            )
            self.emit("[오밤] 팝업 제거 완료", "DEBUG")
        except Exception as exc:
            self.emit(f"[오밤] 팝업 제거 중 오류 (무시): {exc}", "DEBUG")

    # ── Login hooks ──

    def _navigate_to_login(self) -> None:
        """로그인 페이지(=메인) 이동 + 팝업 닫기."""
        super()._navigate_to_login()
        # 페이지 로드 직후 팝업이 동적으로 표시되므로 잠깐 대기
        time.sleep(0.8)
        self._dismiss_popups()

    def _fill_login_form(self) -> None:
        """폼 채우기 전에도 팝업 한 번 더 닫기 (혹시 다시 떴을 경우)."""
        self._dismiss_popups()
        super()._fill_login_form()

    # ── Jump ──

    def jump(self) -> JumpResult:
        """메인 페이지에서 점프(N회) 링크 탐색 → jump_shop(wr_id) JS 호출 + alert 처리."""
        try:
            self.driver.get(f"{self.base_url}/")
            time.sleep(1.5)
            self.require_human_check()
            # 점프 링크가 팝업에 가려질 가능성도 차단
            self._dismiss_popups()
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

        before_count, wr_id = self._parse_jump_link()
        if wr_id is None:
            return JumpResult(
                status=STATUS_FAILED,
                message="점프 링크를 찾을 수 없습니다. (업소회원 여부 확인)",
            )

        self.emit(
            f"[오밤] 점프 시작 (wr_id={wr_id}, 남은 횟수: {before_count})",
            "INFO",
        )

        if before_count == 0:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="오늘 점프 가능 횟수를 모두 사용하였습니다.",
            )

        # confirm() 우회 + jump_shop 직접 호출
        try:
            self.driver.execute_script(
                "window.confirm = function() { return true; };"
            )
            self.driver.execute_script(
                "jump_shop(arguments[0]);", str(wr_id)
            )
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"jump_shop 호출 실패: {exc}",
            )

        # alert 대기 및 처리 (첫 번째 alert 또는 confirm 이후 나오는 결과)
        alert_msg = self._wait_and_read_alert(timeout=6.0)

        # 혹시 "배너 점프를 실행하시겠습니까?" 가 뜨면 accept 후 다음 alert 대기
        if alert_msg and ("하시겠습니까" in alert_msg or "실행" in alert_msg):
            # 이미 accept 됐을 것 — 다음 alert 대기
            alert_msg = self._wait_and_read_alert(timeout=5.0)

        return self._classify_alert(alert_msg, before_count)

    def _wait_and_read_alert(self, timeout: float = 5.0) -> str:
        try:
            WebDriverWait(self.driver, timeout).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            text = alert.text or ""
            alert.accept()
            time.sleep(0.4)
            return text
        except Exception:
            return ""

    def _classify_alert(self, text: str, before_count: int | None) -> JumpResult:
        t = (text or "").strip()
        self.emit(f"[오밤] 점프 alert: {t!r}", "DEBUG")

        if not t:
            # alert 가 없었음 — 성공/실패 불확실 → 횟수 재확인 시도
            try:
                self.driver.get(f"{self.base_url}/")
                time.sleep(1.2)
            except Exception:
                pass
            after_count, _ = self._parse_jump_link()
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
                message="점프 결과 확인 불가 (alert 미발생)",
            )

        # 성공
        if "완료" in t or "되었습니다" in t:
            return JumpResult(
                status=STATUS_SUCCESS,
                message=t,
                remaining_count=(before_count - 1) if before_count and before_count > 0 else -1,
            )

        # 쿨다운 / 초과
        if "분에 한번" in t or "분 후" in t or "대기" in t or "분만" in t:
            return JumpResult(status=STATUS_COOLDOWN, message=t)
        if "모두 사용" in t or "초과" in t or "횟수" in t:
            return JumpResult(status=STATUS_COOLDOWN, message=t)

        # 로그인 필요
        if "로그인" in t or "회원" in t:
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message=t)

        # 기타 실패
        return JumpResult(status=STATUS_FAILED, message=t)

    def _parse_jump_link(self) -> tuple[int | None, str | None]:
        """메인 페이지 로그인 후 보이는 점프 링크에서 (남은 횟수, wr_id) 추출.

        HTML 예시:
          <a class="menu" href="javascript:jump_shop('18369');">
            <strong>점프(30회)</strong>
          </a>
        """
        try:
            links = self.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='jump_shop']"
            )
            for link in links:
                href = link.get_attribute("href") or ""
                text = (link.text or "").strip()
                m = re.search(r"jump_shop\(\s*['\"]?(\d+)['\"]?\s*\)", href)
                if not m:
                    continue
                wr_id = m.group(1)

                count: int | None = None
                mn = re.search(r"(\d+)\s*회", text)
                if mn:
                    count = int(mn.group(1))
                return count, wr_id
        except Exception:
            pass
        return None, None
