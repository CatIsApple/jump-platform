from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.events import Key
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, TabbedContent, TabPane

from .api import AdminApiConfig, ApiError, JumpAdminApi
from .config import AppConfig, load_config, save_config


def _fmt_ts(unix_s: int | None) -> str:
    if not unix_s:
        return "-"
    try:
        return datetime.fromtimestamp(int(unix_s)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(unix_s)


def _ua_summary(ua: str) -> str:
    """User-Agent 문자열을 간략한 요약으로 변환."""
    if not ua:
        return "-"
    import re as _re
    s = ua.lower()
    # OS 탐지
    if "windows" in s:
        os_name = "Win"
    elif "mac os x" in s or "macintosh" in s:
        os_name = "macOS"
    elif "linux" in s:
        os_name = "Linux"
    else:
        os_name = "?"
    # 클라이언트 탐지 — 앱 식별자 우선 (Chrome 같은 일반 토큰보다 먼저)
    if "jump-worker-dashboard" in s:
        m = _re.search(r"jump-worker-dashboard/([0-9][\w.\-]*)", ua, _re.I)
        client = f"jump v{m.group(1)}" if m else "jump-client"
    elif "python-requests" in s or "python" in s:
        client = "jump-client"
    elif "firefox" in s:
        client = "Firefox"
    elif "safari" in s and "chrome" not in s:
        client = "Safari"
    elif "chrome" in s:
        client = "Chrome"
    else:
        client = ua[:20]
    return f"{os_name}/{client}"


class Toast(Static):
    def show(self, msg: str) -> None:
        self.update(msg)


@dataclass
class ConfirmState:
    action_key: str
    target: str
    until_s: float


class JumpAdminTui(App):
    """k9s 스타일의 단일 화면 TUI.

    - 모달을 쓰지 않습니다.
    - 하단 편집 패널에서 입력/저장합니다.
    - 중요한 동작(삭제/폐기/정지)은 2회 확인(5초 내 재실행)으로 보호합니다.
    """

    CSS = r"""
    Screen {
      background: #0b0f19;
    }

    #root {
      padding: 1 1;
    }

    #bar {
      height: auto;
      margin: 0 0 1 0;
    }

    #bar Button {
      margin: 0 1 0 0;
    }

    #split {
      height: 1fr;
    }

    .panel {
      border: solid #243042;
      background: #0f172a;
      padding: 1 1;
    }

    #toast {
      height: 1;
      color: #d1d5db;
      background: #111827;
      padding: 0 1;
    }

    DataTable {
      height: 1fr;
      background: #0b1220;
    }

    #detail {
      width: 48;
      min-width: 40;
      background: #0b1220;
    }

    #detail_title {
      text-style: bold;
      margin: 0 0 1 0;
    }

    #edit {
      margin: 1 0 1 0;
      height: auto;
    }

    #edit_title {
      text-style: bold;
      margin: 0 0 1 0;
    }

    .hidden {
      display: none;
    }

    .field_label {
      margin: 0 0 0 0;
      color: #cbd5e1;
    }

    .field_input {
      margin: 0 0 1 0;
    }

    .row {
      height: auto;
      margin: 1 0 0 0;
    }

    .row Button {
      margin: 0 1 0 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "종료", priority=True),
        Binding("r", "refresh", "새로고침", priority=True),
        Binding("1", "view_licenses", "라이센스", priority=True),
        Binding("2", "view_domains", "도메인", priority=True),
        Binding("3", "view_sessions", "세션", priority=True),
        Binding("c", "open_config", "설정", priority=True),
        Binding("n", "license_new", "라이센스 생성", priority=True),
        Binding("e", "primary", "기본 동작", priority=True),
        Binding("s", "license_toggle", "정지/해제", priority=True),
        Binding("x", "license_revoke", "폐기", priority=True),
        Binding("d", "domain_delete", "도메인 삭제", priority=True),
        Binding("k", "session_revoke", "세션 종료", priority=True),
        Binding("u", "sessions_cleanup", "오래된 세션 정리", priority=True),
        Binding("f", "sessions_filter", "세션 필터", priority=True),
        Binding("escape", "edit_cancel", "취소", show=False, priority=True),
        Binding("ctrl+s", "edit_submit", "저장", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg: AppConfig = load_config()
        self.api: JumpAdminApi | None = None

        self.toast = Toast("", id="toast")

        self._edit_mode: str = ""  # config|license_new|license_extend|domain_edit
        self._edit_ctx: dict[str, Any] = {}
        self._pending_confirm: ConfirmState | None = None

        self._licenses: list[dict[str, Any]] = []
        self._domains: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._sessions_stats: dict[str, Any] = {}
        # 세션 탭 필터 순환: all → active → stale → revoked → all
        self._sessions_status_filter: str = "active"

    # -------------------------
    # UI
    # -------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="root"):
            with Horizontal(id="bar"):
                yield Button("새로고침[r]", id="btn_refresh", variant="primary")
                yield Button("설정[c]", id="btn_config")
                yield Button("라이센스[1]", id="btn_view_licenses")
                yield Button("도메인[2]", id="btn_view_domains")
                yield Button("세션[3]", id="btn_view_sessions")
                yield Button("생성[n]", id="btn_license_new")
                yield Button("기본[e]", id="btn_primary")
                yield Button("정지/해제[s]", id="btn_license_toggle")
                yield Button("폐기[x]", id="btn_license_revoke", variant="warning")
                yield Button("삭제[d]", id="btn_domain_delete", variant="warning")
                yield Button("세션종료[k]", id="btn_session_revoke", variant="warning")
                yield Button("필터[f]", id="btn_sessions_filter")
                yield Button("정리[u]", id="btn_sessions_cleanup", variant="warning")

            with Horizontal(id="split"):
                with TabbedContent(id="tabs"):
                    with TabPane("라이센스", id="tab_licenses"):
                        yield self._make_licenses_table()
                    with TabPane("플랫폼 도메인", id="tab_domains"):
                        yield self._make_domains_table()
                    with TabPane("세션", id="tab_sessions"):
                        yield self._make_sessions_table()

                with Container(id="detail", classes="panel"):
                    yield Label("상세", id="detail_title")
                    yield Static("-", id="detail_body")

            with Container(id="edit", classes="panel hidden"):
                yield Label("편집", id="edit_title")

                # fields
                for key, label, secret in self._field_defs():
                    yield Label(label, id=f"lbl_{key}", classes="field_label hidden")
                    yield Input(id=f"in_{key}", password=secret, classes="field_input hidden")

                with Horizontal(classes="row"):
                    yield Button("저장[Ctrl+S/Enter]", id="btn_edit_submit", variant="primary")
                    yield Button("취소[Esc]", id="btn_edit_cancel")

            yield self.toast
        yield Footer()

    def _make_licenses_table(self) -> DataTable:
        table = DataTable(id="licenses", zebra_stripes=True)
        table.cursor_type = "row"
        table.show_cursor = True
        table.add_columns("ID", "업체명", "상태", "만료", "생성", "Prefix", "메모")
        return table

    def _make_domains_table(self) -> DataTable:
        table = DataTable(id="domains", zebra_stripes=True)
        table.cursor_type = "row"
        table.show_cursor = True
        table.add_columns("사이트", "도메인", "업데이트")
        return table

    def _make_sessions_table(self) -> DataTable:
        table = DataTable(id="sessions", zebra_stripes=True)
        table.cursor_type = "row"
        table.show_cursor = True
        table.add_columns(
            "ID", "업체", "상태", "디바이스", "IP", "국가", "최근접속", "생성", "브라우저"
        )
        return table

    @staticmethod
    def _field_defs() -> list[tuple[str, str, bool]]:
        return [
            ("company_name", "업체명", False),
            ("days", "일수", False),
            ("note", "메모(선택)", False),
            ("domain", "도메인", False),
            ("api_base_url", "API 기본 URL", False),
            ("access_client_id", "CF-Access-Client-Id", False),
            ("access_client_secret", "CF-Access-Client-Secret", True),
            ("admin_token", "(선택) X-Admin-Token", True),
        ]

    # -------------------------
    # Focus / context helpers
    # -------------------------
    def _tabs(self) -> TabbedContent:
        return self.query_one("#tabs", TabbedContent)

    def _active_tab(self) -> str:
        return str(getattr(self._tabs(), "active", "") or "")

    def _is_edit_open(self) -> bool:
        return bool(self._edit_mode)

    def _any_input_focused(self) -> bool:
        return isinstance(self.focused, Input)

    def _suspend_global(self) -> bool:
        return self._is_edit_open() or self._any_input_focused()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # 편집 중에는 저장/취소만 허용
        if self._is_edit_open() and action not in {"edit_submit", "edit_cancel", "quit"}:
            return False
        # Input에 포커스가 있으면(편집 패널 포함) 핫키를 입력으로 취급
        if self._any_input_focused() and action not in {"edit_submit", "edit_cancel", "quit"}:
            return False
        return None

    def on_key(self, event: Key) -> None:
        # DataTable이 일부 키를 먹는 환경에서도 동작하도록, 앱 레벨에서 한번 더 라우팅
        if self._suspend_global():
            return
        key = event.key
        mapping: dict[str, str] = {
            "q": "quit",
            "r": "refresh",
            "1": "view_licenses",
            "2": "view_domains",
            "3": "view_sessions",
            "c": "open_config",
            "n": "license_new",
            "e": "primary",
            "s": "license_toggle",
            "x": "license_revoke",
            "d": "domain_delete",
            "k": "session_revoke",
            "u": "sessions_cleanup",
            "f": "sessions_filter",
        }
        action_name = mapping.get(key)
        if not action_name:
            return
        event.stop()
        self.run_worker(self._dispatch_action(action_name), name=f"hotkey:{action_name}", thread=False)

    async def _dispatch_action(self, action_name: str) -> None:
        fn = getattr(self, f"action_{action_name}", None)
        if fn is None:
            return
        result = fn()
        if asyncio.iscoroutine(result):
            await result

    # -------------------------
    # Lifecycle
    # -------------------------
    async def on_mount(self) -> None:
        self._close_edit()
        await self._ensure_api()
        await self.action_refresh()

    async def _ensure_api(self) -> None:
        if not (self.cfg.api_base_url and self.cfg.access_client_id and self.cfg.access_client_secret):
            self.api = None
            self.toast.show("설정이 필요합니다. [c]를 눌러 설정을 입력하세요.")
            return

        self.api = JumpAdminApi(
            AdminApiConfig(
                base_url=self.cfg.api_base_url,
                access_client_id=self.cfg.access_client_id,
                access_client_secret=self.cfg.access_client_secret,
                admin_token=self.cfg.admin_token,
            )
        )
        try:
            await asyncio.to_thread(self.api.health)
            self.toast.show("연결됨")
        except Exception as exc:
            self.toast.show(f"연결 실패: {exc}")

    # -------------------------
    # Data load & render
    # -------------------------
    async def action_refresh(self) -> None:
        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("불러오는 중...")
        status_param: str | None = self._sessions_status_filter if self._sessions_status_filter != "all" else None
        try:
            licenses, domains, sessions_data = await asyncio.gather(
                asyncio.to_thread(self.api.list_licenses),
                asyncio.to_thread(self.api.list_domains),
                asyncio.to_thread(
                    self.api.list_all_sessions, status=status_param, limit=200
                ),
            )
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        self._licenses = list(licenses or [])
        self._domains = list(domains or [])
        self._sessions = list((sessions_data or {}).get("sessions") or [])
        self._sessions_stats = dict((sessions_data or {}).get("stats") or {})

        lic_table = self.query_one("#licenses", DataTable)
        lic_table.clear()
        for lic in self._licenses:
            lid = str(lic.get("id", ""))
            lic_table.add_row(
                lid,
                str(lic.get("company_name", "")),
                str(lic.get("status", "")),
                _fmt_ts(lic.get("expires_at")),
                _fmt_ts(lic.get("created_at")),
                str(lic.get("key_prefix", "")),
                str(lic.get("note", "")),
                key=lid,
            )

        dom_table = self.query_one("#domains", DataTable)
        dom_table.clear()
        for dom in self._domains:
            site_key = str(dom.get("site_key", ""))
            dom_table.add_row(
                site_key,
                str(dom.get("domain", "")),
                _fmt_ts(dom.get("updated_at")),
                key=site_key,
            )

        ses_table = self.query_one("#sessions", DataTable)
        ses_table.clear()
        for s in self._sessions:
            sid = str(s.get("id", ""))
            status = str(s.get("status", ""))
            # 상태별 이모지 프리픽스로 시각적 구분
            badge = {"active": "🟢", "stale": "🟡", "revoked": "⚫"}.get(status, "")
            ua_short = _ua_summary(str(s.get("user_agent", "")))
            ses_table.add_row(
                sid,
                str(s.get("company_name", ""))[:20],
                f"{badge} {status}",
                str(s.get("device_id", ""))[:24],
                str(s.get("ip_address", "")) or "-",
                str(s.get("ip_country", "")) or "-",
                _fmt_ts(s.get("last_seen_at")),
                _fmt_ts(s.get("created_at")),
                ua_short,
                key=sid,
            )

        # ensure cursor
        if lic_table.row_count and (lic_table.cursor_row < 0 or lic_table.cursor_row >= lic_table.row_count):
            lic_table.move_cursor(row=0, column=0, animate=False, scroll=False)
        if dom_table.row_count and (dom_table.cursor_row < 0 or dom_table.cursor_row >= dom_table.row_count):
            dom_table.move_cursor(row=0, column=0, animate=False, scroll=False)
        if ses_table.row_count and (ses_table.cursor_row < 0 or ses_table.cursor_row >= ses_table.row_count):
            ses_table.move_cursor(row=0, column=0, animate=False, scroll=False)

        self._update_detail_from_focus()
        self.toast.show("완료")

    def _update_detail_from_focus(self) -> None:
        detail = self.query_one("#detail_body", Static)
        tab = self._active_tab()
        if tab == "tab_sessions":
            stats = self._sessions_stats or {}
            header = (
                f"📊 전체 {stats.get('total', 0)}개  |  "
                f"🟢 활성 {stats.get('active', 0)}  "
                f"🟡 stale {stats.get('stale', 0)}  "
                f"⚫ 폐기 {stats.get('revoked', 0)}\n"
                f"필터: [{self._sessions_status_filter}]  "
                f"(f=필터 변경)\n"
            )
            table = self.query_one("#sessions", DataTable)
            if table.row_count <= 0 or table.cursor_row < 0:
                detail.update(header + "\n조건에 맞는 세션이 없습니다.")
                return
            # 현재 커서의 세션 dict 찾기
            try:
                row_key = str(table.get_row_at(table.cursor_row)[0])
                session = next((s for s in self._sessions if str(s.get("id")) == row_key), None)
            except Exception:
                session = None
            if not session:
                detail.update(header)
                return
            lines = [
                header,
                f"세션 ID: {session.get('id')}",
                f"라이센스 ID: {session.get('license_id')}",
                f"업체: {session.get('company_name', '')}",
                f"상태: {session.get('status', '')}",
                f"디바이스: {session.get('device_id', '') or '-'}",
                f"IP: {session.get('ip_address') or '-'}  ({session.get('ip_country') or '?'})",
                f"브라우저: {session.get('user_agent') or '-'}",
                f"Token prefix: {session.get('token_prefix', '')}…",
                f"생성: {_fmt_ts(session.get('created_at'))}",
                f"최근 접속: {_fmt_ts(session.get('last_seen_at'))}",
                f"폐기 시각: {_fmt_ts(session.get('revoked_at')) if session.get('revoked_at') else '-'}",
                "",
                "[키] k=세션 종료(2회), u=오래된 세션 정리(2회), f=필터",
            ]
            detail.update("\n".join(lines))
            return

        if tab == "tab_domains":
            table = self.query_one("#domains", DataTable)
            if table.row_count <= 0 or table.cursor_row < 0:
                detail.update("도메인 데이터가 없습니다.")
                return
            row = table.get_row_at(table.cursor_row)
            site_key = str(row[0]) if len(row) > 0 else ""
            dom = str(row[1]) if len(row) > 1 else ""
            updated = str(row[2]) if len(row) > 2 else ""
            detail.update(
                "\n".join(
                    [
                        f"사이트: {site_key}",
                        f"도메인: {dom}",
                        f"업데이트: {updated}",
                        "",
                        "[키] e=수정, d=삭제(2회), r=새로고침",
                    ]
                )
            )
            return

        table = self.query_one("#licenses", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            detail.update("라이센스 데이터가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        detail.update(
            "\n".join(
                [
                    f"ID: {row[0]}",
                    f"업체명: {row[1]}",
                    f"상태: {row[2]}",
                    f"만료: {row[3]}",
                    f"생성: {row[4]}",
                    f"Prefix: {row[5]}",
                    f"메모: {row[6]}",
                    "",
                    "[키] n=생성, e=연장, s=정지/해제(2회), x=폐기(2회)",
                ]
            )
        )

    # selection events
    @on(DataTable.RowHighlighted)
    def _on_row_highlighted(self) -> None:
        self._update_detail_from_focus()

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self) -> None:
        self._update_detail_from_focus()

    # -------------------------
    # Edit panel
    # -------------------------
    def _hide_all_fields(self) -> None:
        for key, _label, _secret in self._field_defs():
            self.query_one(f"#lbl_{key}", Label).add_class("hidden")
            self.query_one(f"#in_{key}", Input).add_class("hidden")

    def _show_fields(self, keys: list[str]) -> None:
        for key in keys:
            self.query_one(f"#lbl_{key}", Label).remove_class("hidden")
            self.query_one(f"#in_{key}", Input).remove_class("hidden")

    def _clear_fields(self) -> None:
        for key, _label, _secret in self._field_defs():
            self.query_one(f"#in_{key}", Input).value = ""

    def _open_edit(self, mode: str, *, title: str, fields: list[str], defaults: dict[str, str] | None = None, ctx: dict[str, Any] | None = None) -> None:
        self._edit_mode = mode
        self._edit_ctx = ctx or {}
        edit = self.query_one("#edit", Container)
        edit.remove_class("hidden")
        self.query_one("#edit_title", Label).update(title)
        self._hide_all_fields()
        self._clear_fields()
        self._show_fields(fields)
        for k, v in (defaults or {}).items():
            self.query_one(f"#in_{k}", Input).value = v
        if fields:
            self.call_after_refresh(lambda: self.set_focus(self.query_one(f"#in_{fields[0]}", Input)))

    def _close_edit(self) -> None:
        self._edit_mode = ""
        self._edit_ctx = {}
        self._pending_confirm = None
        self._clear_fields()
        self._hide_all_fields()
        self.query_one("#edit", Container).add_class("hidden")

    def _visible_inputs(self) -> list[Input]:
        inputs: list[Input] = []
        for key, _label, _secret in self._field_defs():
            inp = self.query_one(f"#in_{key}", Input)
            if not inp.has_class("hidden"):
                inputs.append(inp)
        return inputs

    @on(Input.Submitted)
    async def _on_edit_input_submitted(self, event: Input.Submitted) -> None:
        if not event.input.id or not event.input.id.startswith("in_"):
            return
        if not self._is_edit_open():
            return
        visible = self._visible_inputs()
        try:
            idx = visible.index(event.input)
        except ValueError:
            idx = -1
        if idx >= 0 and idx < len(visible) - 1:
            self.set_focus(visible[idx + 1])
            return
        await self.action_edit_submit()

    @on(Button.Pressed)
    async def _on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        mapping = {
            "btn_refresh": "refresh",
            "btn_config": "open_config",
            "btn_view_licenses": "view_licenses",
            "btn_view_domains": "view_domains",
            "btn_view_sessions": "view_sessions",
            "btn_license_new": "license_new",
            "btn_primary": "primary",
            "btn_license_toggle": "license_toggle",
            "btn_license_revoke": "license_revoke",
            "btn_domain_delete": "domain_delete",
            "btn_session_revoke": "session_revoke",
            "btn_sessions_cleanup": "sessions_cleanup",
            "btn_sessions_filter": "sessions_filter",
            "btn_edit_submit": "edit_submit",
            "btn_edit_cancel": "edit_cancel",
        }
        action_name = mapping.get(bid)
        if not action_name:
            return
        await self._dispatch_action(action_name)

    # -------------------------
    # Confirm (2-step)
    # -------------------------
    def _confirm_twice(self, action_key: str, target: str) -> bool:
        now = monotonic()
        if self._pending_confirm and self._pending_confirm.action_key == action_key and self._pending_confirm.target == target:
            if now <= self._pending_confirm.until_s:
                self._pending_confirm = None
                return True
        self._pending_confirm = ConfirmState(action_key=action_key, target=target, until_s=now + 5.0)
        self.toast.show("확인 필요: 5초 내 같은 명령을 한 번 더 실행하세요.")
        return False

    # -------------------------
    # Actions: view
    # -------------------------
    async def action_view_licenses(self) -> None:
        self._tabs().active = "tab_licenses"
        self._update_detail_from_focus()

    async def action_view_domains(self) -> None:
        self._tabs().active = "tab_domains"
        self._update_detail_from_focus()

    async def action_view_sessions(self) -> None:
        self._tabs().active = "tab_sessions"
        self._update_detail_from_focus()

    # -------------------------
    # Actions: open edit modes
    # -------------------------
    async def action_open_config(self) -> None:
        self._open_edit(
            "config",
            title="설정",
            fields=["api_base_url", "access_client_id", "access_client_secret", "admin_token"],
            defaults={
                "api_base_url": self.cfg.api_base_url,
                "access_client_id": self.cfg.access_client_id,
                "access_client_secret": self.cfg.access_client_secret,
                "admin_token": self.cfg.admin_token,
            },
        )

    async def action_license_new(self) -> None:
        self._open_edit(
            "license_new",
            title="라이센스 생성",
            fields=["company_name", "days", "note"],
            defaults={"days": "30"},
        )

    async def action_primary(self) -> None:
        # context-sensitive
        if self._active_tab() == "tab_domains":
            await self.action_domain_edit()
        else:
            await self.action_license_extend()

    async def action_license_extend(self) -> None:
        table = self.query_one("#licenses", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        license_id = str(row[0])
        self._open_edit(
            "license_extend",
            title=f"기간 연장 (라이센스 #{license_id})",
            fields=["days"],
            defaults={"days": "30"},
            ctx={"license_id": int(license_id) if license_id.isdigit() else license_id},
        )

    async def action_domain_edit(self) -> None:
        table = self.query_one("#domains", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 사이트가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        site_key = str(row[0])
        domain = str(row[1]) if len(row) > 1 else ""
        self._open_edit(
            "domain_edit",
            title=f"도메인 수정 ({site_key})",
            fields=["domain"],
            defaults={"domain": domain},
            ctx={"site_key": site_key},
        )

    # -------------------------
    # Actions: submit/cancel edit
    # -------------------------
    async def action_edit_cancel(self) -> None:
        if not self._is_edit_open():
            return
        self._close_edit()
        self.toast.show("취소")

    async def action_edit_submit(self) -> None:
        if not self._is_edit_open():
            return
        mode = self._edit_mode

        if mode == "config":
            api_base_url = self.query_one("#in_api_base_url", Input).value.strip()
            access_client_id = self.query_one("#in_access_client_id", Input).value.strip()
            access_client_secret = self.query_one("#in_access_client_secret", Input).value.strip()
            admin_token = self.query_one("#in_admin_token", Input).value.strip()

            if not api_base_url or not api_base_url.startswith("http"):
                self.toast.show("API 기본 URL이 올바르지 않습니다.")
                return
            if not access_client_id or not access_client_secret:
                self.toast.show("CF Access ID/Secret을 입력하세요.")
                return

            self.cfg = AppConfig(
                api_base_url=api_base_url.rstrip("/"),
                access_client_id=access_client_id,
                access_client_secret=access_client_secret,
                admin_token=admin_token,
            )
            save_config(self.cfg)
            self._close_edit()
            await self._ensure_api()
            await self.action_refresh()
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        if mode == "license_new":
            company = self.query_one("#in_company_name", Input).value.strip()
            days_s = self.query_one("#in_days", Input).value.strip()
            note = self.query_one("#in_note", Input).value.strip()
            if not company:
                self.toast.show("업체명을 입력하세요.")
                return
            try:
                days = int(days_s)
            except ValueError:
                self.toast.show("일수는 숫자여야 합니다.")
                return

            self.toast.show("생성 중...")
            try:
                resp = await asyncio.to_thread(self.api.create_license, company, days, note)
            except ApiError as exc:
                self.toast.show(f"오류: {exc} ({exc.status_code})")
                return
            except Exception as exc:
                self.toast.show(f"오류: {exc}")
                return

            key = (resp or {}).get("license_key") or ""
            self._close_edit()
            self.toast.show(f"생성 완료. 라이센스 키(1회): {key}")
            await self.action_refresh()
            return

        if mode == "license_extend":
            days_s = self.query_one("#in_days", Input).value.strip()
            try:
                days = int(days_s)
            except ValueError:
                self.toast.show("일수는 숫자여야 합니다.")
                return
            license_id = self._edit_ctx.get("license_id")
            if not license_id:
                self.toast.show("선택된 라이센스가 없습니다.")
                return

            self.toast.show("연장 중...")
            try:
                await asyncio.to_thread(self.api.extend_license, int(license_id), days)
            except ApiError as exc:
                self.toast.show(f"오류: {exc} ({exc.status_code})")
                return
            except Exception as exc:
                self.toast.show(f"오류: {exc}")
                return

            self._close_edit()
            self.toast.show("연장 완료")
            await self.action_refresh()
            return

        if mode == "domain_edit":
            domain = self.query_one("#in_domain", Input).value.strip()
            site_key = str(self._edit_ctx.get("site_key", "")).strip()
            if not site_key:
                self.toast.show("선택된 사이트가 없습니다.")
                return
            if not domain:
                self.toast.show("도메인이 비어 있습니다.")
                return

            self.toast.show("저장 중...")
            try:
                await asyncio.to_thread(self.api.set_domain, site_key, domain)
            except ApiError as exc:
                self.toast.show(f"오류: {exc} ({exc.status_code})")
                return
            except Exception as exc:
                self.toast.show(f"오류: {exc}")
                return

            self._close_edit()
            self.toast.show("저장 완료")
            await self.action_refresh()
            return

    # -------------------------
    # Actions: license ops
    # -------------------------
    async def action_license_toggle(self) -> None:
        if self._active_tab() != "tab_licenses":
            self.toast.show("라이센스 탭에서만 사용할 수 있습니다.")
            return
        table = self.query_one("#licenses", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        lid = str(row[0])
        status = str(row[2]).lower().strip()
        if not self._confirm_twice("license_toggle", lid):
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("처리 중...")
        try:
            if status == "active":
                await asyncio.to_thread(self.api.suspend_license, int(lid))
            else:
                await asyncio.to_thread(self.api.resume_license, int(lid))
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        self.toast.show("완료")
        await self.action_refresh()

    async def action_license_revoke(self) -> None:
        if self._active_tab() != "tab_licenses":
            self.toast.show("라이센스 탭에서만 사용할 수 있습니다.")
            return
        table = self.query_one("#licenses", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        lid = str(row[0])
        if not self._confirm_twice("license_revoke", lid):
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("폐기 중...")
        try:
            await asyncio.to_thread(self.api.revoke_license, int(lid))
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        self.toast.show("완료")
        await self.action_refresh()

    # -------------------------
    # Actions: domain ops
    # -------------------------
    async def action_domain_delete(self) -> None:
        if self._active_tab() != "tab_domains":
            self.toast.show("도메인 탭에서만 사용할 수 있습니다.")
            return
        table = self.query_one("#domains", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 사이트가 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        site_key = str(row[0])
        if not self._confirm_twice("domain_delete", site_key):
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("삭제 중...")
        try:
            await asyncio.to_thread(self.api.delete_domain, site_key)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        self.toast.show("완료")
        await self.action_refresh()

    # -------------------------
    # Actions: session ops
    # -------------------------
    async def action_session_revoke(self) -> None:
        if self._active_tab() != "tab_sessions":
            self.toast.show("세션 탭에서만 사용할 수 있습니다.")
            return
        table = self.query_one("#sessions", DataTable)
        if table.row_count <= 0 or table.cursor_row < 0:
            self.toast.show("선택된 세션이 없습니다.")
            return
        row = table.get_row_at(table.cursor_row)
        sid = str(row[0])
        if not self._confirm_twice("session_revoke", sid):
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("세션 종료 중...")
        try:
            await asyncio.to_thread(self.api.revoke_session, int(sid))
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        self.toast.show(f"세션 #{sid} 종료됨 — 30초 내 클라이언트 자동 로그아웃")
        await self.action_refresh()

    async def action_sessions_cleanup(self) -> None:
        if self._active_tab() != "tab_sessions":
            self.toast.show("세션 탭에서만 사용할 수 있습니다.")
            return
        if not self._confirm_twice("sessions_cleanup", "stale"):
            return

        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("오래된 세션 정리 중... (heartbeat 30분 이상 끊긴 세션)")
        try:
            result = await asyncio.to_thread(self.api.cleanup_stale_sessions, 1800)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        revoked = int((result or {}).get("revoked_count", 0))
        self.toast.show(f"{revoked}개 stale 세션 일괄 폐기 완료")
        await self.action_refresh()

    async def action_sessions_filter(self) -> None:
        if self._active_tab() != "tab_sessions":
            self.toast.show("세션 탭에서만 사용할 수 있습니다.")
            return
        # 순환: active → stale → revoked → all → active …
        order = ["active", "stale", "revoked", "all"]
        try:
            idx = order.index(self._sessions_status_filter)
        except ValueError:
            idx = -1
        self._sessions_status_filter = order[(idx + 1) % len(order)]
        self.toast.show(f"세션 필터: {self._sessions_status_filter}")
        await self.action_refresh()


def run() -> None:
    import sys

    # Force color in most terminals unless user explicitly disables
    os.environ.pop("NO_COLOR", None)
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "xterm-256color"

    # Diagnostics
    if "--diag" in sys.argv:
        print(f"TERM={os.environ.get('TERM','(unset)')}")
        print(f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM','(unset)')}")
        print(f"stdout.isatty={sys.stdout.isatty()}")
        print(f"stdin.isatty={sys.stdin.isatty()}")
        print(f"textual={__import__('textual').__version__}")
        raise SystemExit(0)

    if not sys.stdout.isatty():
        print("jump-admin-tui: stdout이 TTY가 아닙니다. 일반 터미널에서 실행해 주세요.", file=sys.stderr)
        raise SystemExit(1)

    JumpAdminTui().run()
