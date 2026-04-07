"""Cloudflare Turnstile 캡차 자동 해결 (2Captcha 기반).

디컴파일된 원본 OpJumFun.py의 robotPass/solver_captcha 로직을 이식 + 개선.
Cloudflare managed challenge 지원 강화.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

_ROBOT_TEXTS = (
    "아래 작업을 완료하여 사람인지 확인하십시오.",
    "사람인지 확인하십시오",
    "사람인지 확인하는 중입니다",
    "사람인지 확인하는 중입니다.",
    "보안 확인 수행 중",
)

# CDP로 페이지 로드 전에 주입하는 인터셉트 스크립트
_INTERCEPT_SCRIPT = """
console.clear = () => console.log('Console was cleared');
const i = setInterval(()=>{
    if (window.turnstile) {
        clearInterval(i);
        const _origRender = window.turnstile.render;
        window.turnstile.render = (a, b) => {
            let params = {
                sitekey: b.sitekey,
                pageurl: window.location.href,
                data: b.cData,
                pagedata: b.chlPageData,
                action: b.action,
                userAgent: navigator.userAgent,
            };
            window.interceptedParams = params;
            window.cfCallback = b.callback;
            console.log('intercepted-params:' + JSON.stringify(params));
            return _origRender.call(window.turnstile, a, b);
        };
    }
}, 10);
"""


def is_robot_page(driver: Any) -> bool:
    """현재 페이지가 로봇/캡차 확인 페이지인지 감지."""
    try:
        src = driver.page_source or ""
    except Exception:
        return False

    return any(t in src for t in _ROBOT_TEXTS)


def _extract_sitekey_from_dom(driver: Any) -> str | None:
    """DOM에서 Turnstile sitekey를 직접 추출 (인터셉트 없이)."""
    try:
        src = driver.page_source or ""
    except Exception:
        return None

    # 1) data-sitekey 속성에서 추출
    m = re.search(r'data-sitekey=["\']([0-9xA-Za-z_-]+)["\']', src)
    if m:
        return m.group(1)

    # 2) JS 코드에서 sitekey 추출 (turnstile.render 호출 인자 등)
    patterns = [
        r"sitekey\s*[:=]\s*['\"]([0-9xA-Za-z_-]+)['\"]",
        r"turnstile[^}]*sitekey[^'\"]*['\"]([0-9xA-Za-z_-]+)['\"]",
    ]
    for pat in patterns:
        m = re.search(pat, src)
        if m:
            return m.group(1)

    # 3) iframe src에서 추출
    m = re.search(
        r'challenges\.cloudflare\.com/cdn-cgi/challenge-platform[^"]*sitekey=([^&"]+)',
        src,
    )
    if m:
        return m.group(1)

    # 4) Turnstile API 스크립트 URL에서 추출
    #    예: challenges.cloudflare.com/turnstile/v0/g/{sitekey}/api.js
    m = re.search(
        r'challenges\.cloudflare\.com/turnstile/v0/g/([0-9a-fA-F]+)/api\.js',
        src,
    )
    if m:
        return m.group(1)

    # 5) 0x로 시작하는 Turnstile sitekey 패턴
    m = re.search(r'["\']?(0x[A-Za-z0-9_-]{20,})["\']?', src)
    if m:
        return m.group(1)

    # 6) Selenium으로 DOM 요소 직접 탐색
    try:
        el = driver.find_element("css selector", "[data-sitekey]")
        sk = el.get_attribute("data-sitekey")
        if sk:
            return sk
    except Exception:
        pass

    # 7) iframe 내부 탐색
    try:
        iframes = driver.find_elements(
            "css selector",
            "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare']",
        )
        for iframe in iframes:
            iframe_src = iframe.get_attribute("src") or ""
            m = re.search(r'sitekey=([^&]+)', iframe_src)
            if m:
                return m.group(1)
    except Exception:
        pass

    # 8) JS로 Turnstile 스크립트 src에서 추출
    try:
        sk = driver.execute_script("""
            var scripts = document.querySelectorAll('script[src*="turnstile"]');
            for (var i = 0; i < scripts.length; i++) {
                var m = scripts[i].src.match(/\\/g\\/([0-9a-fA-F]+)\\/api\\.js/);
                if (m) return m[1];
            }
            return null;
        """)
        if sk:
            return sk
    except Exception:
        pass

    return None


def _try_click_checkbox(driver: Any, emit: Callable | None = None) -> bool:
    """Turnstile 체크박스 클릭 시도 (non-interactive challenge는 자동 통과)."""
    _log = emit or (lambda msg, level: None)

    try:
        # Turnstile iframe 찾기
        iframes = driver.find_elements(
            "css selector",
            "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare']",
        )
        if not iframes:
            # managed challenge의 경우 iframe이 없을 수 있음
            # 직접 체크박스 영역 클릭 시도
            try:
                checkbox = driver.find_element(
                    "css selector",
                    "#cf-turnstile-wrapper, .cf-turnstile, [data-cf-turnstile-response]",
                )
                checkbox.click()
                _log("Turnstile 체크박스 영역 클릭.", "DEBUG")
                time.sleep(3)
                return not is_robot_page(driver)
            except Exception:
                pass
            return False

        for iframe in iframes:
            try:
                driver.switch_to.frame(iframe)
                # iframe 내부 체크박스 클릭
                try:
                    cb = driver.find_element(
                        "css selector",
                        "input[type='checkbox'], .ctp-checkbox-label, #cf-stage",
                    )
                    cb.click()
                    _log("Turnstile iframe 체크박스 클릭.", "DEBUG")
                except Exception:
                    # 클릭 가능 영역 전체 클릭
                    body = driver.find_element("css selector", "body")
                    body.click()
                driver.switch_to.default_content()
                time.sleep(3)
                if not is_robot_page(driver):
                    return True
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
    except Exception:
        pass

    return False


def _get_captcha_params_cdp(driver: Any) -> dict | None:
    """CDP를 사용하여 페이지 로드 전에 인터셉트 스크립트를 주입하고 파라미터 추출."""
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _INTERCEPT_SCRIPT},
        )
    except Exception:
        return _get_captcha_params_fallback(driver)

    try:
        url = driver.current_url
        driver.get(url)
    except Exception:
        return None

    # 파라미터 폴링 (최대 20초 - managed challenge는 느릴 수 있음)
    end = time.time() + 20.0
    while time.time() < end:
        time.sleep(0.5)
        try:
            params = driver.execute_script("return window.interceptedParams;")
            if params and params.get("sitekey"):
                return params
        except Exception:
            pass

    return None


def _get_captcha_params_fallback(driver: Any) -> dict | None:
    """인터셉트 실패 시 DOM에서 sitekey 추출하여 파라미터 구성."""
    sitekey = _extract_sitekey_from_dom(driver)
    if not sitekey:
        return None

    try:
        url = driver.current_url
    except Exception:
        url = ""

    try:
        ua = driver.execute_script("return navigator.userAgent;")
    except Exception:
        ua = ""

    return {
        "sitekey": sitekey,
        "pageurl": url,
        "userAgent": ua,
    }


def _solve_captcha(
    params: dict, api_key: str, emit: Callable | None = None
) -> str | None:
    """2Captcha API로 Turnstile 캡차를 풀고 토큰 반환."""
    _log = emit or (lambda msg, level: None)

    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        _log("twocaptcha 패키지가 설치되지 않았습니다.", "ERROR")
        return None

    try:
        solver = TwoCaptcha(api_key)
        sk = params["sitekey"]
        _log(f"2Captcha Turnstile 요청: sitekey={sk[:20]}...", "DEBUG")
        result = solver.turnstile(
            sitekey=sk,
            url=params["pageurl"],
            action=params.get("action"),
            data=params.get("data"),
            pagedata=params.get("pagedata"),
            useragent=params.get("userAgent"),
        )
        return result["code"]
    except Exception as exc:
        _log(f"2Captcha 풀이 실패: {exc}", "ERROR")
        return None


def _inject_token(driver: Any, token: str, emit: Callable | None = None) -> bool:
    """풀린 캡차 토큰을 브라우저에 전달 (여러 방법 시도)."""
    _log = emit or (lambda msg, level: None)
    success = False

    # 방법 1: cfCallback 호출 (인터셉트로 캡처한 콜백)
    try:
        result = driver.execute_script(
            "if(typeof window.cfCallback==='function')"
            "{window.cfCallback(arguments[0]);return true;}return false;",
            token,
        )
        if result:
            _log("cfCallback으로 토큰 전달 성공.", "DEBUG")
            success = True
    except Exception:
        pass

    # 방법 2: Turnstile 숨김 필드에 토큰 직접 주입
    try:
        driver.execute_script(
            """
            var token = arguments[0];
            var selectors = [
                'input[name="cf-turnstile-response"]',
                'input[name="g-recaptcha-response"]',
                'input[name="h-captcha-response"]',
                'input[id*="cf-chl-widget"][id*="response"]',
            ];
            selectors.forEach(function(sel) {
                document.querySelectorAll(sel).forEach(function(f) {
                    f.value = token;
                });
            });
            document.querySelectorAll('[data-sitekey]').forEach(function(c) {
                var inp = c.querySelector('input[type="hidden"]');
                if (inp) inp.value = token;
            });
            """,
            token,
        )
        _log("숨김 필드에 토큰 주입 완료.", "DEBUG")
        success = True
    except Exception:
        pass

    # 방법 3: turnstile.getResponse를 오버라이드
    try:
        driver.execute_script(
            """
            if (window.turnstile) {
                window.turnstile.getResponse = function() { return arguments[0]; };
            }
            """,
            token,
        )
    except Exception:
        pass

    # 방법 4: Cloudflare 챌린지 폼 제출
    try:
        driver.execute_script(
            """
            var token = arguments[0];
            var forms = document.querySelectorAll(
                'form[action*="cdn-cgi"], form.challenge-form, form[id*="challenge"]'
            );
            if (forms.length === 0) {
                // managed challenge: 숨김 폼 찾기
                forms = document.querySelectorAll('form');
            }
            forms.forEach(function(form) {
                var inputs = form.querySelectorAll('input[type="hidden"]');
                inputs.forEach(function(inp) {
                    if (inp.name.includes('turnstile') || inp.name.includes('cf-')
                        || inp.name.includes('captcha') || inp.name.includes('response')) {
                        inp.value = token;
                    }
                });
                form.submit();
            });
            """,
            token,
        )
    except Exception:
        pass

    return success


def robot_pass(
    driver: Any,
    api_key: str,
    emit: Callable[[str, str], None] | None = None,
) -> bool:
    """로봇/캡차 페이지를 2Captcha로 자동 통과.

    Returns:
        True: 통과 성공 (또는 캡차 페이지가 아님)
        False: 통과 실패 (수동 개입 필요)
    """
    _log = emit or (lambda msg, level: None)

    if not is_robot_page(driver):
        return True

    _log("캡차/보안 페이지가 감지되었습니다. 자동 해결을 시도합니다...", "WARNING")

    if not api_key:
        _log("2Captcha API 키가 설정되지 않았습니다.", "ERROR")
        return False

    # ── 1단계: 자동 통과 대기 (Cloudflare JS challenge는 보통 5~10초 내 통과) ──
    _log("Cloudflare 자동 통과 대기 중... (최대 10초)", "INFO")
    for _ in range(10):
        time.sleep(1)
        if not is_robot_page(driver):
            _log("Cloudflare 보안 자동 통과 완료.", "INFO")
            return True

    # ── 1.5단계: 체크박스 클릭 시도 ──
    _log("체크박스 클릭 시도...", "INFO")
    if _try_click_checkbox(driver, _log):
        _log("체크박스 클릭으로 통과 성공.", "INFO")
        return True

    # ── 2단계: 아직 캡차 페이지 → Turnstile 풀기 시도 ──
    _log("자동 통과 실패. Turnstile 캡차 풀이를 시도합니다...", "INFO")

    # 2-a) CDP 인터셉트 방식 시도 (가장 정확한 sitekey 추출)
    _log("CDP 인터셉트로 sitekey 추출 시도...", "INFO")
    params = _get_captcha_params_cdp(driver)
    if params and params.get("sitekey"):
        _log(f"CDP 인터셉트 성공: sitekey={params['sitekey'][:20]}...", "INFO")
    else:
        # 2-b) DOM에서 sitekey 직접 추출 시도
        _log("CDP 실패. DOM에서 sitekey 추출 시도...", "INFO")
        params = _get_captcha_params_fallback(driver)
        if params:
            _log(f"DOM에서 sitekey 추출 성공: {params['sitekey'][:20]}...", "INFO")

    if not params or not params.get("sitekey"):
        _log("캡차 파라미터 추출 실패. (sitekey를 찾을 수 없음)", "ERROR")
        return False

    _log("캡차 파라미터 추출 완료. 2Captcha에 풀이를 요청합니다...", "INFO")

    # ── 3단계: 2Captcha로 풀기 ──
    token = _solve_captcha(params, api_key, _log)
    if not token:
        _log("캡차 풀이 실패.", "ERROR")
        return False

    _log("캡차 풀이 완료! 토큰을 브라우저에 전달합니다...", "INFO")

    # ── 4단계: 토큰 전달 ──
    _inject_token(driver, token, _log)

    # 토큰 전달 후 대기
    time.sleep(3)

    # ── 5단계: 통과 확인 ──
    if is_robot_page(driver):
        _log("토큰 전달 후 추가 대기...", "DEBUG")
        time.sleep(5)

    if is_robot_page(driver):
        try:
            driver.refresh()
        except Exception:
            pass
        time.sleep(3)

    if is_robot_page(driver):
        _log("캡차 풀이 후에도 로봇 페이지가 유지됩니다.", "ERROR")
        return False

    _log("캡차 자동 통과 성공!", "INFO")
    return True
