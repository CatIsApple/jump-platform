from __future__ import annotations

import random
import time
from typing import Any, Callable, Literal

from .models import Workflow
from .platform_domains import resolve_platform_domain
from .site_handlers import browser_handlers
from .sites import BROWSER_REQUIRED_SITES, SITE_KEYS

# 실행 결과 상태(실행 기록/대시보드용)
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
    """사이트별 작업 실행.

    - simulate=True: UI/엔진 검증용
    - simulate=False: 실제 실행(사이트 로직 이식)

    실행 결과는 (status, message) 형태로 반환되며, 그대로 실행 기록(run history)에 저장된다.
    """

    domain = resolve_platform_domain(workflow.site_key, workflow.domain)
    domain = domain.strip()
    if not domain:
        return "failed", "도메인이 비어 있어 실행할 수 없습니다."

    if simulate:
        return _simulate_workflow(workflow, emit)

    # 원본 work_user 포맷에 맞춰 dict로 변환
    started_at = ""
    if scheduled_for and len(scheduled_for) >= 8:
        started_at = scheduled_for[-8:]

    user = {
        "name": workflow.site_key,
        "shop_name": workflow.shop_name,
        "domain": domain,
        "id": workflow.username.strip(),
        "pw": workflow.password,
        "startedAt": started_at,
    }

    if not user["id"] or not user["pw"]:
        # 일부 사이트는 쿠키만으로 동작할 수 있지만, 기본적으로는 계정이 필요하다고 안내
        emit(f"[{workflow.name}] 아이디/비밀번호가 비어 있습니다. 쿠키 기반으로만 시도할 수 있습니다.", "WARNING")

    site = workflow.site_key.strip()

    # 모든 사이트는 브라우저 기반으로 동작
    if driver is None:
        return "failed", "브라우저가 준비되지 않았습니다. (Selenium/Chrome 설치 및 실행 상태를 확인하세요)"

    if site == "오피가이드":
        return browser_handlers.opguide(driver, user, emit)
    if site == "헬로밤":
        return browser_handlers.hellobam(driver, user, emit)
    if site == "오피뷰":
        return browser_handlers.opview(driver, user, emit)
    if site == "오피매니아":
        return browser_handlers.opmania(driver, user, emit)
    if site == "외로운밤":
        return browser_handlers.lybam(driver, user, emit)
    if site == "카카오떡":
        return browser_handlers.kakaotteok(driver, user, emit)
    if site == "오피아트":
        return browser_handlers.opart(driver, user, emit)
    if site == "밤의민족":
        return browser_handlers.bamminjok(driver, user, emit)
    if site == "섹밤":
        return browser_handlers.sexbam(driver, user, emit)
    if site == "밤의제국":
        return browser_handlers.bamje(driver, user, emit)
    if site == "오피나라":
        return browser_handlers.opnara(driver, user, emit)
    if site == "오피러브":
        return browser_handlers.oplove(driver, user, emit)
    if site == "오피마트":
        return browser_handlers.opmart(driver, user, emit)

    return "failed", f"지원되지 않는 사이트: {site}"
