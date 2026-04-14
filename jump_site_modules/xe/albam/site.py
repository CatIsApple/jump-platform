"""AlbamSite - 아이러브밤 (albam9.com, XE 프레임워크, 게시물별 점프)."""

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
    SITE_NAME = "아이러브밤"

    # ── 로그인 상태 판별 ──

    def _is_logged_in(self) -> bool:
        """실제 로그인 여부를 DOM 기반으로 확인.

        텍스트 매칭은 JS/숨김 요소에서 오탐이 나므로 DOM 요소로 확정.
        """
        # 1. 로그아웃 링크 존재 (가장 확실한 지표)
        try:
            els = self.driver.find_elements(
                By.CSS_SELECTOR, "a[href*='dispMemberLogout'], a[href*='procMemberLogout']"
            )
            if els:
                return True
        except Exception:
            pass

        # 2. 로그인 폼(sun-login 헤더 위젯 or login_widget) 미존재
        try:
            login_form = self.driver.find_elements(
                By.CSS_SELECTOR, "#sun_login, #fo_login_widget"
            )
            if login_form:
                # 폼이 있으면 로그인 안 된 상태
                return False
        except Exception:
            pass

        # 3. 실장전용메뉴 div 존재 (로그인 후 나타남)
        try:
            manager = self.driver.find_elements(
                By.CSS_SELECTOR, "div.sm_item.manager"
            )
            if manager:
                return True
        except Exception:
            pass

        return False

    # ── Login ──

    def login(self) -> LoginResult:
        """홈페이지 헤더 로그인 폼 제출 → /index.php?act=procMemberLogin."""
        self.emit(f"[아이러브밤] 로그인 시작: {self.base_url} (ID: {self.username})", "INFO")
        _acct = {"user_id": self.username, "password": self.password}

        try:
            self.driver.get(f"{self.base_url}/")
            time.sleep(2.0)
            self.require_human_check()
        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"홈페이지 접속 실패: {exc}", account=_acct,
            )

        # 이미 로그인 상태인지 확인 (DOM 기반)
        if self._is_logged_in():
            self.emit("[아이러브밤] 이미 로그인 상태입니다.", "INFO")
            return LoginResult(
                success=True, method="already",
                message="이미 로그인 상태", account=_acct,
            )

        # 로그인 폼 찾기 — 헤더의 sun_login 우선, 없으면 별도 로그인 페이지
        self.emit("[아이러브밤] 로그인 폼 탐색 중...", "DEBUG")
        uid_el = None
        pw_el = None
        for selector_pair in [
            ("#sun_login input[name='user_id']", "#sun_login input[name='password']"),
            ("#fo_login_widget input[name='user_id']", "#fo_login_widget input[name='password']"),
            ("input[name='user_id']", "input[name='password']"),
        ]:
            try:
                uid_candidates = self.driver.find_elements(By.CSS_SELECTOR, selector_pair[0])
                pw_candidates = self.driver.find_elements(By.CSS_SELECTOR, selector_pair[1])
                # 가시 상태 요소 우선
                for u in uid_candidates:
                    if u.is_displayed():
                        uid_el = u
                        break
                if uid_el is None and uid_candidates:
                    uid_el = uid_candidates[0]
                for p in pw_candidates:
                    if p.is_displayed():
                        pw_el = p
                        break
                if pw_el is None and pw_candidates:
                    pw_el = pw_candidates[0]
                if uid_el and pw_el:
                    self.emit(
                        f"[아이러브밤] 로그인 폼 감지: {selector_pair[0]}",
                        "DEBUG",
                    )
                    break
            except Exception:
                continue

        if not uid_el or not pw_el:
            # 전용 로그인 페이지로 이동 시도
            self.emit("[아이러브밤] 홈에서 폼을 못 찾음. 로그인 페이지 이동 시도...", "DEBUG")
            try:
                self.driver.get(f"{self.base_url}/index.php?mid=index&act=dispMemberLoginForm")
                time.sleep(1.5)
                self.require_human_check()
                uid_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='user_id']")
                pw_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
            except Exception as exc:
                return LoginResult(
                    success=False, method="form",
                    message=f"로그인 폼을 찾을 수 없습니다: {exc}", account=_acct,
                )

        try:
            # 포커스 맞추고 값 입력 (일부 input에 send_keys가 무시되는 경우가 있어 JS 보조)
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", uid_el)
            try:
                uid_el.click()
            except Exception:
                pass
            uid_el.clear()
            uid_el.send_keys(self.username)

            try:
                pw_el.click()
            except Exception:
                pass
            pw_el.clear()
            pw_el.send_keys(self.password)

            # 값 확인 후 JS로 보강 (빈 경우 대비)
            try:
                cur_uid = uid_el.get_attribute("value") or ""
                cur_pw = pw_el.get_attribute("value") or ""
                if not cur_uid:
                    self.driver.execute_script(
                        "arguments[0].value = arguments[1];", uid_el, self.username
                    )
                if not cur_pw:
                    self.driver.execute_script(
                        "arguments[0].value = arguments[1];", pw_el, self.password
                    )
            except Exception:
                pass

            time.sleep(0.5)
            self.emit("[아이러브밤] 로그인 폼 제출...", "DEBUG")

            # 제출 — 부모 폼의 submit() 호출이 가장 안정적 (CSRF 토큰 포함)
            submitted = False
            try:
                self.driver.execute_script(
                    "arguments[0].closest('form').submit();", pw_el
                )
                submitted = True
            except Exception as exc:
                self.emit(f"[아이러브밤] form.submit() 실패: {exc}", "DEBUG")

            if not submitted:
                # fallback: submit 버튼 찾아 클릭
                for sel in [
                    "#sun_login button[type='submit']",
                    "#fo_login_widget input[type='submit']",
                    "input[type='submit'].login",
                    "button[type='submit']",
                ]:
                    try:
                        btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                        btn.click()
                        submitted = True
                        break
                    except Exception:
                        continue

            if not submitted:
                return LoginResult(
                    success=False, method="form",
                    message="로그인 제출 버튼을 찾을 수 없습니다.", account=_acct,
                )

            time.sleep(2.5)
            # alert 있으면 처리 (잘못된 비밀번호 등)
            try:
                WebDriverWait(self.driver, 1.5).until(EC.alert_is_present())
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                alert.accept()
                self.emit(f"[아이러브밤] 로그인 alert: {alert_text}", "WARNING")
                return LoginResult(
                    success=False, method="form",
                    message=f"로그인 실패: {alert_text}", account=_acct,
                )
            except Exception:
                pass

        except Exception as exc:
            return LoginResult(
                success=False, method="form",
                message=f"로그인 제출 중 오류: {exc}", account=_acct,
            )

        # 제출 후 로그인 상태 재확인 (DOM 기반)
        time.sleep(1.0)
        if self._is_logged_in():
            self.emit("[아이러브밤] 로그인 성공.", "INFO")
            return LoginResult(
                success=True, method="form",
                message="로그인 성공", account=_acct,
            )

        # 한 번 더 대기 후 재확인
        time.sleep(1.5)
        if self._is_logged_in():
            self.emit("[아이러브밤] 로그인 성공 (지연).", "INFO")
            return LoginResult(
                success=True, method="form",
                message="로그인 성공", account=_acct,
            )

        return LoginResult(
            success=False, method="form",
            message="로그인 실패: 제출 후에도 로그인 상태가 아닙니다 (ID/PW 또는 캡차 확인)",
            account=_acct,
        )

    # ── Jump ──

    def jump(self) -> JumpResult:
        """기본 jump: 등록된 게시물이 없음을 알림 (handler가 post_urls로 호출)."""
        return JumpResult(
            status=STATUS_FAILED,
            message="아이러브밤은 게시물 URL을 등록해야 합니다. 작업 설정에서 URL을 추가해주세요.",
        )

    def jump_posts(self, post_urls: Iterable[str]) -> list[tuple[str, JumpResult]]:
        """여러 게시물에 대해 순차적으로 점프 수행."""
        results: list[tuple[str, JumpResult]] = []
        for url in post_urls:
            url = (url or "").strip()
            if not url:
                continue
            result = self.jump_single_post(url)
            results.append((url, result))
            if result.status == STATUS_COOLDOWN:
                self.emit(
                    "[아이러브밤] 점프 횟수 초과 감지 — 남은 게시물은 건너뜁니다.",
                    "WARNING",
                )
                break
        return results

    def _extract_document_id(self, post_url: str) -> str | None:
        """URL에서 document_srl(게시물 ID)을 추출.

        지원 패턴:
          - /index.php?document_srl=12345
          - /{mid}/12345 (XE rewrite rule)
          - /12345 (trailing number)
        """
        # 1) document_srl 쿼리/경로
        m = re.search(r"document_srl[=/](\d+)", post_url)
        if m:
            return m.group(1)
        # 2) /store/12345, /free/12345 등 rewrite URL (경로 말미 숫자)
        m = re.search(r"/(\d{5,})(?:[/?#]|$)", post_url)
        if m:
            return m.group(1)
        return None

    def jump_single_post(self, post_url: str) -> JumpResult:
        """하나의 게시물 URL에 대한 점프 실행."""
        self.emit(f"[아이러브밤] 점프: {post_url}", "INFO")

        # 1. URL에서 document_srl 우선 추출 (버튼 탐색 실패 대비)
        doc_id = self._extract_document_id(post_url)

        try:
            self.driver.get(post_url)
            time.sleep(1.5)
            self.require_human_check()
        except Exception as exc:
            return JumpResult(
                status=STATUS_FAILED,
                message=f"게시물 접속 실패: {exc}",
            )

        # 로그인 체크 (DOM 기반)
        if not self._is_logged_in():
            return JumpResult(
                status=STATUS_LOGIN_REQUIRED,
                message="로그인 상태가 아닙니다. 게시물 접속 시 세션이 만료되었을 수 있습니다.",
            )

        # 2. 페이지 내 점프 버튼 탐색 (horizontal 상태 확인용)
        button_onclick: str = ""
        button_found = False
        try:
            buttons = self.driver.find_elements(By.CSS_SELECTOR, "a[onclick]")
            for btn in buttons:
                oc = btn.get_attribute("onclick") or ""
                if "procSejin7940_jumpDocumentJump" in oc:
                    button_onclick = oc
                    button_found = True
                    m = re.search(
                        r"procSejin7940_jumpDocumentJump['\"]?\s*,\s*(\d+)", oc
                    )
                    if m:
                        # 버튼에서 추출한 doc_id를 우선 (더 정확)
                        doc_id = m.group(1)
                    break
                if (
                    "상단점프" in (btn.text or "") or "글 위로 올리기" in (btn.text or "")
                ) and "alert(" in oc:
                    button_onclick = oc
                    button_found = True
                    break
        except Exception:
            pass

        # 3. 횟수 초과 버튼 상태
        if button_onclick and "초과" in button_onclick:
            m = re.search(r"alert\(['\"]([^'\"]+)['\"]\)", button_onclick)
            msg = m.group(1) if m else "오늘 상단점프 가능 횟수를 초과했습니다"
            return JumpResult(status=STATUS_COOLDOWN, message=msg)

        if not doc_id:
            return JumpResult(
                status=STATUS_FAILED,
                message="게시물 ID를 URL에서 추출할 수 없습니다. URL 형식을 확인하세요.",
            )

        # 버튼이 안 보이지만 URL에서 doc_id를 뽑았으면 그대로 JS 호출 시도
        if not button_found:
            self.emit(
                f"[아이러브밤] 점프 버튼 미발견 — URL doc_id={doc_id}로 직접 시도",
                "DEBUG",
            )

        # JS로 점프 실행
        try:
            self.driver.execute_script(
                "doCallModuleAction('sejin7940_jump', "
                "'procSejin7940_jumpDocumentJump', arguments[0]);",
                int(doc_id),
            )
        except Exception as exc:
            self.emit(f"[아이러브밤] doCallModuleAction 호출 후 상태: {exc}", "DEBUG")

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
