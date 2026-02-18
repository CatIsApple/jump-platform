from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
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


class Toast(Static):
    def show(self, msg: str) -> None:
        self.update(msg)


# ---------------------------------------------------------------------------
# Modal: 설정
# ---------------------------------------------------------------------------
class ConfigScreen(ModalScreen[AppConfig | None]):
    BINDINGS = [
        Binding("escape", "cancel", "닫기", show=False),
    ]

    DEFAULT_CSS = """
    ConfigScreen {
      align: center middle;
    }
    #card {
      width: 80%;
      max-width: 88;
      max-height: 90%;
      border: solid #2b2b2b;
      padding: 1 2;
      background: #101010;
    }
    #title {
      text-style: bold;
      margin: 0 0 1 0;
    }
    Input {
      margin: 0 0 1 0;
    }
    .btn-row {
      height: 3;
      margin: 1 0 0 0;
    }
    .btn-row Button {
      margin: 0 1 0 0;
    }
    """

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def on_mount(self) -> None:
        inputs = list(self.query(Input))
        if inputs:
            self.set_focus(inputs[0])

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            yield Label("설정", id="title")
            yield Label("API 기본 URL (예: https://api.example.com)")
            yield Input(value=self.cfg.api_base_url, placeholder="https://api.example.com", id="api_base_url")
            yield Label("Cloudflare Access Client ID")
            yield Input(value=self.cfg.access_client_id, placeholder="CF-Access-Client-Id", id="access_client_id")
            yield Label("Cloudflare Access Client Secret")
            yield Input(value=self.cfg.access_client_secret, password=True, placeholder="CF-Access-Client-Secret", id="access_client_secret")
            yield Label("(선택) X-Admin-Token")
            yield Input(value=self.cfg.admin_token, password=True, placeholder="ADMIN_TOKEN", id="admin_token")
            with Horizontal(classes="btn-row"):
                yield Button("저장  [Enter]", variant="primary", id="save")
                yield Button("취소  [Esc]", id="cancel")

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        self._do_save()

    @on(Input.Submitted)
    def _input_submitted(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        api_base_url = self.query_one("#api_base_url", Input).value.strip()
        access_client_id = self.query_one("#access_client_id", Input).value.strip()
        access_client_secret = self.query_one("#access_client_secret", Input).value.strip()
        admin_token = self.query_one("#admin_token", Input).value.strip()

        if not api_base_url or not api_base_url.startswith("http"):
            self.app.bell()
            return
        if not access_client_id or not access_client_secret:
            self.app.bell()
            return

        cfg = AppConfig(
            api_base_url=api_base_url.rstrip("/"),
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
            admin_token=admin_token,
        )
        save_config(cfg)
        self.dismiss(cfg)

    @on(Button.Pressed, "#cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: 범용 프롬프트 (생성/연장/수정 등)
# ---------------------------------------------------------------------------
class PromptScreen(ModalScreen[dict[str, str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "닫기", show=False),
    ]

    DEFAULT_CSS = """
    PromptScreen {
      align: center middle;
    }
    #card {
      width: 80%;
      max-width: 78;
      max-height: 90%;
      border: solid #2b2b2b;
      padding: 1 2;
      background: #101010;
    }
    #title {
      text-style: bold;
      margin: 0 0 1 0;
    }
    Input {
      margin: 0 0 1 0;
    }
    .btn-row {
      height: 3;
      margin: 1 0 0 0;
    }
    .btn-row Button {
      margin: 0 1 0 0;
    }
    """

    def __init__(self, title: str, fields: list[tuple[str, str, bool]], initial: dict[str, str] | None = None) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._initial = initial or {}

    def on_mount(self) -> None:
        inputs = list(self.query(Input))
        if inputs:
            self.set_focus(inputs[0])

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="card"):
            yield Label(self._title, id="title")
            for key, label, secret in self._fields:
                yield Label(label)
                yield Input(value=self._initial.get(key, ""), password=secret, id=key)
            with Horizontal(classes="btn-row"):
                yield Button("확인  [Enter]", variant="primary", id="ok")
                yield Button("취소  [Esc]", id="cancel")

    @on(Button.Pressed, "#ok")
    def _ok_btn(self) -> None:
        self._do_submit()

    @on(Input.Submitted)
    def _input_submitted(self, event: Input.Submitted) -> None:
        inputs = list(self.query(Input))
        if not inputs:
            self._do_submit()
            return

        try:
            idx = inputs.index(event.input)
        except ValueError:
            self._do_submit()
            return

        if idx < len(inputs) - 1:
            self.set_focus(inputs[idx + 1])
            return

        self._do_submit()

    def _do_submit(self) -> None:
        out: dict[str, str] = {}
        for key, _label, _secret in self._fields:
            out[key] = self.query_one(f"#{key}", Input).value.strip()
        self.dismiss(out)

    @on(Button.Pressed, "#cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal: 확인 다이얼로그 (삭제/폐기 등 위험 동작)
# ---------------------------------------------------------------------------
class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "닫기", show=False),
        Binding("enter", "confirm", "확인", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
      align: center middle;
    }
    #card {
      width: 60%;
      max-width: 60;
      border: solid #2b2b2b;
      padding: 1 2;
      background: #101010;
    }
    #msg {
      margin: 1 0;
    }
    .btn-row {
      height: 3;
      margin: 1 0 0 0;
    }
    .btn-row Button {
      margin: 0 1 0 0;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="card"):
            yield Label(self._message, id="msg")
            with Horizontal(classes="btn-row"):
                yield Button("확인  [Enter]", variant="error", id="yes")
                yield Button("취소  [Esc]", id="no")

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DataRefreshed(Message):
    pass


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class JumpAdminTui(App):
    CSS = """
    #toast {
      height: 1;
      color: #cccccc;
      background: #0f172a;
      padding: 0 1;
    }
    DataTable {
      height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "종료"),
        Binding("r", "refresh", "새로고침"),
        Binding("c", "config", "설정"),
        Binding("n", "license_new", "라이센스 생성"),
        Binding("e", "license_extend", "기간 연장"),
        Binding("s", "license_toggle_suspend", "정지/해제"),
        Binding("x", "license_revoke", "폐기"),
        Binding("shift+e", "domain_edit", "도메인 수정"),
        Binding("E", "domain_edit", show=False),
        Binding("d", "domain_delete", "도메인 삭제"),
    ]

    _GLOBAL_ACTION_KEYS = {
        "quit",
        "refresh",
        "config",
        "license_new",
        "license_extend",
        "license_toggle_suspend",
        "license_revoke",
        "domain_edit",
        "domain_delete",
    }

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.api: JumpAdminApi | None = None
        self.toast = Toast("", id="toast")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container():
            with TabbedContent():
                with TabPane("라이센스", id="tab_licenses"):
                    yield self._licenses_view()
                with TabPane("플랫폼 도메인", id="tab_domains"):
                    yield self._domains_view()
            yield self.toast
        yield Footer()

    def _licenses_view(self) -> DataTable:
        table = DataTable(id="licenses_table", zebra_stripes=True)
        table.add_columns("ID", "업체명", "상태", "만료", "생성", "키 Prefix", "메모")
        return table

    def _domains_view(self) -> DataTable:
        table = DataTable(id="domains_table", zebra_stripes=True)
        table.add_columns("사이트", "도메인", "업데이트")
        return table

    def _active_tab_id(self) -> str:
        try:
            tabs = self.query_one(TabbedContent)
            return str(getattr(tabs, "active", "") or "")
        except Exception:
            return ""

    def _is_modal_open(self) -> bool:
        return isinstance(self.screen, (ConfigScreen, PromptScreen, ConfirmScreen))

    def _is_input_focused(self) -> bool:
        return isinstance(self.focused, Input)

    def _suspend_global_actions(self) -> bool:
        return self._is_modal_open() or self._is_input_focused()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in self._GLOBAL_ACTION_KEYS and self._suspend_global_actions():
            return False
        return None

    # ---- Modal helper (callback-based, compatible with 0.85.x) ----
    async def _show_modal(self, screen: ModalScreen) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        def _on_dismiss(result: Any) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, callback=_on_dismiss)
        return await future

    async def _confirm(self, message: str) -> bool:
        result = await self._show_modal(ConfirmScreen(message))
        return bool(result)

    # ---- API setup ----
    async def on_mount(self) -> None:
        await self._ensure_api()
        await self.action_refresh()

    async def _ensure_api(self) -> None:
        if not (self.cfg.api_base_url and self.cfg.access_client_id and self.cfg.access_client_secret):
            cfg = await self._show_modal(ConfigScreen(self.cfg))
            if cfg is not None:
                self.cfg = cfg
            else:
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

    async def action_config(self) -> None:
        cfg = await self._show_modal(ConfigScreen(self.cfg))
        if cfg is not None:
            self.cfg = cfg
            await self._ensure_api()

    async def action_refresh(self) -> None:
        if not self.api:
            await self._ensure_api()
        if not self.api:
            return

        self.toast.show("불러오는 중...")
        try:
            licenses = await asyncio.to_thread(self.api.list_licenses)
            domains = await asyncio.to_thread(self.api.list_domains)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return

        lic_table = self.query_one("#licenses_table", DataTable)
        lic_table.clear()
        for lic in licenses:
            lic_table.add_row(
                str(lic.get("id", "")),
                str(lic.get("company_name", "")),
                str(lic.get("status", "")),
                _fmt_ts(lic.get("expires_at")),
                _fmt_ts(lic.get("created_at")),
                str(lic.get("key_prefix", "")),
                str(lic.get("note", "")),
                key=str(lic.get("id", "")),
            )

        dom_table = self.query_one("#domains_table", DataTable)
        dom_table.clear()
        for d in domains:
            dom_table.add_row(
                str(d.get("site_key", "")),
                str(d.get("domain", "")),
                _fmt_ts(d.get("updated_at")),
                key=str(d.get("site_key", "")),
            )

        self.toast.show("완료")
        self.post_message(DataRefreshed())

    # ---- Selection helpers ----
    def _selected_license_id(self) -> int | None:
        table = self.query_one("#licenses_table", DataTable)
        if table.row_count == 0:
            return None
        row_index = table.cursor_row
        if row_index < 0 or row_index >= table.row_count:
            return None
        row = table.get_row_at(row_index)
        try:
            return int(str(row[0]))
        except Exception:
            return None

    def _selected_license_status(self) -> str:
        table = self.query_one("#licenses_table", DataTable)
        if table.row_count == 0:
            return ""
        row_index = table.cursor_row
        if row_index < 0 or row_index >= table.row_count:
            return ""
        row = table.get_row_at(row_index)
        return str(row[2]) if len(row) >= 3 else ""

    def _selected_domain_site_key(self) -> str | None:
        table = self.query_one("#domains_table", DataTable)
        if table.row_count == 0:
            return None
        row_index = table.cursor_row
        if row_index < 0 or row_index >= table.row_count:
            return None
        row = table.get_row_at(row_index)
        return str(row[0]) if len(row) >= 1 else None

    # ---- License actions ----
    async def action_license_new(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return
        fields = [
            ("company_name", "업체명", False),
            ("days", "유효기간(일)", False),
            ("note", "메모(선택)", False),
        ]
        data = await self._show_modal(PromptScreen("라이센스 생성", fields, initial={"days": "30"}))
        if data is None:
            return
        company = data.get("company_name", "").strip()
        days_s = data.get("days", "").strip()
        note = data.get("note", "").strip()
        if not company:
            self.toast.show("업체명을 입력하세요.")
            return
        try:
            days = int(days_s)
        except ValueError:
            self.toast.show("유효기간은 숫자여야 합니다.")
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
        self.toast.show(f"생성됨. 라이센스 키(1회): {key}")
        await self.action_refresh()

    async def action_license_extend(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return

        # UX fallback: domains 탭에서는 `e` 키로 도메인 수정 동작
        if self._active_tab_id() == "tab_domains":
            await self.action_domain_edit()
            return

        license_id = self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        data = await self._show_modal(
            PromptScreen("기간 연장", [("days", "추가 일수", False)], initial={"days": "30"})
        )
        if data is None:
            return
        try:
            days = int(data.get("days", "0"))
        except ValueError:
            self.toast.show("일수는 숫자여야 합니다.")
            return

        self.toast.show("연장 중...")
        try:
            await asyncio.to_thread(self.api.extend_license, license_id, days)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return
        self.toast.show("완료")
        await self.action_refresh()

    async def action_license_toggle_suspend(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return
        license_id = self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        status = self._selected_license_status().lower().strip()
        action_name = "정지" if status == "active" else "정지 해제"

        if not await self._confirm(f"라이센스 #{license_id} {action_name} 하시겠습니까?"):
            return

        self.toast.show("처리 중...")
        try:
            if status == "active":
                await asyncio.to_thread(self.api.suspend_license, license_id)
            else:
                await asyncio.to_thread(self.api.resume_license, license_id)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return
        self.toast.show("완료")
        await self.action_refresh()

    async def action_license_revoke(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return
        license_id = self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
            return

        if not await self._confirm(f"라이센스 #{license_id} 을(를) 폐기하시겠습니까? (복구 불가)"):
            return

        self.toast.show("폐기 중...")
        try:
            await asyncio.to_thread(self.api.revoke_license, license_id)
        except ApiError as exc:
            self.toast.show(f"오류: {exc} ({exc.status_code})")
            return
        except Exception as exc:
            self.toast.show(f"오류: {exc}")
            return
        self.toast.show("완료")
        await self.action_refresh()

    # ---- Domain actions ----
    async def action_domain_edit(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return
        site_key = self._selected_domain_site_key()
        if not site_key:
            self.toast.show("선택된 사이트가 없습니다.")
            return
        data = await self._show_modal(
            PromptScreen("도메인 수정", [("domain", f"{site_key} 도메인", False)])
        )
        if data is None:
            return
        domain = data.get("domain", "").strip()
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
        self.toast.show("완료")
        await self.action_refresh()

    async def action_domain_delete(self) -> None:
        if self._suspend_global_actions():
            return
        if not self.api:
            return
        site_key = self._selected_domain_site_key()
        if not site_key:
            self.toast.show("선택된 사이트가 없습니다.")
            return

        if not await self._confirm(f"'{site_key}' 도메인을 삭제하시겠습니까?"):
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


def run() -> None:
    import sys

    # --diag flag: print terminal diagnostics and exit
    if "--diag" in sys.argv:
        print(f"TERM={os.environ.get('TERM', '(unset)')}")
        print(f"COLORTERM={os.environ.get('COLORTERM', '(unset)')}")
        print(f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM', '(unset)')}")
        print(f"NO_COLOR={os.environ.get('NO_COLOR', '(unset)')}")
        print(f"stdout.isatty={sys.stdout.isatty()}")
        print(f"stdin.isatty={sys.stdin.isatty()}")
        print(f"textual version={__import__('textual').__version__}")
        print("\n색상 테스트:")
        print("\033[38;2;255;255;255;48;2;0;0;200m TrueColor 테스트 \033[0m ← 흰 글씨+파란 배경이 보여야 합니다")
        print("\033[38;5;15;48;5;21m 256Color 테스트 \033[0m ← 흰 글씨+파란 배경이 보여야 합니다")
        raise SystemExit(0)

    os.environ.pop("NO_COLOR", None)
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "xterm-256color"

    if not sys.stdout.isatty():
        print("jump-admin-tui: stdout이 TTY가 아닙니다. 일반 터미널에서 실행해 주세요.", file=sys.stderr)
        raise SystemExit(1)

    try:
        JumpAdminTui().run()
    except Exception as exc:
        print(f"jump-admin-tui 오류: {exc}", file=sys.stderr)
        raise SystemExit(1)
