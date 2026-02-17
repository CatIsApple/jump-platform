"""Cloudflare Turnstile 캡차 자동 해결 (2Captcha 기반).

디컴파일된 원본 OpJumFun.py의 robotPass/solver_captcha 로직을 이식 + 개선.
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
        console.log('success!!');
        clearInterval(i);
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
            console.log('intercepted-params:' + JSON.stringify(params));
            window.cfCallback = b.callback;
            return;
        }
    }
}, 50);
"""


def is_robot_page(driver: Any) -> bool:
    """현재 페이지가 로봇/캡차 확인 페이지인지 감지.

    원본 robotPass 로직과 동일하게 Cloudflare 한국어 챌린지 텍스트만 체크.
    """
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
    m = re.search(r'data-sitekey=["\']([0-9x A-Za-z_-]+)["\']', src)
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
    m = re.search(r'challenges\.cloudflare\.com/cdn-cgi/challenge-platform[^"]*sitekey=([^&"]+)', src)
    if m:
        return m.group(1)

    # 4) Selenium으로 DOM 요소 직접 탐색
    try:
        el = driver.find_element("css selector", "[data-sitekey]")
        sk = el.get_attribute("data-sitekey")
        if sk:
            return sk
    except Exception:
        pass

    # 5) iframe 내부 탐색
    try:
        iframes = driver.find_elements("css selector", "iframe[src*='turnstile'], iframe[src*='challenges.cloudflare']")
        for iframe in iframes:
            iframe_src = iframe.get_attribute("src") or ""
            m = re.search(r'sitekey=([^&]+)', iframe_src)
            if m:
                return m.group(1)
    except Exception:
        pass

    return None


def _get_captcha_params_cdp(driver: Any) -> dict | None:
    """CDP를 사용하여 페이지 로드 전에 인터셉트 스크립트를 주입하고 파라미터 추출."""
    try:
        # CDP 명령으로 페이지 로드 전 스크립트 등록
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _INTERCEPT_SCRIPT},
        )
    except Exception:
        # CDP 미지원 시 폴백
        return _get_captcha_params_fallback(driver)

    try:
        url = driver.current_url
        driver.get(url)  # 같은 URL 새로 로드 (인터셉트 활성 상태)
    except Exception:
        return None

    # 파라미터 폴링 (최대 10초)
    end = time.time() + 10.0
    while time.time() < end:
        time.sleep(0.5)
        try:
            params = driver.execute_script("return window.interceptedParams;")
            if params:
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


def _solve_captcha(params: dict, api_key: str, emit: Callable | None = None) -> str | None:
    """2Captcha API로 Turnstile 캡차를 풀고 토큰 반환."""
    _log = emit or (lambda msg, level: None)

    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        _log("twocaptcha 패키지가 설치되지 않았습니다.", "ERROR")
        return None

    try:
        solver = TwoCaptcha(api_key)
        _log(f"2Captcha 요청: sitekey={params['sitekey'][:20]}...", "DEBUG")
        result = solver.turnstile(
            sitekey=params["sitekey"],
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
            f"if(typeof window.cfCallback==='function'){{window.cfCallback('{token}');return true;}}return false;"
        )
        if result:
            _log("cfCallback으로 토큰 전달 성공.", "DEBUG")
            success = True
    except Exception:
        pass

    # 방법 2: Turnstile 숨김 필드에 토큰 직접 주입
    try:
        driver.execute_script(f"""
            var fields = document.querySelectorAll(
                'input[name="cf-turnstile-response"], input[name="g-recaptcha-response"], input[name="h-captcha-response"]'
            );
            fields.forEach(function(f) {{ f.value = '{token}'; }});
            var containers = document.querySelectorAll('[data-sitekey]');
            containers.forEach(function(c) {{
                var inp = c.querySelector('input[type="hidden"]');
                if (inp) inp.value = '{token}';
            }});
        """)
        _log("숨김 필드에 토큰 주입 완료.", "DEBUG")
        success = True
    except Exception:
        pass

    # 방법 3: turnstile.getResponse를 오버라이드하여 토큰 반환하도록
    try:
        driver.execute_script(f"""
            if (window.turnstile) {{
                window.turnstile.getResponse = function() {{ return '{token}'; }};
            }}
        """)
    except Exception:
        pass

    # 방법 4: Cloudflare 챌린지 폼 제출
    try:
        driver.execute_script(f"""
            var forms = document.querySelectorAll('form[action*="cdn-cgi"], form.challenge-form');
            forms.forEach(function(form) {{
                var inp = form.querySelector('input[type="hidden"]');
                if (inp) {{ inp.value = '{token}'; form.submit(); }}
            }});
        """)
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

    # ── 1단계: 자동 통과 대기 (Cloudflare JS challenge는 보통 10~15초 내 통과) ──
    _log("Cloudflare 자동 통과 대기 중... (최대 15초)", "INFO")
    for i in range(15):
        time.sleep(1)
        if not is_robot_page(driver):
            _log("Cloudflare 보안 자동 통과 완료.", "INFO")
            return True

    # ── 2단계: 아직 캡차 페이지 → Turnstile 풀기 시도 ──
    _log("자동 통과 실패. Turnstile 캡차 풀이를 시도합니다...", "INFO")

    # 2-a) DOM에서 sitekey 직접 추출 시도 (빠르고 안정적)
    params = _get_captcha_params_fallback(driver)
    if params:
        _log(f"DOM에서 sitekey 추출 성공: {params['sitekey'][:20]}...", "INFO")
    else:
        # 2-b) CDP 인터셉트 방식 시도
        _log("DOM 추출 실패. CDP 인터셉트 방식 시도...", "INFO")
        params = _get_captcha_params_cdp(driver)

    if not params:
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
    # 페이지가 아직 챌린지면 폼 제출 + 새로고침 시도
    if is_robot_page(driver):
        _log("토큰 전달 후 추가 대기...", "DEBUG")
        time.sleep(5)

    if is_robot_page(driver):
        # 마지막으로 리프레시해서 확인
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
