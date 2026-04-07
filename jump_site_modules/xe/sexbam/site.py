"""SexbamSite - 섹밤 (XpressEngine, Cloudflare Turnstile)."""

from __future__ import annotations

import re
import time

from ...base import (
    STATUS_COOLDOWN,
    STATUS_FAILED,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    BaseSite,
)
from ...types import JumpResult, LoginResult


class SexbamSite(BaseSite):
    SITE_NAME = "섹밤"

    def _solve_turnstile(self) -> bool:
        """Solve Turnstile captcha via 2Captcha and inject token."""
        if not self._captcha_api_key:
            self.emit("2Captcha API 키가 없어 Turnstile을 해결할 수 없습니다.", "ERROR")
            return False

        # Extract sitekey
        try:
            sitekey = self.driver.execute_script(
                "var el = document.querySelector('[data-sitekey]');"
                "return el ? el.getAttribute('data-sitekey') : null;"
            )
        except Exception:
            sitekey = None

        if not sitekey:
            self.emit("[섹밤] Turnstile sitekey를 찾을 수 없습니다.", "WARN")
            return False

        self.emit(f"Turnstile sitekey 감지: {sitekey[:20]}... 2Captcha에 풀이 요청 중...", "INFO")

        try:
            from twocaptcha import TwoCaptcha
        except ImportError:
            self.emit("[섹밤] twocaptcha 패키지 없음 - Turnstile 미해결", "WARN")
            return False

        try:
            solver = TwoCaptcha(self._captcha_api_key)
            result = solver.turnstile(
                sitekey=sitekey,
                url=self.driver.current_url,
            )
            token = result.get("code") if isinstance(result, dict) else result
        except Exception as exc:
            self.emit(f"[섹밤] Turnstile 풀이 실패: {exc}", "WARN")
            return False

        if not token:
            self.emit("[섹밤] Turnstile 토큰을 받지 못했습니다.", "WARN")
            return False

        self.emit("Turnstile 토큰 수신 완료. 폼에 주입합니다.", "INFO")

        self.driver.execute_script(
            """
            var token = arguments[0];
            var cf = document.querySelector('input[name="cf-turnstile-response"]');
            var g = document.querySelector('input[name="g-recaptcha-response"]');
            if (cf) cf.value = token;
            if (g) g.value = token;
            """,
            token,
        )
        return True

    def login(self) -> LoginResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        self.emit(f"[섹밤] 시작: {self.base_url} (ID: {self.username})", "INFO")

        _acct = {"mb_id": self.username, "mb_password": self.password}

        login_url = f"{self.base_url}/index.php?mid=main_04&act=dispMemberLoginForm"
        self.driver.get(login_url)
        time.sleep(1.0)
        self.require_human_check()

        # Check if login form exists
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#uid"))
            )
        except Exception:
            if self.page_contains("로그아웃"):
                self.emit("[섹밤] 이미 로그인된 상태입니다.", "INFO")
                return LoginResult(success=True, method="already", message="이미 로그인 상태", account=_acct)
            return LoginResult(success=False, method="form", message="로그인 폼을 찾을 수 없습니다.", account=_acct)

        # Fill credentials
        uid_el = self.driver.find_element(By.CSS_SELECTOR, "#uid")
        pw_el = self.driver.find_element(By.CSS_SELECTOR, "#upw")
        uid_el.clear()
        uid_el.send_keys(self.username)
        pw_el.clear()
        pw_el.send_keys(self.password)

        # Turnstile captcha
        has_turnstile = self.driver.execute_script(
            "return !!document.querySelector('[data-sitekey]');"
        )
        if has_turnstile:
            if not self._solve_turnstile():
                self.emit("[섹밤] Turnstile 미해결 - 로그인 시도 계속", "WARN")

        # Submit
        try:
            self.driver.find_element(By.CSS_SELECTOR, "input.submit.btn").click()
        except Exception:
            self.driver.execute_script(
                "document.querySelector('#fo_member_login').submit();"
            )

        time.sleep(1.5)

        if not self.page_contains("로그아웃"):
            src = ""
            try:
                src = self.driver.page_source or ""
            except Exception:
                pass
            if "로봇" in src or "리캡차" in src:
                return LoginResult(
                    success=False,
                    method="form",
                    message="Turnstile 인증 실패 - 로봇 확인 메시지",
                    account=_acct,
                )
            return LoginResult(
                success=False,
                method="form",
                message="로그인 실패 (ID/PW 확인 필요)",
                account=_acct,
            )

        self.emit("[섹밤] 로그인 성공.", "INFO")
        return LoginResult(success=True, method="form", message="로그인 성공", account=_acct)

    def jump(self) -> JumpResult:
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # Get document_srl from own documents
        own_doc_url = f"{self.base_url}/index.php?act=dispMemberOwnDocument&mid=sch"
        self.driver.get(own_doc_url)
        time.sleep(1.0)
        self.require_human_check()

        document_srl = None
        try:
            document_srl = self.driver.execute_script("""
                var rows = document.querySelectorAll('table tbody tr');
                var candidates = [];
                for (var i = 0; i < rows.length; i++) {
                    var tds = rows[i].querySelectorAll('td');
                    if (!tds.length) continue;
                    var numText = tds[0].textContent.trim();
                    var num = parseInt(numText, 10);
                    if (isNaN(num)) continue;
                    var link = rows[i].querySelector('td.title a[href]');
                    if (!link) continue;
                    var href = link.href || '';
                    var m = href.match(/\\/([0-9]+)/);
                    var srl = m ? m[1] : null;
                    if (srl) candidates.push({num: num, srl: srl});
                }
                if (!candidates.length) return null;
                candidates.sort(function(a, b) { return a.num - b.num; });
                return candidates[0].srl;
            """)
        except Exception:
            pass

        if not document_srl:
            return JumpResult(
                status=STATUS_FAILED,
                message="내 작성글에서 게시물을 찾을 수 없습니다. 출근부 게시글 등록 여부를 확인하세요.",
            )

        self.emit(f"[섹밤] document_srl 확인: {document_srl}", "INFO")

        # Navigate to post detail
        post_url = f"{self.base_url}/sch/{document_srl}"
        self.driver.get(post_url)
        time.sleep(1.0)
        self.require_human_check()

        # Find jump button
        try:
            jump_info = self.driver.execute_script("""
                var btn = document.querySelector('a[onclick*="procDocument_jumpDocumentUp"]');
                if (!btn) return null;
                var text = btn.textContent.trim();
                var m = btn.getAttribute('onclick').match(/(\\d+)\\s*\\)/);
                var srl = m ? m[1] : null;
                return { text: text, srl: srl };
            """)
        except Exception:
            jump_info = None

        if not jump_info:
            return JumpResult(
                status=STATUS_FAILED,
                message="상단점프 버튼을 찾을 수 없습니다. (제휴 등급 확인 필요)",
            )

        before_text = jump_info.get("text", "")
        self.emit(f"[섹밤] 점프 버튼 발견: {before_text}", "INFO")

        # Parse remaining count
        count_match = re.search(r"\((\d+)\)", before_text)
        before_count = int(count_match.group(1)) if count_match else -1

        if before_count == 0:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="오늘 점프 횟수를 모두 사용했습니다.",
                remaining_count=0,
            )

        # Execute jump
        target_srl = jump_info.get("srl") or document_srl
        try:
            self.driver.execute_script(
                "doCallModuleAction('document_jump', 'procDocument_jumpDocumentUp', arguments[0]);",
                int(target_srl),
            )
        except Exception as exc:
            self.emit(f"[섹밤] 점프 호출 후 리로드 감지 (정상): {exc}", "DEBUG")

        # Check result via alert
        alert_msg = ""
        try:
            WebDriverWait(self.driver, 5).until(EC.alert_is_present())
            alert_obj = self.driver.switch_to.alert
            alert_msg = alert_obj.text or ""
            alert_obj.accept()
            self.emit(f"[섹밤] 점프 결과 alert: {alert_msg}", "DEBUG")
        except Exception:
            pass

        if "올렸습니다" in alert_msg or "완료" in alert_msg or "성공" in alert_msg:
            return JumpResult(status=STATUS_SUCCESS, message="상단점프 완료")

        if "횟수" in alert_msg and (
            "없" in alert_msg or "초과" in alert_msg or "소진" in alert_msg
        ):
            return JumpResult(status=STATUS_COOLDOWN, message=alert_msg)

        if "분" in alert_msg and ("대기" in alert_msg or "한번" in alert_msg):
            return JumpResult(status=STATUS_COOLDOWN, message=alert_msg)

        if alert_msg:
            return JumpResult(status=STATUS_FAILED, message=f"점프 결과: {alert_msg}")

        # No alert - compare button count
        time.sleep(1.0)
        try:
            after_text = self.driver.execute_script("""
                var btn = document.querySelector('a[onclick*="procDocument_jumpDocumentUp"]');
                return btn ? btn.textContent.trim() : '';
            """)
        except Exception:
            after_text = ""

        after_match = re.search(r"\((\d+)\)", after_text)
        after_count = int(after_match.group(1)) if after_match else -1

        if before_count > 0 and after_count >= 0 and after_count < before_count:
            return JumpResult(
                status=STATUS_SUCCESS,
                message=f"상단점프 완료 (남은 횟수: {after_count})",
                remaining_count=after_count,
            )

        if after_count == 0:
            return JumpResult(
                status=STATUS_COOLDOWN,
                message="점프 횟수 소진",
                remaining_count=0,
            )

        if after_text and "점프" in after_text:
            return JumpResult(
                status=STATUS_SUCCESS,
                message=f"상단점프 실행됨 ({after_text})",
            )

        return JumpResult(
            status=STATUS_FAILED,
            message=f"점프 결과 확인 실패 (before={before_text}, after={after_text})",
        )
