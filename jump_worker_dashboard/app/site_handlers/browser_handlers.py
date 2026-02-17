from __future__ import annotations

import re
import time
from typing import Any, Callable

from ..captcha_solver import is_robot_page, robot_pass
from ..exceptions import UserInterventionRequired
from ..file_manager import load_cookies, save_cookies

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"
STATUS_COOLDOWN = "cooldown"
STATUS_INSUFFICIENT = "insufficient"
STATUS_LOGIN_REQUIRED = "login_required"

# 버튼 텍스트로 남은 시간(예: 10:00, 09:59) 표시되는 케이스
_COUNTDOWN_FULL_RE = re.compile(r"^\\d{1,2}:\\d{2}$")
_COUNTDOWN_FIND_RE = re.compile(r"\\b(\\d{1,2}:\\d{2})\\b")

# 엔진에서 설정하는 모듈 레벨 변수
_captcha_api_key: str = ""
_captcha_emit: Callable[[str, str], None] | None = None


def configure_captcha(api_key: str, emit: Callable[[str, str], None] | None = None) -> None:
    """엔진에서 호출하여 2Captcha API 키와 로거를 설정."""
    global _captcha_api_key, _captcha_emit  # noqa: PLW0603
    _captcha_api_key = api_key or ""
    _captcha_emit = emit


def _require_human_check(driver: Any) -> None:
    """로봇/캡차 페이지가 감지되면 2Captcha로 자동 해결을 시도.

    자동 해결에 실패하면 UserInterventionRequired를 발생시킨다.
    """
    if not is_robot_page(driver):
        return

    # 2Captcha로 자동 해결 시도
    if _captcha_api_key:
        solved = robot_pass(driver, _captcha_api_key, _captcha_emit)
        if solved:
            return

    # 자동 해결 실패 → '차단됨' 처리(엔진은 멈추지 않고 다음 작업으로 넘어감)
    raise UserInterventionRequired(
        "로봇/캡차 페이지가 감지되었습니다. 2Captcha 자동 해결에 실패했습니다. "
        "이 실행은 '차단됨'으로 기록하고 다음 작업으로 넘어갑니다."
    )


def _classify_result_text(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    if not t:
        return (STATUS_UNKNOWN, "결과 확인 불가")

    # 사이트에 따라 점프 버튼 텍스트가 남은시간으로 바뀌며(10:00 -> 09:59...) 이를 성공/대기 신호로 본다.
    if _COUNTDOWN_FULL_RE.match(t):
        return (STATUS_COOLDOWN, t)

    # 대기(쿨다운) - 다양한 사이트 문구 포괄
    if "대기" in t or "10분" in t or "5분" in t or "분에 한번" in t or "분 후" in t:
        return (STATUS_COOLDOWN, t)

    # 횟수/포인트 부족 계열
    if ("횟수" in t or "회수" in t) and ("없" in t or "부족" in t or "초과" in t or "소진" in t):
        return (STATUS_INSUFFICIENT, t)
    if "남은" in t and ("0회" in t or "0개" in t or "0번" in t):
        return (STATUS_INSUFFICIENT, t)
    if "내일" in t and ("다시" in t or "시도" in t):
        return (STATUS_INSUFFICIENT, t)
    if "소진" in t or "모두 사용" in t:
        return (STATUS_INSUFFICIENT, t)

    # 중지/정지 상태
    if "중지" in t or "정지" in t or "일시중지" in t or "중단" in t:
        return (STATUS_FAILED, t)

    # 로그인 필요
    if "회원만" in t or "로그인" in t:
        return (STATUS_LOGIN_REQUIRED, t)

    # 성공
    if "완료" in t or "성공" in t or "올렸습니다" in t:
        return (STATUS_SUCCESS, t)

    # 실패(기타)
    if "실패" in t or "불가" in t or "오류" in t:
        return (STATUS_FAILED, t)

    # 알 수 없는 문구는 실패로 단정하지 않고 확인 불가로 남긴다.
    return (STATUS_UNKNOWN, t)


def _find_countdown_in_clickables(driver: Any, By: Any) -> str | None:
    """페이지 내 버튼/링크 텍스트에서 카운트다운(예: 09:59) 탐색."""
    # 1) jump 관련 요소 우선(오탐 방지)
    xpaths = [
        "//*[self::button or self::a]["
        "(contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'jump') "
        "or contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'jump'))"
        "]",
        "//*[self::button or self::a]",
    ]
    for xp in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xp)
        except Exception:
            continue
        for el in els:
            try:
                if hasattr(el, "is_displayed") and not el.is_displayed():
                    continue
                txt = (el.text or "").strip()
            except Exception:
                continue
            if len(txt) <= 5 and _COUNTDOWN_FULL_RE.match(txt):
                return txt
            m = _COUNTDOWN_FIND_RE.search(txt)
            if m:
                return m.group(1)
    return None


def _wait_for_countdown(driver: Any, By: Any, timeout_s: float = 4.0) -> str | None:
    end = time.time() + float(timeout_s)
    while time.time() < end:
        v = _find_countdown_in_clickables(driver, By)
        if v:
            return v
        time.sleep(0.2)
    return None


def _wait_for_login_text(driver: Any, text: str, timeout_s: float = 3.0) -> bool:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, timeout_s).until(
            EC.presence_of_element_located((By.XPATH, f"//*[normalize-space(text())='{text}']"))
        )
        return True
    except Exception:
        return False


def _naver_warmup(driver: Any, *, sleep_s: float = 0.5) -> None:
    """원본 jump.exe 동작처럼 네이버를 한번 연 뒤 대상 도메인으로 이동하는 워밍업."""
    try:
        driver.get("https://naver.com")
        time.sleep(float(sleep_s))
    except Exception:
        return


def _goto(driver: Any, url: str, *, via_script: bool = False) -> None:
    """url로 이동. 원본처럼 execute_script(location.href) 경로도 지원."""
    if not url.startswith("http"):
        url = f"https://{url}"
    if not via_script:
        driver.get(url)
        return
    try:
        driver.execute_script("window.location.href = arguments[0];", url)
    except Exception:
        driver.get(url)


