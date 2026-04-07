from __future__ import annotations

import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Literal

from .exceptions import UserInterventionRequired
from .models import Workflow
from .platform_domains import resolve_platform_domain
from .sites import BROWSER_REQUIRED_SITES, SITE_KEYS

# ── jump_site_modules 라이브러리 로드 ──
# /Users/daon/Downloads/dist/ 에 jump_site_modules 패키지가 위치
_lib_parent = str(Path(__file__).resolve().parent.parent.parent)
if _lib_parent not in sys.path:
    sys.path.insert(0, _lib_parent)

from jump_site_modules import create_site  # noqa: E402
from jump_site_modules.base import BaseSite  # noqa: E402
from jump_site_modules.exceptions import CaptchaError  # noqa: E402

# ── 워커 대시보드 모듈 연동 (캡차/쿠키) ──
from .captcha_solver import is_robot_page, robot_pass  # noqa: E402
from .file_manager import load_cookies as _fm_load_cookies  # noqa: E402
from .file_manager import save_cookies as _fm_save_cookies  # noqa: E402

# 2Captcha API 키 (하드코딩)
CAPTCHA_API_KEY = "0d832bea4650d16a3cd7fa6bcb70a06e"

# ══════════════════════════════════════════════════════════════════
#  BaseSite 메서드 패치
#  라이브러리는 captcha_solver/file_manager를 상대 import로 찾지만
#  독립 패키지로 사용 시 ImportError가 발생한다.
#  여기서 워커 대시보드의 구현체를 직접 주입한다.
# ══════════════════════════════════════════════════════════════════


def _patched_is_robot_page(self: BaseSite) -> bool:
    try:
        return is_robot_page(self.driver)
    except Exception:
        return False


def _patched_require_human_check(self: BaseSite) -> None:
    try:
        if not is_robot_page(self.driver):
            return
    except Exception:
        return

    self.emit("Cloudflare 챌린지 감지. 자동 해결 시도...", "WARNING")

    if self._captcha_api_key:
        solved = robot_pass(self.driver, self._captcha_api_key, self._emit)
        if solved:
            return

    raise CaptchaError(
        "로봇/캡차 페이지가 감지되었습니다. 자동 해결에 실패했습니다."
    )


def _patched_save_cookies(self: BaseSite, cookie_keys: list[str] | None = None) -> bool:
    try:
        return _fm_save_cookies(self.driver, self.domain, self.username, cookie_keys)
    except Exception:
        return False


def _patched_load_cookies(self: BaseSite, cookie_keys: list[str] | None = None) -> bool:
    try:
        return _fm_load_cookies(self.driver, self.domain, self.username, cookie_keys)
    except Exception:
        return False


BaseSite._is_robot_page = _patched_is_robot_page
BaseSite.require_human_check = _patched_require_human_check
BaseSite.save_cookies = _patched_save_cookies
BaseSite.load_cookies = _patched_load_cookies


# ══════════════════════════════════════════════════════════════════
#  공개 API
# ══════════════════════════════════════════════════════════════════

WorkflowStatus = Literal[
    "success",
    "failed",
    "blocked",
    "unknown",
    "cooldown",
    "insufficient",
    "login_required",
]


def available_sites() -> list[str]:
    return SITE_KEYS.copy()


def requires_browser(workflow: Workflow) -> bool:
    return workflow.site_key in BROWSER_REQUIRED_SITES


def _simulate_workflow(
    workflow: Workflow,
    emit: Callable[[str, str], None],
) -> tuple[WorkflowStatus, str]:
    emit(f"[시뮬레이션] [{workflow.name}] 작업 시작 ({workflow.site_key})", "INFO")
    emit(f"[시뮬레이션] 도메인: {workflow.domain}", "INFO")

    simulated_steps = [
        "작업 설정 로드",
        "세션 준비",
        "사이트 처리 실행",
        "결과 저장",
    ]

    for step in simulated_steps:
        emit(f"[시뮬레이션] [{workflow.name}] {step}", "DEBUG")
        time.sleep(random.uniform(0.15, 0.55))

    if random.random() < 0.06:
        return "failed", "원격 페이지 응답 지연(시뮬레이션)."

    return "success", "정상 완료(시뮬레이션)"


def execute_workflow(
    workflow: Workflow,
    emit: Callable[[str, str], None],
    *,
    scheduled_for: str,
    driver: Any | None,
    simulate: bool = False,
) -> tuple[WorkflowStatus, str]:
    """사이트별 작업 실행 (jump_site_modules 라이브러리 기반).

    create_site() → site.login() → site.jump() 순으로 실행.
    """

    domain = resolve_platform_domain(workflow.site_key, workflow.domain)
    domain = domain.strip()
    if not domain:
        return "failed", "도메인이 비어 있어 실행할 수 없습니다."

    if simulate:
        return _simulate_workflow(workflow, emit)

    if driver is None:
        return "failed", "브라우저가 준비되지 않았습니다. (Selenium/Chrome 설치 및 실행 상태를 확인하세요)"

    site_key = workflow.site_key.strip()
    uid = workflow.username.strip()
    started_at = ""
    if scheduled_for and len(scheduled_for) >= 8:
        started_at = scheduled_for[-8:]

    if not uid or not workflow.password:
        emit(f"[{workflow.name}] 아이디/비밀번호가 비어 있습니다.", "WARNING")

    # ── 사이트 인스턴스 생성 ──
    try:
        site = create_site(
            site_key,
            driver,
            domain,
            uid,
            workflow.password,
            emit=emit,
            captcha_api_key=CAPTCHA_API_KEY,
        )
    except KeyError:
        return "failed", f"지원되지 않는 사이트: {site_key}"

    # ── 도메인 자동 전환 비활성화 (업주 전용 도메인 보호) ──
    if hasattr(site, '_maybe_switch_announced_domain'):
        site._maybe_switch_announced_domain = lambda: False

    # ── 로그인 ──
    try:
        login_result = site.login()
        if not login_result.success:
            emit(f"[{site_key}] 로그인 실패: {login_result.message}", "ERROR")
            return "login_required", login_result.message
        emit(f"[{site_key}] 로그인 성공 ({login_result.method})", "INFO")
    except CaptchaError as exc:
        raise UserInterventionRequired(str(exc)) from exc
    except Exception as exc:
        return "failed", f"로그인 오류: {exc}"

    # ── 점프 실행 ──
    try:
        jump_result = site.jump()
        status = jump_result.status or "unknown"
        msg = jump_result.message or "결과 확인 불가"

        if status == "success":
            emit(f"[성공] {site_key} - {uid}: {started_at} {msg}", "INFO")
        elif status == "cooldown":
            emit(f"[대기] {site_key} - {uid}: {started_at} {msg}", "INFO")
        elif status == "insufficient":
            emit(f"[부족] {site_key} - {uid}: {started_at} {msg}", "WARNING")
        elif status == "login_required":
            emit(f"[로그인] {site_key} - {uid}: {started_at} {msg}", "WARNING")
        else:
            emit(f"[실패] {site_key} - {uid}: {started_at} {msg}", "ERROR")

        return status, msg
    except CaptchaError as exc:
        raise UserInterventionRequired(str(exc)) from exc
    except Exception as exc:
        return "failed", f"점프 오류: {exc}"
