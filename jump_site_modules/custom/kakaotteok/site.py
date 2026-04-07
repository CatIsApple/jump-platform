"""KakaotteokSite - 카카오떡."""

from __future__ import annotations

import random
import re
import time
from typing import Any

from ...base import (
    STATUS_FAILED,
    STATUS_INSUFFICIENT,
    STATUS_LOGIN_REQUIRED,
    STATUS_SUCCESS,
    BaseSite,
)
from ...types import (
    Board,
    Comment,
    JumpResult,
    LoginResult,
    Post,
    WriteResult,
)


class KakaotteokSite(BaseSite):
    SITE_NAME = "카카오떡"
    COOKIE_KEYS = ["PHPSESSID"]

    def login(self) -> LoginResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        _acct = {"mb_id": self.username, "mb_password": self.password}

        self.naver_warmup(sleep_s=0.5)
        self.goto(self.base_url)
        self.require_human_check()

        # Cookie injection
        for k in self.COOKIE_KEYS:
            try:
                self.driver.delete_cookie(k)
            except Exception:
                pass
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

        # Check login
        if self.wait_for_text("로그아웃", timeout=2.0):
            return LoginResult(success=True, method="cookie", message="쿠키 로그인 성공", account=_acct)

        # Form login at /signin
        for k in self.COOKIE_KEYS:
            try:
                self.driver.delete_cookie(k)
            except Exception:
                pass
        try:
            self.driver.refresh()
        except Exception:
            pass

        self.goto("/signin")
        time.sleep(0.5)
        self.require_human_check()

        try:
            # eyoom 로그인: #mb_id / #mb_password
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#mb_id, input[name='mb_id']")
                )
            )

            # ID 입력
            id_el = self.driver.find_element(By.CSS_SELECTOR, "#mb_id, input[name='mb_id']")
            id_el.clear()
            id_el.send_keys(self.username)

            # PW 입력
            pw_el = self.driver.find_element(By.CSS_SELECTOR, "#mb_password, input[name='mb_password']")
            pw_el.clear()
            pw_el.send_keys(self.password)

            # 제출
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, "form[name='flogin'] button[type='submit']")
            except Exception:
                btn = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            btn.click()
        except Exception:
            return LoginResult(success=False, method="form", message="로그인 폼 입력/제출 실패", account=_acct)

        time.sleep(1.0)
        self.require_human_check()

        # alert 처리 (로그인 실패 시 alert 뜰 수 있음)
        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text:
            self.emit(f"[{self.SITE_NAME}] 로그인 alert: {alert_text}", "INFO")

        if not self.wait_for_text("로그아웃", timeout=4.0):
            # page_source에서도 확인
            if not self.page_contains("로그아웃"):
                return LoginResult(
                    success=False,
                    method="form",
                    message=f"로그인 실패(로그아웃 표시 없음) {alert_text}".strip(),
                    account=_acct,
                )

        self.save_cookies(self.COOKIE_KEYS)
        return LoginResult(success=True, method="form", message="로그인 성공", account=_acct)

    def jump(self) -> JumpResult:
        # Navigate to jump page
        self.goto("/mypage/myjump")
        time.sleep(1.0)
        self.require_human_check()

        # AJAX jump
        try:
            result_json = self.driver.execute_script("""
                return (async () => {
                    const r = await fetch('/ajaxinc', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: 'inc=dldjqthfmfwjqgmgkdufk&type=jump&jump_type=수동'
                    });
                    return await r.json();
                })();
            """)
        except Exception as exc:
            return JumpResult(status=STATUS_FAILED, message=f"점프 AJAX 요청 실패: {exc}")

        self.save_cookies(self.COOKIE_KEYS)

        if not result_json or not isinstance(result_json, dict):
            return JumpResult(status=STATUS_FAILED, message="점프 응답 파싱 실패")

        state = result_json.get("state", "")
        cnt = result_json.get("cnt", "")

        if state == "ok":
            return JumpResult(
                status=STATUS_SUCCESS,
                message=f"점프 완료 (잔여: {cnt})",
                remaining_count=int(cnt) if str(cnt).isdigit() else -1,
            )
        if state == "nonum":
            return JumpResult(status=STATUS_INSUFFICIENT, message="점프 잔여 수량 없음")
        if state == "jumpdateover":
            return JumpResult(status=STATUS_INSUFFICIENT, message="프리미엄 회원 만기")
        if state == "nolog":
            return JumpResult(status=STATUS_LOGIN_REQUIRED, message="로그인 필요")
        if state == "lackpoint":
            return JumpResult(status=STATUS_INSUFFICIENT, message="포인트 부족")
        if state == "nobiz":
            return JumpResult(status=STATUS_FAILED, message="업소회원이 아닙니다")

        return JumpResult(status=STATUS_FAILED, message=f"점프 실패 (state={state})")

    # ──────────────────────────────────────────────
    #  0. 프로필
    # ──────────────────────────────────────────────

    def get_profile(self):
        """로그인된 계정의 프로필 정보 가져오기."""
        from . import parsers

        # eyoom 마이페이지: /mypage/
        self.goto(f"{self.base_url}/mypage/")
        time.sleep(0.5)
        self.require_human_check()
        profile = parsers.parse_profile(self.driver.page_source)
        if profile.nickname:
            return profile

        # fallback: 메인 페이지 사이드바
        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()
        return parsers.parse_profile(self.driver.page_source)

    # ──────────────────────────────────────────────
    #  1. 회원가입
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """카카오떡 회원가입 (/bbs/register_form.php)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        mb_id = kwargs.get("mb_id", "")
        mb_password = kwargs.get("mb_password", "")
        mb_nick = kwargs.get("mb_nick", "")
        mb_name = kwargs.get("mb_name", mb_nick)
        acct = {"mb_id": mb_id, "mb_password": mb_password, "mb_name": mb_name, "mb_nick": mb_nick}

        if not all([mb_id, mb_password, mb_nick]):
            return LoginResult(
                success=False, method="register",
                message="필수 항목 누락 (mb_id, mb_password, mb_nick)",
                account=acct,
            )

        # ── 약관 동의 페이지 (/bbs/register.php) ──
        self.goto("/bbs/register.php")
        time.sleep(0.5)
        self.require_human_check()

        # 약관 동의: hidden input(이미 value=1) + 일반회원 버튼 클릭
        try:
            agree_btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "button[name='member_type'][value='일반회원'], "
                "button[name='member_type']"
            )
            agree_btn.click()
            time.sleep(1.0)
            self.require_human_check()
        except Exception:
            # 약관 페이지가 아닌 경우 (이미 폼 페이지일 수 있음) - 계속 진행
            pass

        # ── 회원가입 폼 대기 ──
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#reg_mb_id"))
            )
        except Exception:
            return LoginResult(success=False, method="register", message="회원가입 폼 로드 실패", account=acct)

        # ── 필드 입력 (JS) ──
        self.driver.execute_script("""
            var f = document.querySelector('form[name="fregisterform"]') || document.forms[0];
            if (!f) return;
            var set = function(sel, val) {
                var el = f.querySelector(sel);
                if (el) { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }
            };
            set('#reg_mb_id, [name="mb_id"]', arguments[0]);
            set('#reg_mb_password, [name="mb_password"]', arguments[1]);
            set('#reg_mb_password_re, [name="mb_password_re"]', arguments[1]);
            set('#reg_mb_nick, [name="mb_nick"]', arguments[2]);
        """, mb_id, mb_password, mb_nick)
        time.sleep(0.3)

        # 아이디 중복체크: JS로 check_duplication 호출 → Swal 확인
        try:
            self.driver.execute_script("check_duplication('mb_id');")
            time.sleep(0.5)
            # Swal 팝업 닫기 (성공/실패 모두)
            self.handle_swal(click_confirm=True)
            time.sleep(0.3)
        except Exception:
            pass
        # 강제로 'y' 설정 (check_duplication이 설정하지 못한 경우 대비)
        try:
            self.driver.execute_script(
                "var el=document.getElementById('mb_id_duplicated'); if(el) el.value='y';"
            )
        except Exception:
            pass

        # 닉네임 중복체크
        try:
            self.driver.execute_script("check_duplication('mb_nick');")
            time.sleep(0.5)
            self.handle_swal(click_confirm=True)
            time.sleep(0.3)
        except Exception:
            pass
        try:
            self.driver.execute_script(
                "var el=document.getElementById('mb_nick_duplicated'); if(el) el.value='y';"
            )
        except Exception:
            pass

        # mb_name 필드 (서버 필수 - 폼에 없으면 hidden으로 추가)
        mb_name = kwargs.get("mb_name", kwargs.get("mb_nick", mb_nick))
        try:
            name_el = self.driver.find_element(By.CSS_SELECTOR, "#reg_mb_name, input[name='mb_name']")
            name_el.clear()
            name_el.send_keys(mb_name)
        except Exception:
            # 폼에 mb_name 필드가 없으면 hidden input 추가
            try:
                self.driver.execute_script("""
                    var f = document.querySelector('form[name="fregisterform"]');
                    if (f && !f.querySelector('[name="mb_name"]')) {
                        var inp = document.createElement('input');
                        inp.type = 'hidden'; inp.name = 'mb_name'; inp.value = arguments[0];
                        f.appendChild(inp);
                    }
                """, mb_name)
            except Exception:
                pass

        # 관심 업종/지역 선택 (eyoom 전용)
        try:
            from selenium.webdriver.support.ui import Select
            area1 = self.driver.find_element(By.CSS_SELECTOR, "select[name='ca_area_name1']")
            sel1 = Select(area1)
            opts1 = [o.get_attribute("value") for o in sel1.options if o.get_attribute("value")]
            if opts1:
                sel1.select_by_value(random.choice(opts1))
        except Exception:
            pass
        try:
            from selenium.webdriver.support.ui import Select
            area2 = self.driver.find_element(By.CSS_SELECTOR, "select[name='ca_area_name2']")
            sel2 = Select(area2)
            opts2 = [o.get_attribute("value") for o in sel2.options if o.get_attribute("value")]
            if opts2:
                sel2.select_by_value(random.choice(opts2))
        except Exception:
            pass

        # 지역 선택 (표준 gnuboard - 있으면)
        try:
            from selenium.webdriver.support.ui import Select
            area_el = self.driver.find_element(
                By.CSS_SELECTOR, "select[name='mb_area'], select[name='mb_2']"
            )
            select = Select(area_el)
            options = [o.get_attribute("value") for o in select.options if o.get_attribute("value")]
            if options:
                select.select_by_value(random.choice(options))
        except Exception:
            pass

        time.sleep(0.3)

        # kcaptcha 자동 해결 (있으면)
        if not self._solve_kcaptcha():
            return LoginResult(
                success=False, method="register",
                message="캡차 미입력 (수동 입력 필요)",
                account=acct,
            )

        # ── 제출 ──
        if not self._gnuboard_submit_form():
            return LoginResult(success=False, method="register", message="회원가입 제출 실패", account=acct)

        time.sleep(1.0)
        self.require_human_check()

        return self._check_register_result(acct)

    # ──────────────────────────────────────────────
    #  3. 게시판 목록
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """카카오떡 게시판 목록 가져오기.

        eyoom 사이트: 로그인 후 네비게이션에서 실제 URL 추출.
        비로그인 시 fun_nologin()으로 차단됨.
        """
        from . import parsers

        self.goto(self.base_url)
        time.sleep(0.5)
        self.require_human_check()

        # 표준 파서 시도 (bo_table 링크)
        boards = parsers.parse_boards(self.driver.page_source)
        if boards:
            return boards

        # eyoom 네비게이션에서 JS로 실제 링크 추출 (로그인 후에만 동작)
        try:
            nav_data = self.driver.execute_script("""
                var results = [];
                var seen = {};
                var links = document.querySelectorAll('.navbar-nav a, .sidebar-left a, nav a');
                for (var i = 0; i < links.length; i++) {
                    var a = links[i];
                    var href = a.getAttribute('href') || '';
                    var onclick = a.getAttribute('onclick') || '';
                    if (onclick.indexOf('fun_nologin') >= 0) continue;
                    if (href.indexOf('javascript:') >= 0) continue;
                    if (!href || href === '#' || href === '/') continue;
                    var text = a.textContent.trim().replace(/\\s+/g, ' ');
                    if (!text || text.length > 30) continue;
                    // bo_table 패턴
                    var m = href.match(/bo_table=([\\w]+)/);
                    if (m && !seen[m[1]]) { seen[m[1]] = 1; results.push({id: m[1], name: text}); continue; }
                    // /board/XXX 패턴
                    m = href.match(/\\/board\\/([\\w]+)/);
                    if (m && !seen[m[1]]) { seen[m[1]] = 1; results.push({id: m[1], name: text}); continue; }
                    // eyoom 커스텀 라우팅: /XXX 패턴 (짧은 경로)
                    m = href.match(/^\\/([a-z_][a-z0-9_]{1,20})$/i);
                    if (!m) m = href.match(/https?:\\/\\/[^\\/]+\\/([a-z_][a-z0-9_]{1,20})$/i);
                    if (m) {
                        var bid = m[1];
                        var skip = ['signin','register','bizpage','timesale','logout','mypage','login','bbs'];
                        if (skip.indexOf(bid) >= 0) continue;
                        if (!seen[bid]) { seen[bid] = 1; results.push({id: bid, name: text, url: href}); }
                    }
                }
                return results;
            """)
            if nav_data:
                for item in nav_data:
                    boards.append(Board(
                        id=item.get("id", ""),
                        name=item.get("name", ""),
                    ))
        except Exception:
            pass

        return boards

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

        eyoom 라우팅: /{board_id} (URL 파라미터 무시되는 경우 있음)
        page > 1 또는 검색 시 gnuboard 표준 URL 사용.
        """
        from . import parsers

        if (page > 1) or (search_field and search_text):
            # gnuboard 표준 URL 사용 (eyoom 단축 URL은 파라미터 무시함)
            url = f"{self.base_url}/bbs/board.php?bo_table={board_id}&page={page}"
            if search_field and search_text:
                url += f"&sfl={search_field}&stx={search_text}&sop=and"
            if sort_field:
                url += f"&sst={sort_field}"
            if sort_order:
                url += f"&sod={sort_order}"
            self.driver.get(url)
            time.sleep(0.7)
            self.require_human_check()
            alert_text = self.handle_alert(accept=True, timeout=1.0)
            if alert_text and "로그인" in alert_text:
                self.emit(f"[{self.SITE_NAME}] 게시판 접근 alert: {alert_text}", "WARNING")

            posts = parsers.parse_posts(self.driver.page_source, board_id=board_id)
            if posts:
                return posts

            # gnuboard URL 실패 시 eyoom URL + JS 폴백
            self.driver.get(f"{self.base_url}/{board_id}")
            time.sleep(0.7)
            self.require_human_check()
            self.handle_alert(accept=True, timeout=1.0)

            if page > 1:
                self.driver.execute_script("""
                    var page = arguments[0];
                    var links = document.querySelectorAll('.pg_wrap a, .pagination a, .paging a, .pg a, a.pg_page');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].textContent.trim() === String(page)) { links[i].click(); return; }
                    }
                    var allLinks = document.querySelectorAll('a[href*="page="]');
                    for (var i = 0; i < allLinks.length; i++) {
                        var m = allLinks[i].href.match(/[?&]page=(\\d+)/);
                        if (m && parseInt(m[1]) === page) { allLinks[i].click(); return; }
                    }
                """, page)
                time.sleep(0.7)
                self.require_human_check()

            if search_field and search_text:
                self.driver.execute_script("""
                    var sfl = arguments[0], stx = arguments[1];
                    var form = document.querySelector('form[name="fsearch"]');
                    if (!form) { var sel = document.querySelector('select[name="sfl"]'); if (sel) form = sel.closest('form'); }
                    if (!form) { var inp = document.querySelector('input[name="stx"]'); if (inp) form = inp.closest('form'); }
                    if (form) {
                        var sflEl = form.querySelector('select[name="sfl"]');
                        var stxEl = form.querySelector('input[name="stx"]');
                        if (sflEl) sflEl.value = sfl;
                        if (stxEl) stxEl.value = stx;
                        form.submit();
                    }
                """, search_field, search_text)
                time.sleep(0.7)
                self.require_human_check()
        else:
            # page 1, 검색 없음: eyoom 단축 URL 사용
            url = f"{self.base_url}/{board_id}"
            self.driver.get(url)
            time.sleep(0.7)
            self.require_human_check()
            alert_text = self.handle_alert(accept=True, timeout=1.0)
            if alert_text and "로그인" in alert_text:
                self.emit(f"[{self.SITE_NAME}] 게시판 접근 alert: {alert_text}", "WARNING")

        return parsers.parse_posts(self.driver.page_source, board_id=board_id)

    # ──────────────────────────────────────────────
    #  5. 댓글 가져오기
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, *, board_id: str = "") -> list[Comment]:
        """특정 게시글 댓글 가져오기."""
        from . import parsers

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        # eyoom: /{board_id}/{post_id}
        url = f"{self.base_url}/{board_id}/{post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()
        self.handle_alert(accept=True, timeout=1.0)

        comments = parsers.parse_comments(self.driver.page_source, post_id=post_id)
        if comments:
            return comments

        # gnuboard URL 시도 (eyoom URL 실패 시)
        url2 = f"{self.base_url}/bbs/board.php?bo_table={board_id}&wr_id={post_id}"
        self.driver.get(url2)
        time.sleep(0.7)
        self.require_human_check()
        self.handle_alert(accept=True, timeout=1.0)

        comments = parsers.parse_comments(self.driver.page_source, post_id=post_id)
        if comments:
            return comments

        # JS 기반 댓글 추출 fallback
        try:
            js_comments = self.driver.execute_script("""
                var results = [];
                // eyoom: div#c_NNNNN.view-comment-item
                var items = document.querySelectorAll('[id^="c_"], .view-comment-item, .comment-item');
                for (var i = 0; i < items.length; i++) {
                    var el = items[i];
                    var id = (el.id || '').replace(/^c_/, '') || String(i);
                    var authorEl = el.querySelector('.comment-name a, .member, .sv_member, b.name, .cmt_name');
                    var author = authorEl ? authorEl.textContent.trim() : '';
                    var contentEl = el.querySelector('.comment-cont-txt, .cmt_contents, .cmt_textbox, .comment-text');
                    var content = contentEl ? contentEl.textContent.trim() : '';
                    var dateEl = el.querySelector('.comment-time, .cmt_date, time, .datetime');
                    var date = dateEl ? dateEl.textContent.trim() : '';
                    if (author || content) {
                        results.push({id: id, author: author, content: content, date: date});
                    }
                }
                return results;
            """)
            if js_comments:
                for jc in js_comments:
                    comments.append(Comment(
                        id=str(jc.get("id", "")),
                        post_id=post_id,
                        author=jc.get("author", ""),
                        content=jc.get("content", ""),
                        date=jc.get("date", ""),
                    ))
        except Exception:
            pass

        return comments

    # ──────────────────────────────────────────────
    #  6. 게시글 작성
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성 (eyoom gnuboard).

        글쓰기 URL: /board/write?bo_table={board_id}
        에디터: SmartEditor2
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # eyoom: /board/write?bo_table={board_id}
        url = f"{self.base_url}/board/write?bo_table={board_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()

        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text and ("로그인" in alert_text or "권한" in alert_text):
            return WriteResult(success=False, message=f"로그인/권한 필요: {alert_text}")

        if "login" in self.driver.current_url or "signin" in self.driver.current_url:
            return WriteResult(success=False, message="로그인이 필요합니다.")

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='wr_subject']"))
            )
        except Exception:
            return WriteResult(success=False, message="게시글 작성 폼 로드 실패")

        # 팝업 배너 닫기
        try:
            self.driver.execute_script("""
                document.querySelectorAll('.popup_bg, .popup-layer, .modal-backdrop, .layer-popup').forEach(e => e.remove());
                document.querySelectorAll('[class*="popup"][class*="close"], [class*="banner"] .close').forEach(e => e.click());
            """)
        except Exception:
            pass

        subj_el = self.driver.find_element(By.CSS_SELECTOR, "input[name='wr_subject']")
        subj_el.clear()
        subj_el.send_keys(subject)

        # SmartEditor2 → CKEditor → textarea fallback
        content_set = False
        try:
            content_set = self.driver.execute_script("""
                if (typeof oEditors !== 'undefined') {
                    oEditors.getById['wr_content'].exec('SET_IR', [arguments[0]]);
                    return true;
                }
                return false;
            """, content)
        except Exception:
            content_set = False

        if not content_set:
            try:
                content_set = self.driver.execute_script("""
                    if (typeof CKEDITOR !== 'undefined') {
                        for (var name in CKEDITOR.instances) {
                            CKEDITOR.instances[name].setData(arguments[0]);
                            return true;
                        }
                    }
                    return false;
                """, content)
            except Exception:
                content_set = False

        if not content_set:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea[name='wr_content'], #wr_content")
                self.driver.execute_script("arguments[0].style.display='block';", ta)
                ta.clear()
                ta.send_keys(content)
                content_set = True
            except Exception:
                pass

        if not content_set:
            return WriteResult(success=False, message="내용 입력 영역을 찾을 수 없습니다.")

        time.sleep(0.3)

        # SmartEditor2: 제출 전 UPDATE_CONTENTS_FIELD 호출
        try:
            self.driver.execute_script("""
                if (typeof oEditors !== 'undefined') {
                    oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD', []);
                }
            """)
        except Exception:
            pass

        # 제출
        try:
            self.driver.execute_script("""
                var btn = document.querySelector('#btn_submit, button[type="submit"], input[type="submit"]');
                if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); }
            """)
        except Exception:
            try:
                self.driver.execute_script("document.getElementById('fwrite').submit();")
            except Exception:
                return WriteResult(success=False, message="게시글 제출 버튼 클릭 실패")

        time.sleep(1.5)
        self.require_human_check()

        # alert 처리
        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text and ("오류" in alert_text or "실패" in alert_text):
            return WriteResult(success=False, message=f"작성 실패: {alert_text}")

        # URL에서 post_id 추출 (eyoom: /{board_id}/{post_id})
        current = self.driver.current_url
        wr_id = ""
        wr_id_m = re.search(r"/(\d+)\??$", current)
        if wr_id_m:
            wr_id = wr_id_m.group(1)
        if not wr_id:
            wr_id_m = re.search(r"wr_id=(\d+)", current)
            if wr_id_m:
                wr_id = wr_id_m.group(1)

        # eyoom: 글 작성 후 게시판 목록으로 리다이렉트됨 → 첫 글 ID 추출
        if not wr_id and "write" not in current:
            try:
                wr_id = self.driver.execute_script("""
                    var link = document.querySelector('.bl-list:not(.bl-notice) a[href*="/%s/"]');
                    if (link) {
                        var m = link.href.match(/\\/(\\d+)\\??$/);
                        if (m) return m[1];
                    }
                    var link2 = document.querySelector('a[href*="wr_id="]');
                    if (link2) {
                        var m2 = link2.href.match(/wr_id=(\\d+)/);
                        if (m2) return m2[1];
                    }
                    return '';
                """ % board_id) or ""
            except Exception:
                pass

        if "write" not in current:
            return WriteResult(success=True, id=wr_id, message="게시글 작성 완료")

        return WriteResult(success=False, message="게시글 작성 결과 확인 불가")

    # ──────────────────────────────────────────────
    #  7. 댓글 작성
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, *, board_id: str = "") -> WriteResult:
        """특정 게시글에 댓글 작성.

        eyoom: /{board_id}/{post_id} 로 이동 → textarea#wr_content 입력 →
        fviewcomment_submit() 호출.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if ":" in post_id and not board_id:
            board_id, post_id = post_id.split(":", 1)
        if not board_id:
            raise ValueError("board_id 필수 (직접 전달 또는 'bo_table:wr_id' 형식)")

        # eyoom: /{board_id}/{post_id}
        url = f"{self.base_url}/{board_id}/{post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self.require_human_check()

        # alert 처리 (로그인 필요, 삭제된 글 등)
        alert_text = self.handle_alert(accept=True, timeout=1.0)
        if alert_text:
            if any(kw in alert_text for kw in ("존재하지 않", "삭제", "이동된", "없는 글")):
                return WriteResult(success=False, message=f"존재하지 않는 글: {alert_text[:80]}")
            if "로그인" in alert_text or "회원" in alert_text:
                return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        # 댓글 textarea 찾기: textarea#wr_content (fviewcomment 폼 내) 또는 textarea#comment
        ta = None

        # 방법 1: eyoom fviewcomment 폼의 textarea
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     "form[name='fviewcomment'] textarea[name='wr_content'], "
                     "form[name='fviewcomment'] textarea#wr_content")
                )
            )
            ta = self.driver.find_element(
                By.CSS_SELECTOR,
                "form[name='fviewcomment'] textarea[name='wr_content'], "
                "form[name='fviewcomment'] textarea#wr_content"
            )
        except Exception:
            pass

        # 방법 2: 일반 textarea#wr_content
        if ta is None:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea[name='wr_content']")
            except Exception:
                pass

        # 방법 3: APMS textarea#comment
        if ta is None:
            try:
                ta = self.driver.find_element(By.CSS_SELECTOR, "textarea#comment")
            except Exception:
                return WriteResult(success=False, message="댓글 입력 폼을 찾을 수 없습니다. (레벨 제한 또는 로그인 필요)")

        ta.clear()
        ta.send_keys(content)
        time.sleep(0.3)

        # 제출: 댓글등록 버튼 클릭 → requestSubmit → form.submit fallback
        # NOTE: fviewcomment_submit()은 onsubmit 검증 함수이므로 직접 호출하면 안 됨.
        #       버튼 클릭이 자연스러운 폼 제출 플로우를 트리거함.
        submitted = False

        # 방법 1: "댓글등록" 버튼 직접 클릭 (가장 자연스러운 방법)
        btn_selectors = [
            "#btn_submit",
            "form[name='fviewcomment'] button[type='submit']",
            "#fviewcomment button[type='submit']",
            "form[name='fviewcomment'] input[type='submit']",
            "#btn_comment_submit",
        ]
        for sel in btn_selectors:
            if submitted:
                break
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn
                )
                time.sleep(0.3)
                btn.click()
                submitted = True
            except Exception:
                continue

        # 방법 2: 텍스트로 버튼 찾기 ("댓글등록")
        if not submitted:
            try:
                btn = self.driver.find_element(
                    By.XPATH,
                    "//form[@name='fviewcomment']//button[contains(text(),'댓글등록')] | "
                    "//form[@name='fviewcomment']//button[contains(text(),'등록')]"
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn
                )
                time.sleep(0.3)
                btn.click()
                submitted = True
            except Exception:
                pass

        # 방법 3: requestSubmit (onsubmit 핸들러 호출됨)
        if not submitted:
            try:
                self.driver.execute_script(
                    "var f = document.forms['fviewcomment']; "
                    "if (f && f.requestSubmit) { f.requestSubmit(); }"
                )
                submitted = True
            except Exception:
                pass

        # 방법 4: 최종 fallback — form.submit()
        if not submitted:
            try:
                self.driver.execute_script(
                    "var f = document.forms['fviewcomment']; if(f) f.submit();"
                )
                submitted = True
            except Exception:
                return WriteResult(success=False, message="댓글 제출 실패: 버튼/폼 모두 실패")

        time.sleep(1.5)
        self.require_human_check()

        # alert 결과 확인
        alert_text = self.detect_form_result(timeout=3.0)
        if alert_text:
            if "오류" in alert_text or "실패" in alert_text or "권한" in alert_text:
                return WriteResult(success=False, message=f"댓글 작성 실패: {alert_text}")

        # 제출 후 검증: 페이지에 작성한 댓글이 존재하는지 확인
        try:
            time.sleep(1.0)
            page_src = self.driver.page_source or ""
            # 댓글 내용 첫 20자로 존재 여부 확인
            snippet = content[:20].strip()
            if snippet and snippet in page_src:
                return WriteResult(success=True, message="댓글 작성 완료 (본문 확인됨)")
        except Exception:
            pass

        return WriteResult(success=True, message="댓글 작성 완료")

    # ──────────────────────────────────────────────
    #  출석체크
    # ──────────────────────────────────────────────

    def checkin(self) -> dict[str, Any]:
        """출석체크 (eyoom /attendance 페이지)."""
        from selenium.webdriver.common.by import By

        try:
            self.goto(self.base_url + "/attendance")
            time.sleep(2)
            self.require_human_check()

            for sel in [
                "button[type='submit']",
                "input[type='submit'][value*='출석']",
                "#attendance_submit",
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
                        break
                except Exception:
                    continue

            alert_text = self.detect_form_result(timeout=2.0) or ""
            return {"success": True, "message": alert_text or "출석 페이지 방문"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    # ──────────────────────────────────────────────
    #  등업 버튼 클릭
    # ──────────────────────────────────────────────

    def click_levelup(self) -> dict[str, Any]:
        """등업 버튼 클릭 (/guidelevel — eyoom gnuboard).

        eyoom 테마: ul.next_level 안에 다음 레벨 정보.
        조건 미달 시: <li class="pt3"><div class="this_state">등업조건미달</div></li>
        조건 충족 시: <li class="pt4"><button class="this_state_up" onclick="get_level_up('N');">등업신청</button></li>
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            self.goto(self.base_url + "/guidelevel")
            # next_level 영역이 나타날 때까지 대기
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "ul.next_level"))
                )
            except Exception:
                pass
            time.sleep(1)
            self.require_human_check()

            btn = None

            # 1) this_state_up 버튼 (조건 충족 시 나타남)
            for sel in [
                "ul.next_level button.this_state_up",
                "ul.next_level button",
                "ul.next_level a",
                ".next_level .pt4 button",
                ".next_level .pt3 button",
            ]:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    btn = el
                    break
                except Exception:
                    continue

            # 2) XPath 폴백
            if not btn:
                for xp in [
                    "//ul[contains(@class,'next_level')]//button[contains(text(),'등업')]",
                    "//button[contains(text(),'등업신청')]",
                    "//ul[contains(@class,'next_level')]//a[contains(text(),'등업')]",
                ]:
                    try:
                        el = self.driver.find_element(By.XPATH, xp)
                        btn = el
                        break
                    except Exception:
                        continue

            if btn:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn
                )
                time.sleep(0.3)
                btn.click()
            else:
                # 3) JS 직접 호출 fallback: get_level_up('N')
                # next_level에서 target level 추출
                js_called = False
                try:
                    onclick = self.driver.execute_script(
                        "var b = document.querySelector('ul.next_level button[onclick*=\"get_level_up\"]');"
                        "return b ? b.getAttribute('onclick') : null;"
                    )
                    if onclick:
                        self.driver.execute_script(onclick.replace("return false;", ""))
                        js_called = True
                except Exception:
                    pass

                if not js_called:
                    # 조건 미달 확인
                    try:
                        el = self.driver.find_element(
                            By.CSS_SELECTOR,
                            "ul.next_level .this_state",
                        )
                        deficit = self._parse_next_level_deficit()
                        result = {"success": False, "message": f"등업조건미달 — {el.text}"}
                        if deficit:
                            result["deficit"] = deficit
                        return result
                    except Exception:
                        pass
                    return {"success": False, "message": "등업 버튼을 찾을 수 없음 (guidelevel)"}

            # 확인 모달 처리: "정말로 등업 신청하시겠습니까?" → "확인" 클릭
            time.sleep(1.5)
            confirmed = False
            for sel in [
                "button.ajs-ok",
                ".ajs-primary button.ajs-ok",
                "//div[contains(@class,'ajs-modal')]//button[contains(text(),'확인')]",
                "//div[contains(@class,'modal')]//button[contains(text(),'확인')]",
                ".modal.show button.btn-primary",
            ]:
                try:
                    if sel.startswith("//"):
                        modal_btn = self.driver.find_element(By.XPATH, sel)
                    else:
                        modal_btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if modal_btn.is_displayed():
                        modal_btn.click()
                        confirmed = True
                        break
                except Exception:
                    continue

            # 네이티브 alert/confirm 폴백
            if not confirmed:
                try:
                    self.driver.switch_to.alert.accept()
                    confirmed = True
                except Exception:
                    pass

            time.sleep(2)
            alert_text = self.detect_form_result(timeout=3.0) or ""

            # 등업 후 실제 레벨 확인: guidelevel 리로드하여 this_level 읽기
            new_level = None
            try:
                self.driver.get(self.base_url + "/guidelevel")
                time.sleep(2)
                lv_el = self.driver.find_element(
                    By.CSS_SELECTOR, "ul.this_level li:nth-child(2)"
                )
                import re as _re
                lv_m = _re.search(r'(\d+)', lv_el.text)
                if lv_m:
                    new_level = int(lv_m.group(1))
            except Exception:
                pass

            result = {"success": True, "message": alert_text or "등업 신청 완료"}
            if new_level is not None:
                result["new_level"] = new_level
            return result
        except Exception as exc:
            try:
                self.driver.switch_to.alert.accept()
            except Exception:
                pass
            return {"success": False, "message": str(exc)}

    def _parse_next_level_deficit(self) -> dict | None:
        """ul.next_level의 li 항목에서 등업 조건 부족분을 파싱.

        eyoom 테마 ul.next_level 구조 (li 순서):
          [0] 직급명 (e.g. "일병")
          [1] 레벨 (e.g. "Lv.3")
          [2] 권한 설명
          [3] 가입일 (e.g. "2일" 또는 "3 / 2일")
          [4] 포인트 (e.g. "931 / 1,000P")
          [5] 후기 (e.g. "0 / 0개")
          [6] 게시글 (e.g. "5 / 1개")
          [7] 댓글 (e.g. "14 / 2개")
          [8] 상태 ("등업조건미달" 또는 버튼)

        Returns {"deficit": {"days": N, "points": N, "reviews": N, "posts": N, "comments": N}}
        """
        import re as _re
        from selenium.webdriver.common.by import By

        try:
            ul = self.driver.find_element(By.CSS_SELECTOR, "ul.next_level")
            lis = ul.find_elements(By.TAG_NAME, "li")
            if len(lis) < 8:
                return None

            # Map li indices (after first 3) to field names
            field_map = {3: "days", 4: "points", 5: "reviews", 6: "posts", 7: "comments"}
            current: dict[str, int] = {}
            required: dict[str, int] = {}
            deficit: dict[str, int] = {}

            for idx, field in field_map.items():
                if idx >= len(lis):
                    break
                text = lis[idx].text.strip()
                # Parse "current / required" format (e.g. "931 / 1,000P")
                m = _re.search(r'(-?[\d,]+)\s*/\s*([\d,]+)', text)
                if m:
                    cur = int(_re.sub(r'[^\d]', '', m.group(1)) or '0')
                    req = int(_re.sub(r'[^\d]', '', m.group(2)) or '0')
                else:
                    # Single value format (e.g. "2일") — this is the required value
                    nums = _re.findall(r'[\d,]+', text)
                    if nums:
                        req = int(_re.sub(r'[^\d]', '', nums[0]) or '0')
                        cur = 0
                    else:
                        # "-" or empty
                        cur, req = 0, 0

                current[field] = max(0, cur)
                required[field] = max(0, req)
                deficit[field] = max(0, req - cur)

            return {"current": current, "required": required, "deficit": deficit}
        except Exception:
            return None