def hellobam(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["PHPSESSID"]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    # 쿠키 주입
    for k in cookie_keys:
        try:
            driver.delete_cookie(k)
        except Exception:
            pass
    try:
        driver.refresh()
    except Exception:
        pass

    load_cookies(driver, domain, user["id"], cookie_keys)
    try:
        driver.refresh()
    except Exception:
        pass
    _require_human_check(driver)

    # 로그인 확인 / 필요 시 로그인
    if not _wait_for_login_text(driver, "로그아웃", timeout_s=2.0):
        # 원본 로직: 쿠키 삭제 후 새로고침, 그 다음 로그인 링크 클릭
        for k in cookie_keys:
            try:
                driver.delete_cookie(k)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        try:
            login_link = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.LINK_TEXT, "로그인")))
            login_link.click()
        except Exception:
            return STATUS_FAILED, "로그인 링크를 찾지 못했습니다."

        time.sleep(0.5)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, "//input[@name='mb_id']")))
            driver.find_element(By.XPATH, "//input[@name='mb_id']").send_keys(user["id"])
            driver.find_element(By.XPATH, "//input[@name='mb_password']").send_keys(user["pw"])
            driver.find_element(By.CSS_SELECTOR, "BUTTON[type='submit'].btn_submit").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(1.0)
        _require_human_check(driver)
        if not _wait_for_login_text(driver, "로그아웃", timeout_s=4.0):
            return STATUS_LOGIN_REQUIRED, "로그인 실패(로그아웃 표시 없음)"
        save_cookies(driver, domain, user["id"], cookie_keys)

    # 점프 실행
    result_text = ""
    try:
        element = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@href=\"javascript:sidebar_open('sidebar-user');\"]"))
        )
        element.click()
        time.sleep(0.4)
        jpbtn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#jumpbtn")))
        jpbtn.click()
        time.sleep(0.5)

        # SweetAlert confirm ("예" 버튼)
        try:
            confirm_button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@class, 'swal2-confirm')]")
                )
            )
            confirm_button.click()
        except Exception:
            pass

        # SweetAlert 전환 대기 (confirm → result)
        time.sleep(1.5)

        # 결과 SweetAlert 텍스트 읽기
        try:
            el = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".swal2-html-container"))
            )
            result_text = (el.text or "").strip()
        except Exception:
            pass
        if not result_text:
            try:
                el = WebDriverWait(driver, 1).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".swal2-title"))
                )
                result_text = (el.text or "").strip()
            except Exception:
                pass

        # 결과 SweetAlert 닫기 (확인 버튼)
        try:
            ok_btn = driver.find_element(By.CSS_SELECTOR, ".swal2-confirm")
            if ok_btn.is_displayed():
                ok_btn.click()
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        # SweetAlert가 이미 떠있는 경우 텍스트 읽기 시도
        try:
            el = driver.find_element(By.CSS_SELECTOR, ".swal2-html-container, .swal2-title")
            result_text = (el.text or "").strip()
        except Exception:
            pass
        if not result_text:
            return STATUS_FAILED, f"점프 실행 실패: {exc}"

    save_cookies(driver, domain, user["id"], cookie_keys)
    status, msg = _classify_result_text(result_text)
    if status == STATUS_UNKNOWN:
        # 알림/문구가 없더라도 버튼 텍스트가 10:00 -> 09:59처럼 바뀌는 경우가 있어 보정한다.
        timer = _wait_for_countdown(driver, By, timeout_s=4.0)
        if timer:
            status, msg = (STATUS_COOLDOWN, timer)
    if status == STATUS_SUCCESS:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
    elif status == STATUS_UNKNOWN:
        emit(f"[확인불가] {user['name']} - {user['id']}: {user['startedAt']} {msg}", "WARNING")
    elif status == STATUS_COOLDOWN:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} {msg}", "INFO")
    elif status == STATUS_INSUFFICIENT:
        emit(f"[부족] {user['name']} - {user['id']}: {user['startedAt']} {msg}", "WARNING")
    elif status == STATUS_LOGIN_REQUIRED:
        emit(f"[로그인] {user['name']} - {user['id']}: {user['startedAt']} {msg}", "WARNING")
    else:
        emit(f"[실패] {user['name']} - {user['id']}: {user['startedAt']} {msg}", "ERROR")

    return status, msg


def opguide(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["__data_a"]

    def is_login() -> bool:
        return _wait_for_login_text(driver, "로그아웃", timeout_s=2.0)

    _naver_warmup(driver, sleep_s=0.0)
    _goto(driver, domain, via_script=True)
    _require_human_check(driver)

    for key in cookie_keys:
        try:
            driver.delete_cookie(key)
        except Exception:
            pass
    try:
        driver.refresh()
        time.sleep(1.0)
    except Exception:
        pass

    if load_cookies(driver, domain, user["id"], cookie_keys):
        try:
            driver.refresh()
            time.sleep(1.0)
        except Exception:
            pass

    if not is_login():
        for key in cookie_keys:
            try:
                driver.delete_cookie(key)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        login_url = domain + "/bbs/login.php"
        _goto(driver, login_url, via_script=True)
        time.sleep(3.0)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, "//input[@name='mb_id']")))
            driver.find_element(By.CSS_SELECTOR, "#login_id").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, "#login_pw").send_keys(user["pw"])
            time.sleep(6.0)
            driver.find_element(By.CSS_SELECTOR, "BUTTON[type='submit'].btn-login").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(4.0)
        _require_human_check(driver)
        if not is_login():
            return STATUS_LOGIN_REQUIRED, "로그인 실패(로그아웃 표시 없음)"

        save_cookies(driver, domain, user["id"], cookie_keys)

    # 점프 실행 전 남은 점프 횟수 확인 (사이드바 열기 → "남은점프: N" 읽기)
    try:
        # 사이드바를 열기 위해 점프관리 버튼 또는 관련 링크 클릭
        driver.execute_script("""
            // sidebar-user 열기 (헬로밤과 유사 구조)
            if (typeof sidebar_open === 'function') {
                try { sidebar_open('sidebar-user'); } catch(e) {}
            }
            // 또는 점프관리 관련 링크 클릭
            var links = document.querySelectorAll('a[href*="sidebar"], a[onclick*="sidebar"]');
            for (var i = 0; i < links.length; i++) {
                try { links[i].click(); } catch(e) {}
            }
        """)
        time.sleep(1.0)

        remaining = driver.execute_script("""
            // 페이지 전체에서 "남은점프" 텍스트 탐색
            var all = document.body ? document.body.innerText : '';
            var m = all.match(/남은\\s*점프\\s*[:：]\\s*(\\d+)/);
            if (m) return parseInt(m[1], 10);
            // "남은 점프" 변형도 탐색
            var m2 = all.match(/남은\\s*점프\\s*[:：]?\\s*(\\d+)/);
            if (m2) return parseInt(m2[1], 10);
            // 점프 횟수 관련 텍스트 탐색
            var m3 = all.match(/잔여\\s*점프\\s*[:：]?\\s*(\\d+)/);
            if (m3) return parseInt(m3[1], 10);
            // 사이드바 내부에서 더 구체적으로 탐색
            var sidebar = document.querySelector('#sidebar-user, .sidebar-user, [class*="sidebar"]');
            if (sidebar) {
                var st = sidebar.innerText || '';
                var m4 = st.match(/남은\\s*점프\\s*[:：]?\\s*(\\d+)/);
                if (m4) return parseInt(m4[1], 10);
            }
            return -1;  // 찾지 못함
        """)
        emit(f"[오피가이드] 남은점프 확인 결과: {remaining}", "DEBUG")
        if isinstance(remaining, int) and remaining == 0:
            emit(f"[부족] {user['name']} - {user['id']}: 남은점프 0 - 횟수 부족", "WARNING")
            save_cookies(driver, domain, user["id"], cookie_keys)
            return STATUS_INSUFFICIENT, "남은 점프 횟수가 0입니다"
        if isinstance(remaining, int) and remaining > 0:
            emit(f"[오피가이드] 남은점프: {remaining}", "DEBUG")
    except Exception as exc:
        emit(f"[오피가이드] 남은점프 확인 실패 (무시하고 계속): {exc}", "DEBUG")

    # 점프 실행: fnJump() 호출 후 SweetAlert/native alert 결과 읽기
    result_text = ""
    try:
        driver.execute_script("fnJump();")
        time.sleep(1.0)

        # 1) native confirm/alert 처리
        try:
            WebDriverWait(driver, 2).until(EC.alert_is_present())
            a = driver.switch_to.alert
            a.accept()  # confirm "예"
            time.sleep(1.0)
        except Exception:
            pass

        # 2) SweetAlert confirm 처리 (예 버튼)
        try:
            swal_btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@class, 'swal2-confirm')]")
                )
            )
            swal_btn.click()
            time.sleep(1.5)
        except Exception:
            pass

        # 3) 결과 SweetAlert 텍스트 읽기
        try:
            el = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".swal2-html-container"))
            )
            result_text = (el.text or "").strip()
        except Exception:
            pass
        if not result_text:
            try:
                el = WebDriverWait(driver, 1).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".swal2-title"))
                )
                result_text = (el.text or "").strip()
            except Exception:
                pass

        # 4) native alert 결과
        if not result_text:
            try:
                WebDriverWait(driver, 2).until(EC.alert_is_present())
                a = driver.switch_to.alert
                result_text = a.text
                a.accept()
            except Exception:
                pass

        # 5) window.__fnJumpResult fallback
        if not result_text:
            try:
                result_text = driver.execute_script("return window.__fnJumpResult || '';") or ""
            except Exception:
                pass

    except UserInterventionRequired:
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            a = driver.switch_to.alert
            result_text = a.text
            a.accept()
        except Exception:
            pass
        if not result_text:
            return STATUS_FAILED, f"fnJump 실행 실패: {exc}"

    # SweetAlert 결과 닫기
    try:
        swal_close = driver.find_element(By.CSS_SELECTOR, ".swal2-confirm")
        if swal_close.is_displayed():
            swal_close.click()
    except Exception:
        pass

    save_cookies(driver, domain, user["id"], cookie_keys)

    if result_text:
        emit(f"[오피가이드] 결과: {result_text}", "DEBUG")
        status, msg = _classify_result_text(result_text)
        if status != STATUS_UNKNOWN:
            return status, msg

    # 카운트다운 체크 (대기 신호)
    timer = _wait_for_countdown(driver, By, timeout_s=6.0)
    if timer:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} {timer}", "INFO")
        return STATUS_COOLDOWN, timer

    emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 실행", "INFO")
    return STATUS_SUCCESS, "점프 실행"


