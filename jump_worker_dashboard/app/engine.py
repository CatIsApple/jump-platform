from __future__ import annotations

from collections import deque
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .browser import BrowserManager
from .db import Database
from .exceptions import UserInterventionRequired
from .file_manager import artifacts_dir, cleanup_artifacts
from .handlers import execute_workflow, requires_browser
from .log_bus import LogBus
from .site_handlers.browser_handlers import configure_captcha


@dataclass(frozen=True)
class WorkItem:
    workflow_id: int
    scheduled_for: str
    trigger_type: str  # scheduled | manual
    enqueued_at: str


class WorkerEngine:
    """단일 러너(순차 실행) + 스케줄러(시간 체크) 엔진.

    원본 JumpWorker 동작(무한 루프, work_stack 기반, 브라우저 재사용 구조)을
    최대한 비슷하게 가져가되, 여기서는 UI/상태 관리가 명확하도록 단순화했다.
    """

    def __init__(
        self,
        db: Database,
        log_bus: LogBus,
        poll_interval: float = 1.0,
        idle_sleep: float = 0.5,
    ) -> None:
        self.db = db
        self.log_bus = log_bus
        self.poll_interval = max(0.2, float(poll_interval))
        self.idle_sleep = max(0.1, float(idle_sleep))

        self._stop_event = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._runner_thread: threading.Thread | None = None
        self._browser: BrowserManager | None = None

        self._queue: deque[WorkItem] = deque()
        self._enqueued_keys: set[str] = set()  # day|workflow_id|HH:MM:SS

        self._queue_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._current_item: WorkItem | None = None
        self._current_started_at: str | None = None
        self._last_result: tuple[str, str, str] | None = None  # (name, status, finished_at)

    @property
    def is_running(self) -> bool:
        runner = self._runner_thread is not None and self._runner_thread.is_alive()
        sched = self._scheduler_thread is not None and self._scheduler_thread.is_alive()
        return runner or sched

    @property
    def queue_size(self) -> int:
        with self._queue_lock:
            return len(self._queue)

    def snapshot(self) -> dict[str, str]:
        """UI에서 쓰는 현재 상태 스냅샷."""
        with self._state_lock, self._queue_lock:
            if not self.is_running:
                state = "중지"
            else:
                state = "실행 중" if self._current_item is not None else "대기"

            current_name = ""
            current_trigger = ""
            current_started_at = ""
            if self._current_item is not None:
                wf = self.db.get_workflow(self._current_item.workflow_id)
                current_name = wf.name if wf else f"id={self._current_item.workflow_id}"
                current_trigger = "스케줄" if self._current_item.trigger_type == "scheduled" else "수동"
                current_started_at = self._current_started_at or ""

            last_name = ""
            last_status = ""
            last_finished_at = ""
            if self._last_result is not None:
                last_name, last_status, last_finished_at = self._last_result

            return {
                "state": state,
                "queue_size": str(len(self._queue)),
                "current_name": current_name,
                "current_trigger": current_trigger,
                "current_started_at": current_started_at,
                "last_name": last_name,
                "last_status": last_status,
                "last_finished_at": last_finished_at,
            }

    def start(self) -> None:
        with self._state_lock:
            if self.is_running:
                return

            self._stop_event.clear()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="scheduler-loop",
                daemon=True,
            )
            self._runner_thread = threading.Thread(
                target=self._runner_loop,
                name="runner-loop",
                daemon=True,
            )
            self._scheduler_thread.start()
            self._runner_thread.start()

        self.log_bus.emit(
            f"워커 엔진 시작 (순차 처리, 폴링={self.poll_interval:.1f}초)",
            "INFO",
        )

    def stop(self) -> None:
        with self._state_lock:
            if not self.is_running:
                return
            self._stop_event.set()

        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=2.0)
        if self._runner_thread:
            self._runner_thread.join(timeout=2.0)

        with self._state_lock, self._queue_lock:
            self._scheduler_thread = None
            self._runner_thread = None
            self._queue.clear()
            self._current_item = None
            self._current_started_at = None

        if self._browser is not None:
            self._browser.quit()
            self._browser = None

        self.log_bus.emit("워커 엔진 중지", "INFO")

    def run_now(self, workflow_id: int) -> None:
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._enqueue(
            WorkItem(
                workflow_id=int(workflow_id),
                scheduled_for=now_text,
                trigger_type="manual",
                enqueued_at=now_text,
            )
        )
        if not self.is_running:
            self.log_bus.emit("엔진이 중지 상태입니다. 시작하면 대기열 작업이 실행됩니다.", "WARNING")
        self.log_bus.emit(f"수동 실행 대기열 추가 (id={workflow_id})", "INFO")

    def _enqueue(self, item: WorkItem) -> None:
        with self._queue_lock:
            self._queue.append(item)

    def _dequeue(self) -> WorkItem | None:
        with self._queue_lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    def _scheduler_loop(self) -> None:
        # poll_interval이 1초보다 크거나(또는 시스템이 잠깐 멈추는 경우) 특정 "초" 스케줄을 놓치는 문제가 있어
        # 마지막 체크 시각부터 현재까지의 구간을 초 단위로 따라가며 캐치업한다.
        last_checked = datetime.now().replace(microsecond=0) - timedelta(seconds=1)
        last_day = last_checked.strftime("%Y-%m-%d")
        max_catchup_seconds = 180  # 너무 긴 구간 캐치업으로 UI/CPU가 멈추는 것을 방지

        while not self._stop_event.is_set():
            try:
                now = datetime.now().replace(microsecond=0)

                # 날짜가 바뀌면 dedupe 키도 초기화(일자 단위 중복 방지)
                day_now = now.strftime("%Y-%m-%d")
                if day_now != last_day:
                    with self._queue_lock:
                        self._enqueued_keys = {k for k in self._enqueued_keys if k.startswith(day_now)}
                    last_day = day_now

                gap = int((now - last_checked).total_seconds())
                if gap <= 0:
                    time.sleep(self.poll_interval)
                    continue

                if gap > max_catchup_seconds:
                    self.log_bus.emit(
                        f"스케줄러 지연 감지: {gap}초. 최근 {max_catchup_seconds}초 구간만 처리합니다.",
                        "WARNING",
                    )
                    start = now - timedelta(seconds=max_catchup_seconds)
                else:
                    start = last_checked

                cursor = start + timedelta(seconds=1)
                while cursor <= now and not self._stop_event.is_set():
                    hms = cursor.strftime("%H:%M:%S")
                    day = cursor.strftime("%Y-%m-%d")

                    due = self.db.list_due_workflows(hms)
                    for wf in due:
                        key = f"{day}|{wf.id}|{hms}"
                        with self._queue_lock:
                            if key in self._enqueued_keys:
                                continue
                            self._enqueued_keys.add(key)

                        self._enqueue(
                            WorkItem(
                                workflow_id=int(wf.id),
                                scheduled_for=f"{day} {hms}",
                                trigger_type="scheduled",
                                enqueued_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            )
                        )
                        self.log_bus.emit(f"스케줄 실행 대기열 추가: {wf.name} ({hms})", "INFO")

                    cursor += timedelta(seconds=1)

                last_checked = now

                # 오래된 dedupe 키 정리(안전장치)
                with self._queue_lock:
                    if len(self._enqueued_keys) > 5000:
                        self._enqueued_keys = {k for k in self._enqueued_keys if k.startswith(day_now)}

                time.sleep(self.poll_interval)
            except Exception as exc:  # noqa: BLE001
                # 스케줄러 스레드가 죽지 않게 보호(24시간 무중단)
                self.log_bus.emit(f"스케줄러 오류: {exc}", "ERROR")
                last_checked = datetime.now().replace(microsecond=0)
                last_day = last_checked.strftime("%Y-%m-%d")
                time.sleep(min(self.poll_interval, 1.0))

    def _runner_loop(self) -> None:
        status_kor = {
            "success": "성공",
            "failed": "실패",
            "blocked": "차단됨",
            "unknown": "확인 불가",
            "cooldown": "대기 룰",
            "insufficient": "횟수/회수 부족",
            "login_required": "로그인 필요",
        }

        def save_driver_artifacts(driver_obj: object, *, prefix: str) -> str | None:
            """브라우저 실행 증적(스크린샷/URL/HTML) 저장. 실패해도 엔진은 계속 돈다."""
            try:
                d = artifacts_dir()
            except Exception:
                return None

            # Screenshot
            png_path = d / f"{prefix}.png"
            try:
                ok = False
                if hasattr(driver_obj, "save_screenshot"):
                    ok = bool(driver_obj.save_screenshot(str(png_path)))  # type: ignore[attr-defined]
                if not ok:
                    return None
            except Exception:
                return None

            # 디스크가 무한히 늘지 않도록 보관 정책 적용
            try:
                cleanup_artifacts(max_keep=500, max_age_days=14)
            except Exception:
                pass

            # UI/로그에 노출은 상대 경로가 보기 좋다.
            try:
                rel = png_path.relative_to(Path(__file__).resolve().parents[1])
                return str(rel)
            except Exception:
                return str(png_path)

        while not self._stop_event.is_set():
            try:
                item = self._dequeue()
                if item is None:
                    time.sleep(self.idle_sleep)
                    continue

                wf = self.db.get_workflow(item.workflow_id)
                if wf is None:
                    self.log_bus.emit(f"작업을 찾을 수 없습니다: id={item.workflow_id}", "WARNING")
                    continue
                if not wf.enabled:
                    self.log_bus.emit(f"중지된 작업이라 건너뜁니다: {wf.name}", "INFO")
                    continue

                started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                trigger = "스케줄" if item.trigger_type == "scheduled" else "수동"
                with self._state_lock:
                    self._current_item = item
                    self._current_started_at = started_at

                self.log_bus.emit(f"실행 시작: {wf.name} ({trigger})", "INFO")

                simulate = False
                driver = None
                status = "failed"
                message = ""
                max_retries = 1  # 브라우저 사망 시 1회 재시도

                for attempt in range(max_retries + 1):
                    try:
                        simulate = self.db.get_setting("simulate_mode", "0") == "1"
                        if simulate:
                            self.log_bus.emit(
                                "시뮬레이션 모드: 실제 사이트 접속/자동화는 수행하지 않습니다.",
                                "WARNING",
                            )

                        if not simulate and requires_browser(wf):
                            if self._browser is None:
                                headless = self.db.get_setting("headless", "0") == "1"
                                self._browser = BrowserManager(headless=headless)

                            # 브라우저가 닫혔으면 자동 재생성
                            if not self._browser.is_alive():
                                self.log_bus.emit("브라우저가 닫혀 있어 새로 실행합니다...", "WARNING")
                                self._browser.reset()

                            driver = self._browser.get_driver()

                            # 사이트 간 세션/쿠키 충돌 방지: 매 작업 전 초기화
                            try:
                                driver.delete_all_cookies()
                            except Exception:
                                pass
                            try:
                                driver.execute_script("window.sessionStorage.clear(); window.localStorage.clear();")
                            except Exception:
                                pass

                            # 2Captcha API 키 설정
                            captcha_key = self.db.get_setting("captcha_api_key", "")
                            configure_captcha(
                                captcha_key,
                                lambda msg, level="INFO": self.log_bus.emit(msg, level),
                            )

                        status, message = execute_workflow(
                            wf,
                            lambda msg, level="INFO": self.log_bus.emit(msg, level),
                            scheduled_for=item.scheduled_for,
                            driver=driver,
                            simulate=simulate,
                        )
                        break  # 성공하면 재시도 루프 탈출
                    except UserInterventionRequired as exc:
                        reason = str(exc).strip() or "자동 진행 불가"
                        status = "blocked"
                        message = f"차단됨: {reason}"
                        break  # 차단은 재시도하지 않음
                    except Exception as exc:  # noqa: BLE001
                        exc_msg = str(exc).lower()
                        is_browser_dead = any(kw in exc_msg for kw in (
                            "no such window", "target window already closed",
                            "session not created", "invalid session",
                            "web view not found", "chrome not reachable",
                        ))

                        if is_browser_dead and attempt < max_retries:
                            self.log_bus.emit(
                                f"브라우저 창이 죽었습니다. 새 브라우저로 재시도합니다: {wf.name}",
                                "WARNING",
                            )
                            if self._browser is not None:
                                self._browser.reset()
                            driver = None
                            continue  # 재시도

                        status = "failed"
                        message = f"예기치 않은 오류: {exc}"
                        if is_browser_dead:
                            self.log_bus.emit("브라우저 재시도에도 실패. 리셋 후 다음 작업으로 넘어갑니다.", "ERROR")
                            if self._browser is not None:
                                self._browser.reset()
                        break

                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 실패/문제 상태는 스크린샷을 남겨서 나중에 확인할 수 있게 한다.
                if not simulate and driver is not None and status in (
                    "failed",
                    "blocked",
                    "unknown",
                    "login_required",
                    "insufficient",
                ):
                    prefix = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_wf{wf.id}_{status}"
                    shot = save_driver_artifacts(driver, prefix=prefix)
                    if shot:
                        message = f"{message} | 스크린샷: {shot}"

                self.db.add_history(
                    workflow_id=int(wf.id),
                    workflow_name=wf.name,
                    trigger_type=item.trigger_type,
                    scheduled_for=item.scheduled_for,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    message=message,
                )

                kor = status_kor.get(status, status)
                if status == "success":
                    self.log_bus.emit(f"실행 완료: {wf.name} - {message}", "INFO")
                elif status in ("blocked", "unknown", "cooldown", "insufficient", "login_required"):
                    self.log_bus.emit(f"실행 결과: {wf.name} - {kor} - {message}", "WARNING")
                else:
                    self.log_bus.emit(f"실행 실패: {wf.name} - {message}", "ERROR")

                with self._state_lock:
                    self._last_result = (wf.name, status, finished_at)
                    self._current_item = None
                    self._current_started_at = None
            except Exception as exc:  # noqa: BLE001
                # 러너 스레드가 죽지 않게 보호(24시간 무중단)
                self.log_bus.emit(f"러너 오류: {exc}", "ERROR")
                with self._state_lock:
                    self._current_item = None
                    self._current_started_at = None
                time.sleep(0.5)
