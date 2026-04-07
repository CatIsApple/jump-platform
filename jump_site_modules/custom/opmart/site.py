"""OpmartSite - 오피마트 (커스텀 PHP, AJAX)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ...base import (
    STATUS_COOLDOWN,
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
    Profile,
    WriteResult,
)
from . import parsers


class OpmartSite(BaseSite):
    SITE_NAME = "오피마트"

    # ──────────────────────────────────────────────
    #  login
    # ──────────────────────────────────────────────

    def login(self) -> LoginResult:
        self.emit(f"[오피마트] 시작: {self.base_url} (ID: {self.username})", "INFO")

        _acct = {"mb_id": self.username, "mb_password": self.password}

        # Naver warmup
        self.driver.get("https://naver.com")
        time.sleep(0.5)
        self.driver.get(self.base_url)
        time.sleep(0.7)
        self.require_human_check()

        # Handle popup/alert
        self._dismiss_popups()

        # Check login
        if self._is_logged_in():
            self.emit("[오피마트] 이미 로그인 상태.", "INFO")
            return LoginResult(success=True, method="already", message="이미 로그인 상태", account=_acct)

        # AJAX login
        login_result = self.driver.execute_script(
            """
            return (async () => {
                try {
                    const r = await fetch('/login/login_proc.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        },
                        body: 'act=login&referer=/&user_id=' + encodeURIComponent(arguments[0]) + '&user_pw=' + encodeURIComponent(arguments[1])
                    });
                    const text = await r.text();
                    return { status: r.status, body: text, url: r.url };
                } catch(e) {
                    return { status: 0, body: e.toString() };
                }
            })();
            """,
            self.username,
            self.password,
        )

        self.emit(f"[오피마트] 로그인 응답: {login_result}", "DEBUG")

        # Refresh to reflect login state
        self.driver.get(self.base_url)
        time.sleep(0.7)

        # Handle alert
        self._dismiss_popups()

        if not self._is_logged_in():
            return LoginResult(success=False, method="ajax", message="로그인 실패", account=_acct)

        self.emit("[오피마트] 로그인 성공.", "INFO")
        return LoginResult(success=True, method="ajax", message="로그인 성공", account=_acct)

    # ──────────────────────────────────────────────
    #  register
    # ──────────────────────────────────────────────

    def register(self, **kwargs: Any) -> LoginResult:
        """회원가입.

        kwargs: mb_id, mb_password, mb_name, mb_nick
        reCAPTCHA v2 자동 해결 (2captcha).
        가입 폼: /login/join_form.php
        """
        mb_id = kwargs.get("mb_id", self.username)
        mb_pw = kwargs.get("mb_password", self.password)
        mb_name = kwargs.get("mb_name", "테스트")
        mb_nick = kwargs.get("mb_nick", "테스터" + mb_id[-3:])

        acct = {"mb_id": mb_id, "mb_password": mb_pw, "mb_name": mb_name, "mb_nick": mb_nick}
        self.emit(f"[오피마트] 회원가입 시작: ID={mb_id}, 닉={mb_nick}", "INFO")

        # Navigate to join form (네트워크 오류 시 재시도)
        for attempt in range(3):
            try:
                self.driver.get(self.base_url + "/login/join_form.php")
                time.sleep(1.0)
                self._dismiss_popups()
                break
            except Exception as e:
                if attempt < 2:
                    self.emit(f"[오피마트] 가입 페이지 접속 재시도 ({attempt+1}/3): {e}", "WARN")
                    time.sleep(1.0)
                else:
                    raise

        # Fill form fields
        self.driver.execute_script("""
            var f = document.getElementById('frm');
            if (!f) return;
            var uid = document.getElementById('user_id');
            var upw = document.getElementById('user_pw');
            var rupw = document.getElementById('re_user_pw');
            var uname = document.getElementById('user_name');
            var unick = document.getElementById('user_nick');
            if (uid) { uid.value = arguments[0]; }
            if (upw) { upw.value = arguments[1]; }
            if (rupw) { rupw.value = arguments[1]; }
            if (uname) { uname.value = arguments[2]; }
            if (unick) { unick.value = arguments[3]; }
        """, mb_id, mb_pw, mb_name, mb_nick)
        time.sleep(0.3)

        # Solve reCAPTCHA v2
        recaptcha_sitekey = "6LdHOSMUAAAAAKXzUTF_gIYLTRhmWpUn7ekcsnSF"
        captcha_token = ""
        if self._captcha_api_key:
            self.emit("[오피마트] reCAPTCHA v2 해결 중...", "INFO")
            try:
                from twocaptcha import TwoCaptcha
                solver = TwoCaptcha(self._captcha_api_key)
                result = solver.recaptcha(
                    sitekey=recaptcha_sitekey,
                    url=self.driver.current_url,
                )
                captcha_token = result.get("code") if isinstance(result, dict) else str(result)
                self.emit(f"[오피마트] reCAPTCHA 토큰 획득: {captcha_token[:40]}...", "DEBUG")

                # Inject token into all g-recaptcha-response textareas
                self.driver.execute_script("""
                    var token = arguments[0];
                    // 1) 모든 g-recaptcha-response textarea에 토큰 주입
                    document.querySelectorAll('[id="g-recaptcha-response"], textarea[name="g-recaptcha-response"]').forEach(function(ta) {
                        ta.style.display = 'block';
                        ta.value = token;
                    });
                    // 2) grecaptcha.getResponse() 오버라이드
                    if (typeof grecaptcha !== 'undefined') {
                        try {
                            grecaptcha.getResponse = function() { return token; };
                        } catch(e) {}
                    }
                    // 3) callback 호출 (재귀 탐색)
                    if (typeof ___grecaptcha_cfg !== 'undefined') {
                        try {
                            var findCb = function(obj, depth) {
                                if (depth > 8 || !obj) return null;
                                if (typeof obj === 'function') return obj;
                                if (typeof obj === 'object') {
                                    for (var k in obj) {
                                        if (k === 'callback' && typeof obj[k] === 'function') return obj[k];
                                    }
                                    for (var k2 in obj) {
                                        var found = findCb(obj[k2], depth + 1);
                                        if (found) return found;
                                    }
                                }
                                return null;
                            };
                            var clients = ___grecaptcha_cfg.clients || {};
                            for (var key in clients) {
                                var cb = findCb(clients[key], 0);
                                if (cb) { cb(token); break; }
                            }
                        } catch(e) {}
                    }
                """, captcha_token)
                time.sleep(0.3)
            except Exception as e:
                self.emit(f"[오피마트] reCAPTCHA 해결 실패: {e}", "ERROR")
                return LoginResult(success=False, method="register", message=f"reCAPTCHA 실패: {e}", account=acct)
        else:
            self.emit("[오피마트] 2captcha API 키 없음 - reCAPTCHA 수동 대기 (120초)", "WARN")
            time.sleep(120)

        # Submit form via AJAX
        # g-recaptcha-response를 명시적으로 포함하여 전송
        submit_result = self.driver.execute_script("""
            return (async () => {
                try {
                    var f = document.getElementById('frm');
                    if (!f) return {status: 0, body: 'no_form'};
                    var fd = new FormData(f);
                    // reCAPTCHA 토큰 명시적 설정 (FormData에 누락될 수 있음)
                    if (arguments[0]) {
                        fd.set('g-recaptcha-response', arguments[0]);
                    }
                    var params = new URLSearchParams(fd).toString();
                    const r = await fetch('/login/join_proc.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        },
                        body: params
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch(e) {
                    return { status: 0, body: e.toString() };
                }
            })();
        """, captcha_token)

        self.emit(f"[오피마트] 가입 응답: {submit_result}", "DEBUG")

        # Handle alert
        time.sleep(1.0)
        alert_text = self.handle_alert(accept=True, timeout=3.0)
        if alert_text:
            self.emit(f"[오피마트] 가입 alert: {alert_text}", "DEBUG")

        # Check response
        body = ""
        if isinstance(submit_result, dict):
            body = submit_result.get("body", "")

        # Try JSON parse
        try:
            data = json.loads(body)
            if data.get("result") or data.get("success"):
                self.emit("[오피마트] 회원가입 성공 (JSON)", "INFO")
                return LoginResult(success=True, method="register", message="회원가입 성공", account=acct)
            msg = data.get("message", data.get("msg", ""))
            if msg:
                self.emit(f"[오피마트] 가입 결과: {msg}", "INFO")
                if "성공" in msg or "완료" in msg or "환영" in msg:
                    return LoginResult(success=True, method="register", message=msg, account=acct)
                return LoginResult(success=False, method="register", message=msg, account=acct)
        except (json.JSONDecodeError, TypeError):
            pass

        # HTML response check
        if "환영" in body or "성공" in body or "완료" in body or "가입" in body:
            self.emit("[오피마트] 회원가입 성공 (HTML)", "INFO")
            return LoginResult(success=True, method="register", message="회원가입 성공", account=acct)

        # If alert had success message
        if alert_text and ("환영" in alert_text or "성공" in alert_text or "완료" in alert_text or "가입" in alert_text):
            return LoginResult(success=True, method="register", message=alert_text, account=acct)

        # Fallback: try login to verify
        self.username = mb_id
        self.password = mb_pw
        lr = self.login()
        if lr.success:
            self.emit("[오피마트] 회원가입 후 로그인 성공 → 가입 성공으로 판단", "INFO")
            return LoginResult(success=True, method="register", message="회원가입 성공 (로그인 확인)", account=acct)

        return LoginResult(success=False, method="register", message=f"가입 결과 불확실: {body[:200]}", account=acct)

    # ──────────────────────────────────────────────
    #  get_profile
    # ──────────────────────────────────────────────

    def get_profile(self) -> Profile:
        """마이페이지에서 프로필 조회."""
        self.emit("[오피마트] 프로필 조회 시작", "INFO")
        self.driver.get(self.base_url + "/member/mypage_user_info.php")
        time.sleep(0.7)
        self._dismiss_popups()

        source = self.driver.page_source or ""

        # 로그인 페이지로 리다이렉트된 경우
        if "login.php" in self.driver.current_url and "mypage" not in self.driver.current_url:
            self.emit("[오피마트] 프로필: 로그인 필요", "WARN")
            return Profile()

        profile = parsers.parse_profile(source)
        self.emit(f"[오피마트] 프로필: {profile.nickname} lv={profile.level} pt={profile.point}", "INFO")
        return profile

    # ──────────────────────────────────────────────
    #  get_boards
    # ──────────────────────────────────────────────

    def get_boards(self) -> list[Board]:
        """게시판 목록 조회."""
        self.emit("[오피마트] 게시판 목록 조회", "INFO")
        self.driver.get(self.base_url)
        time.sleep(0.7)
        self._dismiss_popups()

        source = self.driver.page_source or ""
        boards = parsers.parse_boards(source)
        self.emit(f"[오피마트] 게시판 {len(boards)}개 발견", "INFO")
        return boards

    # ──────────────────────────────────────────────
    #  get_posts
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
        """게시판 글 목록 조회.

        URL: /bbs/board_list.php?type={board_id}&page={page}&search_key={search_field}&search_str={search_text}
        """
        self.emit(f"[오피마트] 게시글 조회: board={board_id}, page={page}", "INFO")

        url = f"{self.base_url}/bbs/board_list.php?type={board_id}&page={page}"
        if search_field and search_text:
            url += f"&search_key={search_field}&search_str={search_text}"

        self.driver.get(url)
        time.sleep(0.7)
        self._dismiss_popups()

        # 로그인 페이지로 리다이렉트 체크
        cur = self.driver.current_url
        if "login.php" in cur and "board_list" not in cur:
            self.emit("[오피마트] 게시글 조회: 로그인 필요 → 로그인 후 재시도", "WARN")
            lr = self.login()
            if lr.success:
                self.driver.get(url)
                time.sleep(0.7)
                self._dismiss_popups()
            else:
                return []

        source = self.driver.page_source or ""
        posts = parsers.parse_posts(source, board_id)
        self.emit(f"[오피마트] 게시글 {len(posts)}개", "INFO")
        return posts

    # ──────────────────────────────────────────────
    #  get_comments
    # ──────────────────────────────────────────────

    def get_comments(self, post_id: str, board_id: str = "") -> list[Comment]:
        """게시글 댓글 조회.

        URL: /bbs/board_read.php?type={board_id}&idx={post_id}
        """
        if not board_id:
            board_id = "notice"
        self.emit(f"[오피마트] 댓글 조회: board={board_id}, post={post_id}", "INFO")

        url = f"{self.base_url}/bbs/board_read.php?type={board_id}&idx={post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self._dismiss_popups()

        source = self.driver.page_source or ""
        comments = parsers.parse_comments(source, post_id)
        self.emit(f"[오피마트] 댓글 {len(comments)}개", "INFO")
        return comments

    # ──────────────────────────────────────────────
    #  write_post
    # ──────────────────────────────────────────────

    def write_post(self, board_id: str, subject: str, content: str) -> WriteResult:
        """게시글 작성.

        /bbs/board_form.php?type={board_id} 에 접근.
        폼 action: /bbs/board_proc.php, act=add
        에디터: CKEditor 4 (textarea#content)
        """
        self.emit(f"[오피마트] 게시글 작성: board={board_id}, title={subject[:30]}", "INFO")

        # Navigate to write form
        url = f"{self.base_url}/bbs/board_form.php?type={board_id}"
        self.driver.get(url)
        time.sleep(1.0)
        self._dismiss_popups()

        # 에러 페이지 체크
        if "ERROR" in (self.driver.title or ""):
            self.emit("[오피마트] 글쓰기 페이지 에러", "ERROR")
            return WriteResult(success=False, message="글쓰기 페이지 접근 불가")

        # 로그인 체크
        cur = self.driver.current_url
        if "login.php" in cur and "board_form" not in cur:
            self.emit("[오피마트] 글쓰기: 로그인 필요", "WARN")
            return WriteResult(success=False, message="로그인 필요")

        # Fill form via AJAX POST (bypass CKEditor issues)
        result = self.driver.execute_script("""
            return (async () => {
                try {
                    var form = document.getElementById('frm');
                    if (!form) return {status: 0, body: 'no_form'};

                    // 제목: readonly 제거 후 설정
                    var titleEl = form.querySelector('#title') || form.querySelector('input[name="title"]');
                    if (titleEl) {
                        titleEl.removeAttribute('readonly');
                        titleEl.value = arguments[0];
                    }

                    // 내용: CKEditor가 있으면 setData, 없으면 textarea
                    if (typeof CKEDITOR !== 'undefined' && CKEDITOR.instances.content) {
                        CKEDITOR.instances.content.setData(arguments[1]);
                    }
                    var ta = form.querySelector('textarea#content') || form.querySelector('textarea[name="content"]');
                    if (ta) ta.value = arguments[1];

                    // CKEditor 동기화 대기
                    await new Promise(r => setTimeout(r, 500));

                    // updateElement로 textarea 동기화
                    if (typeof CKEDITOR !== 'undefined' && CKEDITOR.instances.content) {
                        CKEDITOR.instances.content.updateElement();
                    }

                    // FormData 수집 후 POST (기본 양식 항상 덮어씌우기)
                    var fd = new FormData(form);
                    fd.set('title', arguments[0]);
                    fd.set('content', arguments[1]);

                    var params = new URLSearchParams(fd).toString();
                    const r = await fetch('/bbs/board_proc.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: params
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch(e) {
                    return { status: 0, body: e.toString() };
                }
            })();
        """, subject, content)

        self.emit(f"[오피마트] 글쓰기 응답: {result}", "DEBUG")
        time.sleep(1.0)

        # Handle alerts
        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text:
            self.emit(f"[오피마트] 글쓰기 alert: {alert_text}", "DEBUG")
            _fail_kw = ("오류", "실패", "권한", "로그인", "등급")
            if any(kw in alert_text for kw in _fail_kw):
                return WriteResult(success=False, message=f"글쓰기 실패: {alert_text}")

        if not isinstance(result, dict):
            return WriteResult(success=False, message="글쓰기 응답 없음")

        body = result.get("body", "")
        status = result.get("status", 0)

        # JSON response
        try:
            data = json.loads(body)
            if data.get("result"):
                post_id = str(data.get("idx", ""))
                self.emit("[오피마트] 게시글 작성 성공 (JSON)", "INFO")
                return WriteResult(success=True, id=post_id, message="게시글 작성 성공")
            msg = data.get("message", data.get("msg", ""))
            if msg:
                return WriteResult(success=False, message=msg)
        except (json.JSONDecodeError, TypeError):
            pass

        if status == 200 and "no_form" not in body:
            self.emit("[오피마트] 게시글 작성 성공 (HTTP 200)", "INFO")
            return WriteResult(success=True, message="게시글 작성 성공")

        return WriteResult(success=False, message=f"글쓰기 결과 불확실: HTTP {status}")

    # ──────────────────────────────────────────────
    #  write_comment
    # ──────────────────────────────────────────────

    def write_comment(self, post_id: str, content: str, board_id: str = "") -> WriteResult:
        """댓글 작성.

        게시글 상세 페이지의 reply_frm 폼 사용.
        AJAX POST: /bbs/board_proc.php + act=comment_add
        """
        if not board_id:
            board_id = "notice"
        self.emit(f"[오피마트] 댓글 작성: board={board_id}, post={post_id}", "INFO")

        # Navigate to post
        url = f"{self.base_url}/bbs/board_read.php?type={board_id}&idx={post_id}"
        self.driver.get(url)
        time.sleep(0.7)
        self._dismiss_popups()

        # 로그인 체크
        cur = self.driver.current_url
        if "login.php" in cur and "board_read" not in cur:
            self.emit("[오피마트] 댓글: 로그인 필요", "WARN")
            return WriteResult(success=False, message="로그인 필요")

        # Fill comment and submit via AJAX
        # 실제 JS: var json_parm = $('#reply_frm').serialize();
        #          json_parm += "&act=comment_add";
        #          $.post("/bbs/board_proc.php", json_parm, ...)
        result = self.driver.execute_script("""
            return (async () => {
                try {
                    var form = document.getElementById('reply_frm');
                    if (!form) return {status: 0, body: 'no_form'};

                    // Fill textarea
                    var ta = form.querySelector('textarea#comment') || form.querySelector('textarea[name="comment"]');
                    if (ta) ta.value = arguments[0];

                    // Gather form data + act=comment_add
                    var fd = new FormData(form);
                    var params = new URLSearchParams(fd).toString();
                    params += '&act=comment_add';

                    const r = await fetch('/bbs/board_proc.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: params
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch(e) {
                    return { status: 0, body: e.toString() };
                }
            })();
        """, content)

        self.emit(f"[오피마트] 댓글 응답: {result}", "DEBUG")
        time.sleep(1)

        # Handle alert
        alert_text = self.detect_form_result(timeout=2.0)
        if alert_text:
            self.emit(f"[오피마트] 댓글 alert: {alert_text}", "DEBUG")
            _fail_kw = ("오류", "실패", "권한", "로그인", "등급", "30일", "작성할 수 없")
            if any(kw in alert_text for kw in _fail_kw):
                return WriteResult(success=False, message=f"댓글 실패: {alert_text}")

        if not isinstance(result, dict):
            return WriteResult(success=False, message="댓글 응답 없음")

        body = result.get("body", "")
        status = result.get("status", 0)

        # JSON response check
        try:
            data = json.loads(body)
            if data.get("result") or data.get("success"):
                self.emit("[오피마트] 댓글 작성 성공 (JSON)", "INFO")
                return WriteResult(success=True, message="댓글 작성 성공")
            msg = data.get("message", data.get("msg", ""))
            if msg and ("성공" in msg or "완료" in msg):
                return WriteResult(success=True, message=msg)
            if msg:
                return WriteResult(success=False, message=msg)
        except (json.JSONDecodeError, TypeError):
            pass

        # HTML/text response
        if status == 200 and body and "no_form" not in body:
            # 200 응답이면 대부분 성공
            self.emit("[오피마트] 댓글 작성 성공 (HTTP 200)", "INFO")
            return WriteResult(success=True, message="댓글 작성 성공")

        # Reload page and check
        self.driver.get(url)
        time.sleep(0.7)
        source = self.driver.page_source or ""
        if content[:20] in source:
            return WriteResult(success=True, message="댓글 작성 성공 (페이지 확인)")

        return WriteResult(success=False, message=f"댓글 결과 불확실: HTTP {status}")

    # ──────────────────────────────────────────────
    #  jump
    # ──────────────────────────────────────────────

    def jump(self) -> JumpResult:
        # AJAX jump
        result = self.driver.execute_script("""
            return (async () => {
                try {
                    const r = await fetch('/c_member/jump_proc.php', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: 'act=manual'
                    });
                    const text = await r.text();
                    return { status: r.status, body: text };
                } catch(e) {
                    return { status: 0, body: e.toString() };
                }
            })();
        """)

        if not result or not isinstance(result, dict):
            return JumpResult(status=STATUS_FAILED, message="점프 응답 없음")

        status_code = result.get("status", 0)
        body = (result.get("body") or "").strip()

        self.emit(f"[오피마트] 점프 응답: HTTP {status_code}, body={body[:100]}", "DEBUG")

        try:
            data = json.loads(body)
            if data.get("result"):
                return JumpResult(status=STATUS_SUCCESS, message="점프 완료")
            msg = data.get("message", "")
            if "10분" in msg or "대기" in msg or "잠시 후" in msg or "최근" in msg:
                return JumpResult(status=STATUS_COOLDOWN, message=msg or "10분 대기")
            if "부족" in msg or "횟수" in msg:
                return JumpResult(status=STATUS_INSUFFICIENT, message=msg or "횟수 부족")
            return JumpResult(status=STATUS_FAILED, message=msg or "점프 실패")
        except Exception:
            pass

        if status_code == 200 and '"result":true' in body:
            return JumpResult(status=STATUS_SUCCESS, message="점프 완료")

        return JumpResult(status=STATUS_FAILED, message=f"점프 실패 (HTTP {status_code})")

    # ──────────────────────────────────────────────
    #  helpers
    # ──────────────────────────────────────────────

    def _is_logged_in(self) -> bool:
        """현재 페이지에서 로그인 상태 확인."""
        try:
            src = self.driver.page_source or ""
        except Exception:
            src = ""
        return "로그아웃" in src or "logout" in src.lower()

    def _dismiss_popups(self) -> None:
        """팝업 배너/알럿 닫기."""
        # Native alert
        try:
            alert_obj = self.driver.switch_to.alert
            self.emit(f"[오피마트] Alert: {alert_obj.text}", "DEBUG")
            alert_obj.accept()
            time.sleep(0.2)
        except Exception:
            pass

        # DOM popups
        try:
            self.driver.execute_script("""
                document.querySelectorAll('.popup_banner, .popup_banner1, [class*="popup"]').forEach(function(el) {
                    el.style.display = 'none';
                });
                document.querySelectorAll('.bb_close, .bb_close1, [class*="popup"][class*="close"]').forEach(function(el) {
                    try { el.click(); } catch(e) {}
                });
            """)
        except Exception:
            pass
