"""AlbamSite - 알밤 (XpressEngine, 게시물당 점프 / 여러 게시물 지원)."""

from __future__ import annotations

import re
import time
from typing import Iterable

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


class AlbamSite(BaseSite):
    SITE_NAME = "알밤"

    # ── Login ──

    def login(self) -> LoginResult:
        """홈페이지 헤더 로그인 폼(/index.php?act=procMemberLogin) 제출."""
        self.emit(f"[알밤] 로그인 시작: {self.base_url} (ID: {self.username})", "INFO")
        _acct = {"user_id": self.username, "password": self.password}

        try:
            self.driver.get(f"{self.base_url}/")
            time.sleep(1.5)
            self.require_human_check()
        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"홈페이지 접속 실패: {exc}", account=_acct,
            )

        # 이미 로그인 상태인지 확인
        if self.page_contains("로그아웃"):
            self.emit("[알밤] 이미 로그인 상태입니다.", "INFO")
            return LoginResult(
                success=True, method="already",
                message="이미 로그인 상태", account=_acct,
            )

        # 실제 CSRF 토큰을 포함한 로그인 폼을 찾아 제출
        try:
            uid_el = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='user_id']")
                )
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
            # 폼 제출: Enter 키 or submit 버튼 찾기
            try:
                submit = self.driver.find_element(
                    By.CSS_SELECTOR, "input[type='submit'].login"
                )
                submit.click()
            except Exception:
                # fallback: 부모 폼 제출
                self.driver.execute_script(
                    "arguments[0].closest('form').submit();", pw_el
                )
            time.sleep(2.0)
        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"로그인 제출 실패: {exc}", account=_acct,
            )

        # 로그인 성공 여부 확인
        if self.page_contains("로그아웃") or self.page_contains("마이페이지"):
            self.emit("[알밤] 로그인 성공.", "INFO")
            return LoginResult(
                success=True, method="form",
                message="로그인 성공", account=_acct,
            )

        # 실패 메시지 추출 시도 (alert 또는 에러 텍스트)
        err_msg = self._extract_login_error()
        return LoginResult(
            success=False, method="form",
            message=err_msg or "로그인 실패 (ID/PW 확인 필요)",
            account=_acct,
        )

    def _extract_login_error(self) -> str:
        """로그인 실패 시 에러 메시지 추출."""
        try:
            WebDriverWait(self.driver, 1).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            text = alert.text
            alert.accept()
            return text
        except Exception:
            pass

        try:
            src = self.driver.page_source or ""
            m = re.search(r"(아이디|비밀번호|일치하지|탈퇴|차단|승인)[^<>\n]{0,50}", src)
            if m:
                return m.group(0).strip()
        except Exception:
            pass
        return ""

    # ── Jump ──

    def jump(self) -> JumpResult:
        """기본 jump: 등록된 게시물이 없음을 알림 (handler가 post_urls로 호출)."""
        return JumpResult(
            status=STATUS_FAILED,
            message="알밤은 게시물 URL을 등록해야 합니다. 작업 설정에서 URL을 추가해주세요.",
        )

    def jump_posts(self, post_urls: Iterable[str]) -> list[tuple[str, JumpResult]]:
        """여러 게시물에 대해 순차적으로 점프 수행. (url, result) 쌍 리스트 반환."""
        results: list[tuple[str, JumpResult]] = []
        for url in post_urls:
            url = (url or "").strip()
            if not url:
                continue
            result = self.jump_single_post(url)
            results.append((url, result))
            # 횟수 초과 시 더 이상 시도하지 않음
            if result.status == STATUS_COOLDOWN:
                self.emit(
                    f"[알밤] 점프 횟수 초과 감지 — 남은 게시물은 건너뜁니다.",
                    "WARNING",
                )
                break
        return results

    def jump_single_post(self, post_url: str) -> JumpResult:
        """하나의 게시물 URL에 대한 점프 실행."""
        self.emit(f"[알밤] 점프: {post_url}", "INFO")

        try:
            self.driver.get(post_url)
            time.sleep(1.2)
            self.require_human_check()
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"게시물 접속 실패: {exc}",
            )

        # 로그인 체크
        if not self.page_contains("로그아웃"):
            return JumpResult(
                status=STATUS_LOGIN_REQUIRED,
                message="로그인 상태가 아닙니다.",
            )

        # 점프 버튼 검색: onclick에 procSejin7940_jumpDocumentJump 또는
        # 횟수 초과 alert이 들어있는 버튼
        doc_id: str | None = None
        button_onclick: str = ""
        try:
            buttons = self.driver.find_elements(
                By.CSS_SELECTOR, "a.btn[onclick]"
            )
            for btn in buttons:
                oc = btn.get_attribute("onclick") or ""
                if "procSejin7940_jumpDocumentJump" in oc:
                    button_onclick = oc
                    m = re.search(r"procSejin7940_jumpDocumentJump['\"]?,\s*(\d+)", oc)
                    if m:
                        doc_id = m.group(1)
                    break
                if "상단점프" in (btn.text or "") and "alert(" in oc:
                    # 이미 횟수 초과 상태
                    button_onclick = oc
                    break
        except Exception:
            pass

        # URL에서 document_srl 추출 시도 (fallback)
        if not doc_id:
            m = re.search(r"document_srl[=/](\d+)", post_url)
            if m:
                doc_id = m.group(1)

        # 버튼이 횟수 초과 alert 상태라면 바로 cooldown 리턴
        if button_onclick and "초과" in button_onclick:
            m = re.search(r"alert\(['\"]([^'\"]+)['\"]\)", button_onclick)
            msg = m.group(1) if m else "오늘 상단점프 가능 횟수를 초과했습니다"
            return JumpResult(status=STATUS_COOLDOWN, message=msg)

        if not doc_id:
            return JumpResult(
                status=STATUS_FAILED,
                message="점프 버튼을 찾을 수 없습니다. (URL 또는 게시물 확인)",
            )

        # JS 직접 호출
        try:
            self.driver.execute_script(
                "doCallModuleAction('sejin7940_jump', "
                "'procSejin7940_jumpDocumentJump', arguments[0]);",
                int(doc_id),
            )
        except Exception as exc:
            self.emit(f"[알밤] doCallModuleAction 호출 후 상태: {exc}", "DEBUG")

        # alert 대기 및 처리
        alert_msg = ""
        try:
            WebDriverWait(self.driver, 6).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_msg = alert.text or ""
            alert.accept()
            time.sleep(0.5)
        except Exception:
            pass

        return self._classify_jump_alert(alert_msg)

    def _classify_jump_alert(self, text: str) -> JumpResult:
        t = (text or "").strip()
        if not t:
            return JumpResult(status=STATUS_SUCCESS, message="점프 완료 (응답 미확인)")
        if "올렸습니다" in t or "완료" in t:
            return JumpResult(status=STATUS_SUCCESS, message=t)
        if "초과" in t or "횟수" in t:
            return JumpResult(status=STATUS_COOLDOWN, message=t)
        if "로그인" in t or "회원" in t:
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message=t)
        return JumpResult(status=STATUS_FAILED, message=t)