def opview(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    try:
        from selenium.webdriver.common.alert import Alert
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["PHPSESSID"]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    for k in cookie_keys:
        try:
            driver.delete_cookie(k)
        except Exception:
            pass
    try:
        driver.refresh()
    except Exception:
        pass

    load_cookies(driver, domain, user["id"], cookie_keys)
    try:
        driver.refresh()
    except Exception:
        pass
    _require_human_check(driver)

    def is_login() -> bool:
        try:
            WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((By.XPATH, "//*[normalize-space(text())='내글보기']"))
            )
            return True
        except Exception:
            return False

    # 쿠키로 로그인 확인이 안 되면 원본 로직처럼 로그인까지 시도
    if not is_login():
        for k in cookie_keys:
            try:
                driver.delete_cookie(k)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        login_url = f"{domain}/bbs/login.php"
        try:
            driver.get(login_url)
        except Exception:
            _goto(driver, login_url, via_script=False)
        time.sleep(0.5)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#login_fs input[name='mb_id']"))
            )
            driver.find_element(By.CSS_SELECTOR, "#login_fs input[name='mb_id']").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, "#login_fs input[name='mb_password']").send_keys(user["pw"])
            driver.find_element(By.CSS_SELECTOR, "#login_fs button[type='submit']").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(1.0)
        _require_human_check(driver)
        if not is_login():
            return STATUS_LOGIN_REQUIRED, "로그인 실패(내글보기 없음)"
        save_cookies(driver, domain, user["id"], cookie_keys)

    alert_texts = []
    try:
        element = driver.find_element(By.XPATH, "//li[@class='com_jump']//a[contains(text(), '업소 점프하기')]")
        driver.execute_script("arguments[0].click();", element)

        # 첫 번째 alert (confirm 또는 결과)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            a1 = Alert(driver)
            alert_texts.append(a1.text)
            a1.accept()
        except Exception:
            pass

        # 두 번째 alert (결과)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            a2 = Alert(driver)
            alert_texts.append(a2.text)
            a2.accept()
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"점프 실행 실패: {exc}"

    save_cookies(driver, domain, user["id"], cookie_keys)

    # alert 텍스트에서 결과 판정 (마지막 alert이 결과일 가능성이 높음)
    for txt in reversed(alert_texts):
        if txt:
            emit(f"[오피뷰] 결과: {txt}", "DEBUG")
            status, msg = _classify_result_text(txt)
            if status != STATUS_UNKNOWN:
                return status, msg

    # 카운트다운 체크
    timer = _wait_for_countdown(driver, By, timeout_s=4.0)
    if timer:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} {timer}", "INFO")
        return STATUS_COOLDOWN, timer

    emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 실행", "INFO")
    return STATUS_SUCCESS, "점프 실행"


def opmania(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    """오피매니아 자동 점프 (브라우저).

    gnuboard 기반. /bbs/login.php → #flogin 폼 → /bbs/jump_company.php #manual_btn.
    """
    from selenium.webdriver.common.alert import Alert
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = (user.get("domain") or "").strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[오피매니아] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) naver 워밍업 후 사이트 이동 ──
    driver.get("https://naver.com")
    time.sleep(0.5)
    driver.execute_script("window.location.href = arguments[0];", base)
    time.sleep(2)
    _require_human_check(driver)

    # ── 2) 로그인 ──
    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    if "로그아웃" in src:
        emit("[오피매니아] 이미 로그인 상태.", "INFO")
    else:
        driver.execute_script("window.location.href = arguments[0];", base + "/bbs/login.php")
        time.sleep(2)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#flogin input[name='mb_id']"))
            )
            driver.execute_script("""
                var f = document.getElementById('flogin');
                f.querySelector('input[name="mb_id"]').value = arguments[0];
                f.querySelector('input[name="mb_password"]').value = arguments[1];
                f.submit();
            """, uid, pw)
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        # 로그인 직후 alert 처리 (원본: dismiss)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            Alert(driver).dismiss()
        except Exception:
            pass

        time.sleep(1)
        _require_human_check(driver)

        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        emit("[오피매니아] 로그인 성공.", "INFO")

    # ── 3) 점프 실행 ──
    try:
        driver.execute_script("window.location.href = arguments[0];", base + "/bbs/jump_company.php")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#manual_btn"))
        )
        driver.find_element(By.CSS_SELECTOR, "#manual_btn").click()

        # 1) 확인(Confirm) alert
        WebDriverWait(driver, 3).until(EC.alert_is_present())
        Alert(driver).accept()

        # 2) 결과 alert
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            a2 = Alert(driver)
            msg = a2.text
            a2.accept()
            emit(f"[오피매니아] 결과 alert: {msg}", "DEBUG")
        except Exception:
            msg = ""
    except Exception as exc:
        return STATUS_FAILED, f"점프 실행 실패: {exc}"

    if msg:
        status, result_msg = _classify_result_text(msg)
        if status != STATUS_UNKNOWN:
            return status, result_msg

    emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 실행", "INFO")
    return STATUS_SUCCESS, "점프 실행"


def lybam(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["PHPSESSID"]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    for k in cookie_keys:
        try:
            driver.delete_cookie(k)
        except Exception:
            pass
    try:
        driver.refresh()
    except Exception:
        pass

    load_cookies(driver, domain, user["id"], cookie_keys)
    try:
        driver.refresh()
    except Exception:
        pass
    _require_human_check(driver)

    def is_login() -> bool:
        return _wait_for_login_text(driver, "로그아웃", timeout_s=2.0)

    if not is_login():
        # 로봇 페이지인데 로그인으로 오분류되는 케이스 방지
        _require_human_check(driver)

        # 원본 로직: 로그인 링크 클릭 후 레이어 폼 입력
        for k in cookie_keys:
            try:
                driver.delete_cookie(k)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        try:
            wait = WebDriverWait(driver, 10)
            login_link = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "로그인")))
            login_link.click()
            time.sleep(0.5)
            _require_human_check(driver)

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".layer_login input[name='mb_id']"))
            )
            driver.find_element(By.CSS_SELECTOR, ".layer_login input[name='mb_id']").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, ".layer_login input[name='mb_password']").send_keys(user["pw"])
            driver.find_element(By.CSS_SELECTOR, ".layer_login input[type='submit']").click()
        except Exception:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"

        save_cookies(driver, domain, user["id"], cookie_keys)
        _require_human_check(driver)
        if not is_login():
            return STATUS_LOGIN_REQUIRED, "로그인 실패(확인 필요)"

    alert_texts = []
    try:
        jpbtn = driver.find_element(By.CSS_SELECTOR, "a.btn.btn-pink.btn-block")
        driver.execute_script("arguments[0].scrollIntoView(true);", jpbtn)
        driver.execute_script("arguments[0].click();", jpbtn)

        # alert 1 (confirm 또는 결과)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            a1 = driver.switch_to.alert
            alert_texts.append(a1.text)
            a1.accept()
        except Exception:
            pass
        time.sleep(0.5)
        # alert 2 (결과)
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            a2 = driver.switch_to.alert
            alert_texts.append(a2.text)
            a2.accept()
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"점프 실행 실패: {exc}"

    save_cookies(driver, domain, user["id"], cookie_keys)

    # alert 텍스트에서 결과 판정
    for txt in reversed(alert_texts):
        if txt:
            emit(f"[오피아트] 결과: {txt}", "DEBUG")
            status, msg = _classify_result_text(txt)
            if status != STATUS_UNKNOWN:
                return status, msg

    timer = _wait_for_countdown(driver, By, timeout_s=4.0)
    if timer:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} {timer}", "INFO")
        return STATUS_COOLDOWN, timer

    emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 실행", "INFO")
    return STATUS_SUCCESS, "점프 실행"


def kakaotteok(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    """카카오떡 점프 핸들러.

    로그인: /signin 페이지에서 #mb_id / #mb_password 폼 제출.
    점프: /ajaxinc AJAX POST (inc=dldjqthfmfwjqgmgkdufk&type=jump&jump_type=수동).
    응답 state: ok(성공), nonum(부족), jumpdateover(만기), nolog(미로그인), lackpoint(포인트부족), nobiz(업소아님).
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["PHPSESSID"]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    # 쿠키 주입
    for k in cookie_keys:
        try:
            driver.delete_cookie(k)
        except Exception:
            pass
    try:
        driver.refresh()
    except Exception:
        pass

    load_cookies(driver, domain, user["id"], cookie_keys)
    try:
        driver.refresh()
    except Exception:
        pass
    _require_human_check(driver)

    # 로그인 확인
    if not _wait_for_login_text(driver, "로그아웃", timeout_s=2.0):
        for k in cookie_keys:
            try:
                driver.delete_cookie(k)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        login_url = domain + "/signin"
        _goto(driver, login_url, via_script=False)
        time.sleep(0.5)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#mb_id")))
            driver.find_element(By.CSS_SELECTOR, "#mb_id").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, "#mb_password").send_keys(user["pw"])
            driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(1.0)
        _require_human_check(driver)
        if not _wait_for_login_text(driver, "로그아웃", timeout_s=4.0):
            return STATUS_LOGIN_REQUIRED, "로그인 실패(로그아웃 표시 없음)"
        save_cookies(driver, domain, user["id"], cookie_keys)

    # 점프 페이지로 이동
    _goto(driver, domain + "/mypage/myjump", via_script=False)
    time.sleep(1.0)
    _require_human_check(driver)

    # AJAX 점프 실행
    try:
        result_json = driver.execute_script("""
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
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"점프 AJAX 요청 실패: {exc}"

    save_cookies(driver, domain, user["id"], cookie_keys)

    if not result_json or not isinstance(result_json, dict):
        return STATUS_FAILED, "점프 응답 파싱 실패"

    state = result_json.get("state", "")
    cnt = result_json.get("cnt", "")

    if state == "ok":
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료 (잔여: {cnt})", "INFO")
        return STATUS_SUCCESS, f"점프 완료 (잔여: {cnt})"
    if state == "nonum":
        return STATUS_INSUFFICIENT, "점프 잔여 수량 없음"
    if state == "jumpdateover":
        return STATUS_INSUFFICIENT, "프리미엄 회원 만기"
    if state == "nolog":
        return STATUS_LOGIN_REQUIRED, "로그인 필요"
    if state == "lackpoint":
        return STATUS_INSUFFICIENT, "포인트 부족"
    if state == "nobiz":
        return STATUS_FAILED, "업소회원이 아닙니다"

    return STATUS_FAILED, f"점프 실패 (state={state})"


def opart(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    """오피아트 점프 핸들러.

    gnuboard 기반. 로그인: /bbs/login.php (#login_id / #login_pw).
    점프: 메인 페이지에서 onclick="jump(wr_id)" 찾아 wr_id 추출 후
    fetch('/bbs/list_jump.php?wr_id=...') 호출.
    응답 alert 텍스트로 결과 판별.
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]
    cookie_keys = ["PHPSESSID"]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    # 쿠키 주입
    for k in cookie_keys:
        try:
            driver.delete_cookie(k)
        except Exception:
            pass
    try:
        driver.refresh()
    except Exception:
        pass

    load_cookies(driver, domain, user["id"], cookie_keys)
    try:
        driver.refresh()
    except Exception:
        pass
    _require_human_check(driver)

    # 로그인 확인
    if not _wait_for_login_text(driver, "로그아웃", timeout_s=2.0):
        for k in cookie_keys:
            try:
                driver.delete_cookie(k)
            except Exception:
                pass
        try:
            driver.refresh()
        except Exception:
            pass

        login_url = domain + "/bbs/login.php"
        _goto(driver, login_url, via_script=False)
        time.sleep(0.5)
        _require_human_check(driver)

        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id")))
            driver.find_element(By.CSS_SELECTOR, "#login_id").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, "#login_pw").send_keys(user["pw"])
            driver.find_element(By.CSS_SELECTOR, "input.btn_submit[type='submit']").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(1.0)
        _require_human_check(driver)

        # 로그인 후 alert 처리 (쪽지 알림 등)
        try:
            from selenium.webdriver.common.alert import Alert
            WebDriverWait(driver, 2).until(EC.alert_is_present())
            Alert(driver).accept()
        except Exception:
            pass

        # swal2 팝업 처리
        try:
            swal_btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.swal2-confirm"))
            )
            swal_btn.click()
        except Exception:
            pass

        if not _wait_for_login_text(driver, "로그아웃", timeout_s=4.0):
            return STATUS_LOGIN_REQUIRED, "로그인 실패(로그아웃 표시 없음)"
        save_cookies(driver, domain, user["id"], cookie_keys)

    # 팝업 닫기
    try:
        popups = driver.find_elements(By.CSS_SELECTOR, "button.hd_pops_close")
        for p in popups:
            try:
                p.click()
            except Exception:
                pass
    except Exception:
        pass

    # 메인 페이지에서 wr_id 추출
    try:
        _goto(driver, domain, via_script=False)
        time.sleep(1.0)
        _require_human_check(driver)

        wr_id = driver.execute_script("""
            const el = document.querySelector('[onclick*="jump("]');
            if (!el) return null;
            const m = (el.getAttribute('onclick') || '').match(/jump\\((\\d+)\\)/);
            return m ? m[1] : null;
        """)
    except Exception:
        wr_id = None

    if not wr_id:
        return STATUS_FAILED, "점프 버튼(wr_id)을 찾을 수 없습니다"

    # fetch로 점프 실행 (alert 방지)
    try:
        response_text = driver.execute_script("""
            return (async () => {
                const r = await fetch('/bbs/list_jump.php?wr_id=""" + str(wr_id) + """&page=');
                return await r.text();
            })();
        """)
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"점프 요청 실패: {exc}"

    save_cookies(driver, domain, user["id"], cookie_keys)

    if not response_text:
        return STATUS_FAILED, "점프 응답 없음"

    # alert('...') 내용 추출
    alert_match = re.search(r"alert\(['\"](.+?)['\"]\)", response_text)
    alert_msg = alert_match.group(1) if alert_match else response_text.strip()

    if "완료" in alert_msg:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} {alert_msg}", "INFO")
        return STATUS_SUCCESS, alert_msg
    if "중지" in alert_msg:
        return STATUS_FAILED, alert_msg
    if "분에 한번" in alert_msg or "대기" in alert_msg:
        return STATUS_COOLDOWN, alert_msg
    if "횟수" in alert_msg and ("없" in alert_msg or "부족" in alert_msg):
        return STATUS_INSUFFICIENT, alert_msg
    if "회원만" in alert_msg or "로그인" in alert_msg:
        return STATUS_LOGIN_REQUIRED, alert_msg

    return STATUS_FAILED, f"점프 결과: {alert_msg}"


def bamminjok(driver: Any, user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    """밤의민족 점프 핸들러.

    커스텀 플랫폼(Vue.js + Fastify).
    로그인: /login 페이지, input[name='id'] / input[name='password'].
    점프: /page/broker/store 에서 store_id 추출 후
    POST /page/broker/store/{store_id}/jump → 'true'=성공.
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"Selenium import 실패: {exc}"

    domain = (user.get("domain") or "").strip()
    if domain.endswith("/"):
        domain = domain[:-1]

    _naver_warmup(driver, sleep_s=0.5)
    _goto(driver, domain, via_script=False)
    _require_human_check(driver)

    # 로그인 확인 (로그아웃 링크 존재 여부)
    logged_in = False
    try:
        els = driver.find_elements(By.XPATH, "//*[contains(@href,'/logout') or contains(text(),'로그아웃')]")
        logged_in = len(els) > 0
    except Exception:
        pass

    if not logged_in:
        login_url = domain + "/login"
        _goto(driver, login_url, via_script=False)
        time.sleep(1.0)
        _require_human_check(driver)

        # "세션 정보 확인 불가" 등 팝업/모달 닫기
        try:
            driver.execute_script("""
                // SweetAlert2
                var btn = document.querySelector('.swal2-confirm');
                if (btn) { btn.click(); return; }
                // 일반 모달 확인 버튼 (텍스트 기반)
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (t === '확인' || t === 'OK' || t === 'ok') { buttons[i].click(); return; }
                }
            """)
            time.sleep(0.5)
        except Exception:
            pass
        # JS alert 도 처리
        try:
            alert = driver.switch_to.alert
            alert.accept()
            time.sleep(0.5)
        except Exception:
            pass

        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='id']")))
            driver.find_element(By.CSS_SELECTOR, "input[name='id']").send_keys(user["id"])
            driver.find_element(By.CSS_SELECTOR, "input[name='password']").send_keys(user["pw"])

            # CAPTCHA 처리 (그림 문자 입력 필드가 보이는 경우)
            try:
                captcha_input = driver.find_element(By.CSS_SELECTOR, "input[name='captcha']")
                if captcha_input.is_displayed():
                    # 캡차 이미지를 찾아 2Captcha로 풀기
                    emit(f"[{user['name']}] 캡차가 감지되었습니다. 수동 입력이 필요할 수 있습니다.", "WARNING")
            except Exception:
                pass

            driver.find_element(By.CSS_SELECTOR, "#login_form_submit").click()
        except Exception:
            return STATUS_FAILED, "로그인 폼 입력/제출 실패"

        time.sleep(2.0)
        _require_human_check(driver)

        # 로그인 성공 확인 (메인 페이지로 이동 또는 로그아웃 링크 존재)
        try:
            cur_url = driver.current_url or ""
        except Exception:
            cur_url = ""
        if "/login" in cur_url:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"

    # store_id 추출: /page/broker/store 페이지에서 점프 설정 링크 찾기
    _goto(driver, domain + "/page/broker/store", via_script=False)
    time.sleep(2.0)
    _require_human_check(driver)

    try:
        store_id = driver.execute_script("""
            // 점프 설정 링크에서 store_id 추출
            const link = document.querySelector('a[href*="/store/"][href*="#jump"]');
            if (link) {
                const m = link.href.match(/\\/store\\/([a-f0-9]+)/);
                return m ? m[1] : null;
            }
            // Vue 데이터에서 추출 시도
            const el = document.querySelector('#kt_content');
            if (el && el.__vue__ && el.__vue__.storeList) {
                const stores = el.__vue__.storeList;
                if (stores.length > 0) return stores[0]._id;
            }
            return null;
        """)
    except Exception:
        store_id = None

    if not store_id:
        return STATUS_FAILED, "업소(store_id)를 찾을 수 없습니다"

    # 점프 실행: POST /page/broker/store/{store_id}/jump
    try:
        result = driver.execute_script("""
            return (async () => {
                const r = await fetch('/page/broker/store/""" + store_id + """/jump', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                const text = await r.text();
                return { status: r.status, body: text };
            })();
        """)
    except Exception as exc:  # noqa: BLE001
        return STATUS_FAILED, f"점프 API 요청 실패: {exc}"

    if not result or not isinstance(result, dict):
        return STATUS_FAILED, "점프 응답 파싱 실패"

    status_code = result.get("status", 0)
    body = (result.get("body") or "").strip()

    emit(f"[밤의민족] 점프 응답: HTTP {status_code}, body={body[:200]}", "DEBUG")

    if status_code == 200 and body == "true":
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"

    # JSON 응답인 경우 메시지/에러 파싱
    try:
        import json
        data = json.loads(body)
        msg = data.get("message") or data.get("msg") or data.get("error") or ""
        if msg:
            status, result_msg = _classify_result_text(msg)
            if status != STATUS_UNKNOWN:
                return status, result_msg
            return STATUS_FAILED, msg
    except Exception:
        pass

    if body == "false":
        return STATUS_COOLDOWN, "점프 실패 (서버 응답: false - 횟수 소진 또는 대기)"

    # 텍스트 응답에서 키워드 체크
    body_status, body_msg = _classify_result_text(body)
    if body_status != STATUS_UNKNOWN:
        return body_status, body_msg

    return STATUS_FAILED, f"점프 실패 (HTTP {status_code}, body={body[:100]})"


# ---------------------------------------------------------------------------
# 섹밤 (sexbam) – XpressEngine 기반, Cloudflare Turnstile 캡차
# ---------------------------------------------------------------------------

def _sexbam_solve_turnstile(driver: Any, emit: Callable[[str, str], None]) -> bool:
    """섹밤 로그인 폼의 Turnstile 캡차를 2Captcha로 해결하고 hidden 필드에 주입."""
    if not _captcha_api_key:
        emit("2Captcha API 키가 없어 Turnstile을 해결할 수 없습니다.", "ERROR")
        return False

    # sitekey 추출
    try:
        sitekey = driver.execute_script(
            "var el = document.querySelector('[data-sitekey]');"
            "return el ? el.getAttribute('data-sitekey') : null;"
        )
    except Exception:
        sitekey = None

    if not sitekey:
        emit("Turnstile sitekey를 찾을 수 없습니다.", "ERROR")
        return False

    emit(f"Turnstile sitekey 감지: {sitekey[:20]}... 2Captcha에 풀이 요청 중...", "INFO")

    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        emit("twocaptcha 패키지가 설치되지 않았습니다.", "ERROR")
        return False

    try:
        solver = TwoCaptcha(_captcha_api_key)
        result = solver.turnstile(
            sitekey=sitekey,
            url=driver.current_url,
        )
        token = result.get("code") if isinstance(result, dict) else result
    except Exception as exc:  # noqa: BLE001
        emit(f"Turnstile 풀이 실패: {exc}", "ERROR")
        return False

    if not token:
        emit("Turnstile 토큰을 받지 못했습니다.", "ERROR")
        return False

    emit("Turnstile 토큰 수신 완료. 폼에 주입합니다.", "INFO")

    # hidden 필드에 토큰 주입
    driver.execute_script("""
        var token = arguments[0];
        var cf = document.querySelector('input[name="cf-turnstile-response"]');
        var g = document.querySelector('input[name="g-recaptcha-response"]');
        if (cf) cf.value = token;
        if (g) g.value = token;
    """, token)
    return True


def sexbam(
    driver: Any,
    user: dict,
    emit: Callable[[str, str], None],
) -> tuple[str, str]:
    """섹밤 자동 점프 핸들러.

    XpressEngine 기반. 로그인 시 Cloudflare Turnstile 캡차 필수.
    점프: 출근부 게시물 상세 → doCallModuleAction('document_jump', 'procDocument_jumpDocumentUp', document_srl)
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = user["domain"].strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[섹밤] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) 로그인 ──
    login_url = f"{base}/index.php?mid=main_04&act=dispMemberLoginForm"
    driver.get(login_url)
    time.sleep(3)

    # Cloudflare 보안 페이지 처리 (Turnstile과 별도)
    _require_human_check(driver)

    # 로그인 폼 확인
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#uid")))
    except Exception:
        # 이미 로그인 상태인지 확인
        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" in src:
            emit("[섹밤] 이미 로그인된 상태입니다.", "INFO")
        else:
            return STATUS_FAILED, "로그인 폼을 찾을 수 없습니다."
    else:
        # 자격증명 입력
        uid_el = driver.find_element(By.CSS_SELECTOR, "#uid")
        pw_el = driver.find_element(By.CSS_SELECTOR, "#upw")
        uid_el.clear()
        uid_el.send_keys(uid)
        pw_el.clear()
        pw_el.send_keys(pw)

        # Turnstile 캡차 해결
        has_turnstile = driver.execute_script(
            "return !!document.querySelector('[data-sitekey]');"
        )
        if has_turnstile:
            if not _sexbam_solve_turnstile(driver, emit):
                return STATUS_FAILED, "Turnstile 캡차 해결 실패"

        # 로그인 제출
        try:
            driver.find_element(By.CSS_SELECTOR, "input.submit.btn").click()
        except Exception:
            driver.execute_script(
                "document.querySelector('#fo_member_login').submit();"
            )
        time.sleep(3)

        # 로그인 결과 확인
        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src:
            # 로봇 확인 메시지가 있는지 체크
            if "로봇" in src or "리캡차" in src:
                return STATUS_FAILED, "Turnstile 인증 실패 – 로봇 확인 메시지"
            return STATUS_LOGIN_REQUIRED, "로그인 실패 (ID/PW 확인 필요)"

        emit("[섹밤] 로그인 성공.", "INFO")

    # ── 2) 내 작성글 목록에서 document_srl 추출 ──
    own_doc_url = f"{base}/index.php?act=dispMemberOwnDocument&mid=sch"
    driver.get(own_doc_url)
    time.sleep(3)
    _require_human_check(driver)

    # 작성글 목록에서 번호 1번 게시글의 document_srl 추출
    # 테이블에 내 글(번호=숫자) + 댓글 단 글(번호=닉네임)이 혼재됨
    # 번호가 숫자인 행 중 가장 작은 번호(1번)를 선택
    document_srl = None
    try:
        document_srl = driver.execute_script("""
            var rows = document.querySelectorAll('table tbody tr');
            var candidates = [];
            for (var i = 0; i < rows.length; i++) {
                var tds = rows[i].querySelectorAll('td');
                if (!tds.length) continue;
                var numText = tds[0].textContent.trim();
                var num = parseInt(numText, 10);
                if (isNaN(num)) continue;  // 숫자가 아닌 행(댓글 단 글)은 건너뜀
                var link = rows[i].querySelector('td.title a[href]');
                if (!link) continue;
                var href = link.href || '';
                var m = href.match(/\\/([0-9]+)/);
                var srl = m ? m[1] : null;
                if (srl) candidates.push({num: num, srl: srl});
            }
            if (!candidates.length) return null;
            // 번호가 가장 작은 것(1번) 선택
            candidates.sort(function(a, b) { return a.num - b.num; });
            return candidates[0].srl;
        """)
    except Exception:
        pass

    if not document_srl:
        return STATUS_FAILED, "내 작성글에서 게시물을 찾을 수 없습니다. 출근부 게시글 등록 여부를 확인하세요."

    emit(f"[섹밤] document_srl 확인: {document_srl}", "INFO")

    # ── 3) 게시물 상세 페이지 이동 ──
    # /sch/document_srl 로 가면 실제 게시판으로 리다이렉트됨
    post_url = f"{base}/sch/{document_srl}"
    driver.get(post_url)
    time.sleep(3)
    _require_human_check(driver)

    # ── 4) 점프 버튼 확인 및 실행 ──
    try:
        jump_info = driver.execute_script("""
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
        return STATUS_FAILED, "상단점프 버튼을 찾을 수 없습니다. (제휴 등급 확인 필요)"

    before_text = jump_info.get("text", "")
    emit(f"[섹밤] 점프 버튼 발견: {before_text}", "INFO")

    # 남은 횟수 파싱
    count_match = re.search(r"\((\d+)\)", before_text)
    before_count = int(count_match.group(1)) if count_match else -1

    if before_count == 0:
        return STATUS_COOLDOWN, "오늘 점프 횟수를 모두 사용했습니다."

    # 점프 실행 (doCallModuleAction 호출)
    target_srl = jump_info.get("srl") or document_srl
    try:
        driver.execute_script(
            "doCallModuleAction('document_jump', 'procDocument_jumpDocumentUp', arguments[0]);",
            int(target_srl),
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[섹밤] 점프 호출 후 리로드 감지 (정상): {exc}", "DEBUG")

    # ── 5) 결과 확인: alert 처리 ──
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    alert_msg = ""
    try:
        WebDriverWait(driver, 5).until(EC.alert_is_present())
        alert_obj = driver.switch_to.alert
        alert_msg = alert_obj.text or ""
        alert_obj.accept()
        emit(f"[섹밤] 점프 결과 alert: {alert_msg}", "DEBUG")
    except Exception:
        pass

    if "올렸습니다" in alert_msg or "완료" in alert_msg or "성공" in alert_msg:
        emit(
            f"[성공] {user['name']} - {user['id']}: {user['startedAt']} "
            f"상단점프 완료 ({alert_msg})",
            "INFO",
        )
        return STATUS_SUCCESS, f"상단점프 완료"

    if "횟수" in alert_msg and ("없" in alert_msg or "초과" in alert_msg or "소진" in alert_msg):
        return STATUS_COOLDOWN, alert_msg

    if "분" in alert_msg and ("대기" in alert_msg or "한번" in alert_msg):
        return STATUS_COOLDOWN, alert_msg

    if alert_msg:
        return STATUS_FAILED, f"점프 결과: {alert_msg}"

    # alert 없이 페이지 리로드된 경우 – 버튼 횟수 비교로 판단
    time.sleep(3)
    try:
        after_text = driver.execute_script("""
            var btn = document.querySelector('a[onclick*="procDocument_jumpDocumentUp"]');
            return btn ? btn.textContent.trim() : '';
        """)
    except Exception:
        after_text = ""

    after_match = re.search(r"\((\d+)\)", after_text)
    after_count = int(after_match.group(1)) if after_match else -1

    if before_count > 0 and after_count >= 0 and after_count < before_count:
        emit(
            f"[성공] {user['name']} - {user['id']}: {user['startedAt']} "
            f"상단점프 완료 (남은 횟수: {after_count})",
            "INFO",
        )
        return STATUS_SUCCESS, f"상단점프 완료 (남은 횟수: {after_count})"

    if after_count == 0:
        return STATUS_COOLDOWN, "점프 횟수 소진"

    # 횟수 변화를 확인 못 했지만 에러도 없으면 성공으로 간주
    if after_text and "점프" in after_text:
        emit(
            f"[성공] {user['name']} - {user['id']}: {user['startedAt']} "
            f"상단점프 실행됨 ({after_text})",
            "INFO",
        )
        return STATUS_SUCCESS, f"상단점프 실행됨 ({after_text})"

    return STATUS_FAILED, f"점프 결과 확인 실패 (before={before_text}, after={after_text})"


# ---------------------------------------------------------------------------
# 밤의제국 (bamje) – gnuboard 기반, 브라우저 핸들러
# ---------------------------------------------------------------------------

def bamje(
    driver: Any,
    user: dict,
    emit: Callable[[str, str], None],
) -> tuple[str, str]:
    """밤의제국 자동 점프 (브라우저).

    gnuboard 기반. 로그인 후 /bbs/jump.php 호출.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = user["domain"].strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[밤의제국] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) 로그인 ──
    driver.get(f"{base}/bbs/login.php")
    time.sleep(2)
    _require_human_check(driver)

    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    if "로그아웃" in src:
        emit("[밤의제국] 이미 로그인 상태.", "INFO")
    else:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id"))
            )
        except Exception:
            return STATUS_FAILED, "로그인 폼을 찾을 수 없습니다."

        try:
            id_el = driver.find_element(By.CSS_SELECTOR, "#login_id")
            pw_el = driver.find_element(By.CSS_SELECTOR, "#login_pw")
            id_el.clear(); id_el.send_keys(uid)
            pw_el.clear(); pw_el.send_keys(pw)
        except Exception as exc:
            return STATUS_FAILED, f"로그인 입력 실패: {exc}"

        # 메인 로그인 폼의 버튼 클릭 (사이드바 폼이 아닌 #login_id가 속한 폼)
        try:
            form = id_el.find_element(By.XPATH, "./ancestor::form")
            btn = form.find_element(By.CSS_SELECTOR, "button[type='submit']")
            btn.click()
        except Exception:
            driver.execute_script("document.getElementById('login_id').closest('form').submit();")
        time.sleep(3)
        _require_human_check(driver)

        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        emit("[밤의제국] 로그인 성공.", "INFO")

    # ── 2) 점프 실행 ──
    driver.get(f"{base}/bbs/jump.php")

    # jump.php는 alert로 결과를 반환하므로 alert를 먼저 대기
    alert_text = ""
    try:
        WebDriverWait(driver, 10).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        alert_text = alert.text
        alert.accept()
        emit(f"[밤의제국] Alert: {alert_text}", "INFO")
    except Exception:
        # alert 없으면 페이지 소스에서 파싱 시도
        time.sleep(2)
        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        import re as _re
        m = _re.search(r'alert\(["\'](.+?)["\']\)', src)
        alert_text = m.group(1) if m else ""

    if "완료" in alert_text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if "5분" in alert_text or "분에 한번" in alert_text:
        return STATUS_COOLDOWN, alert_text
    if "본인" in alert_text:
        return STATUS_FAILED, alert_text
    if "회원" in alert_text or "로그인" in alert_text:
        return STATUS_LOGIN_REQUIRED, alert_text

    return STATUS_FAILED, f"점프 실패 (응답: {alert_text or '확인불가'})"


# ---------------------------------------------------------------------------
# 오피나라 (opnara) – gnuboard 기반, 브라우저 핸들러
# ---------------------------------------------------------------------------

def opnara(
    driver: Any,
    user: dict,
    emit: Callable[[str, str], None],
) -> tuple[str, str]:
    """오피나라 자동 점프 (브라우저).

    gnuboard 기반. 마이페이지에서 wr_id 추출 → /jump.php?wr_id= 호출.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = user["domain"].strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[오피나라] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) 로그인 ──
    driver.get(f"{base}/bbs/login.php")
    time.sleep(2)
    _require_human_check(driver)

    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    if "로그아웃" in src:
        emit("[오피나라] 이미 로그인 상태.", "INFO")
    else:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id, input[name='mb_id']"))
            )
        except Exception:
            return STATUS_FAILED, "로그인 폼을 찾을 수 없습니다."

        try:
            id_el = driver.find_element(By.CSS_SELECTOR, "#login_id, input[name='mb_id']")
            pw_el = driver.find_element(By.CSS_SELECTOR, "#login_pw, input[name='mb_password']")
            id_el.clear(); id_el.send_keys(uid)
            pw_el.clear(); pw_el.send_keys(pw)
        except Exception as exc:
            return STATUS_FAILED, f"로그인 입력 실패: {exc}"

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], .btn_submit")
            btn.click()
        except Exception:
            driver.execute_script("document.querySelector('form').submit();")
        time.sleep(3)
        _require_human_check(driver)

        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src and "마이페이지" not in src:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        emit("[오피나라] 로그인 성공.", "INFO")

    # ── 2) 마이페이지에서 wr_id 추출 ──
    driver.get(f"{base}/bbs/mypage.php")
    time.sleep(2)

    wr_id = driver.execute_script("""
        // 1) onclick="jump1('wr_id')" 패턴에서 추출
        var links = document.querySelectorAll('a[onclick*="jump"]');
        for (var i = 0; i < links.length; i++) {
            var oc = links[i].getAttribute('onclick') || '';
            var m = oc.match(/jump\\w*\\(['"]?(\\d+)['"]?\\)/);
            if (m) return m[1];
        }
        // 2) 출근부 수정하기 링크에서 wr_id 추출
        var allLinks = document.querySelectorAll('a[href]');
        for (var j = 0; j < allLinks.length; j++) {
            var a = allLinks[j];
            if (a.textContent.includes('출근부') && a.href.includes('wr_id=')) {
                var m2 = a.href.match(/wr_id=(\\d+)/);
                if (m2) return m2[1];
            }
        }
        return null;
    """)

    if not wr_id:
        return STATUS_FAILED, "wr_id를 찾을 수 없습니다. (업소 등록 확인)"

    emit(f"[오피나라] wr_id: {wr_id}", "INFO")

    # ── 3) 점프 실행 (confirm 건너뛰고 직접 GET) ──
    driver.get(f"{base}/jump.php?wr_id={wr_id}")

    # cuteAlert로 결과를 표시하므로 페이지 로드 대기
    time.sleep(3)

    # 일반 alert 먼저 확인
    alert_text = ""
    try:
        WebDriverWait(driver, 5).until(EC.alert_is_present())
        alert_obj = driver.switch_to.alert
        alert_text = alert_obj.text
        alert_obj.accept()
    except Exception:
        pass

    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    result_text = alert_text or src

    if "점프가 완료" in result_text or "완료" in result_text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if "5분" in result_text or "분에 한번" in result_text:
        return STATUS_COOLDOWN, "5분 대기 룰"
    if "횟수" in result_text and ("없" in result_text or "부족" in result_text):
        return STATUS_INSUFFICIENT, "점프 횟수 부족"
    if "회원만" in result_text or "로그인" in result_text:
        return STATUS_LOGIN_REQUIRED, "로그인 필요"

    return STATUS_FAILED, f"점프 실패 (응답 확인 불가)"


# ---------------------------------------------------------------------------
# 오피러브 (oplove) – gnuboard 기반, 브라우저 핸들러
# ---------------------------------------------------------------------------

def oplove(
    driver: Any,
    user: dict,
    emit: Callable[[str, str], None],
) -> tuple[str, str]:
    """오피러브 자동 점프 (브라우저).

    gnuboard 기반. /bbs/board.php?bo_table=shop 에서 wr_id 추출 → /run.jump.php 호출.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = user["domain"].strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[오피러브] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) 로그인 ──
    driver.get(f"{base}/bbs/login.php")
    time.sleep(2)
    _require_human_check(driver)

    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    if "로그아웃" in src:
        emit("[오피러브] 이미 로그인 상태.", "INFO")
    else:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#login_id, input[name='mb_id']"))
            )
        except Exception:
            return STATUS_FAILED, "로그인 폼을 찾을 수 없습니다."

        try:
            id_el = driver.find_element(By.CSS_SELECTOR, "#login_id, input[name='mb_id']")
            pw_el = driver.find_element(By.CSS_SELECTOR, "#login_pw, input[name='mb_password']")
            id_el.clear(); id_el.send_keys(uid)
            pw_el.clear(); pw_el.send_keys(pw)
        except Exception as exc:
            return STATUS_FAILED, f"로그인 입력 실패: {exc}"

        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], .btn_submit")
            btn.click()
        except Exception:
            driver.execute_script("document.querySelector('form').submit();")
        time.sleep(3)
        _require_human_check(driver)

        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        emit("[오피러브] 로그인 성공.", "INFO")

    # ── 2) shop 페이지에서 wr_id 추출 ──
    driver.get(f"{base}/bbs/board.php?bo_table=shop")
    time.sleep(2)

    wr_id = driver.execute_script("""
        var btn = document.querySelector('.jump_btn[onclick]');
        if (btn) {
            var m = btn.getAttribute('onclick').match(/['\"](\\d+)['\"]/);
            return m ? m[1] : null;
        }
        return null;
    """)

    if not wr_id:
        return STATUS_FAILED, "wr_id를 찾을 수 없습니다. (업소 등록 확인)"

    emit(f"[오피러브] wr_id: {wr_id}", "INFO")

    # ── 3) 점프 실행 ──
    driver.get(f"{base}/run.jump.php?wr_id={wr_id}")

    # alert로 결과를 반환하므로 alert를 먼저 대기
    alert_text = ""
    try:
        WebDriverWait(driver, 10).until(EC.alert_is_present())
        alert_obj = driver.switch_to.alert
        alert_text = alert_obj.text
        alert_obj.accept()
        emit(f"[오피러브] Alert: {alert_text}", "INFO")
    except Exception:
        time.sleep(2)
        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        import re as _re
        m = _re.search(r'alert\(["\'](.+?)["\']\)', src)
        alert_text = m.group(1) if m else ""

    if "완료" in alert_text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if "10분" in alert_text or "분에 한번" in alert_text:
        return STATUS_COOLDOWN, alert_text
    if "회원만" in alert_text:
        return STATUS_LOGIN_REQUIRED, alert_text

    return STATUS_FAILED, f"점프 실패 (응답: {alert_text or '확인불가'})"


# ---------------------------------------------------------------------------
# 오피마트 (opmart) – 커스텀 플랫폼, 브라우저 핸들러
# ---------------------------------------------------------------------------

def opmart(
    driver: Any,
    user: dict,
    emit: Callable[[str, str], None],
) -> tuple[str, str]:
    """오피마트 자동 점프 (브라우저).

    커스텀 플랫폼. AJAX 로그인 후 /c_member/jump_proc.php POST.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    domain = user["domain"].strip().rstrip("/")
    base = f"https://{domain}" if not domain.startswith("http") else domain
    uid = user["id"].strip()
    pw = user["pw"].strip()

    emit(f"[오피마트] 시작: {base} (ID: {uid})", "INFO")

    # ── 1) naver 선방문 후 사이트 이동 ──
    driver.get("https://naver.com")
    time.sleep(1)
    driver.get(base)
    time.sleep(2)
    _require_human_check(driver)

    # ── 2) AJAX 로그인 (login_proc.php) ──
    try:
        src = driver.page_source or ""
    except Exception:
        src = ""

    if "로그아웃" in src or "logout" in src.lower():
        emit("[오피마트] 이미 로그인 상태.", "INFO")
    else:
        # login_proc.php에 AJAX POST (원본 패턴)
        login_result = driver.execute_script(f"""
            return (async () => {{
                try {{
                    const r = await fetch('/login/login_proc.php', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        }},
                        body: 'act=login&referer=/&user_id={uid}&user_pw={pw}'
                    }});
                    const text = await r.text();
                    return {{ status: r.status, body: text, url: r.url }};
                }} catch(e) {{
                    return {{ status: 0, body: e.toString() }};
                }}
            }})();
        """)

        emit(f"[오피마트] 로그인 응답: {login_result}", "DEBUG")

        # 페이지 새로고침하여 로그인 상태 반영
        driver.get(base)
        time.sleep(2)

        # alert 처리 (예: "제휴 마감 N일 남았습니다.")
        try:
            alert_obj = driver.switch_to.alert
            emit(f"[오피마트] Alert: {alert_obj.text}", "DEBUG")
            alert_obj.accept()
            time.sleep(1)
        except Exception:
            pass

        try:
            src = driver.page_source or ""
        except Exception:
            src = ""
        if "로그아웃" not in src and "logout" not in src.lower():
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        emit("[오피마트] 로그인 성공.", "INFO")

    # ── 2) 점프 실행 (AJAX POST) ──
    result = driver.execute_script("""
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
        return STATUS_FAILED, "점프 응답 없음"

    status_code = result.get("status", 0)
    body = (result.get("body") or "").strip()

    emit(f"[오피마트] 점프 응답: HTTP {status_code}, body={body[:100]}", "DEBUG")

    try:
        import json
        data = json.loads(body)
        if data.get("result"):
            emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
            return STATUS_SUCCESS, "점프 완료"
        msg = data.get("message", "")
        if "10분" in msg or "대기" in msg:
            return STATUS_COOLDOWN, msg or "10분 대기"
        if "부족" in msg or "횟수" in msg:
            return STATUS_INSUFFICIENT, msg or "횟수 부족"
        return STATUS_FAILED, msg or "점프 실패"
    except Exception:
        pass

    if status_code == 200 and '"result":true' in body:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"

    return STATUS_FAILED, f"점프 실패 (HTTP {status_code})"
