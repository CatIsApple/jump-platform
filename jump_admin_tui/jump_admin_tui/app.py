from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any

from textual import on
from textual.app import App, ComposeResult
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


class ConfigScreen(ModalScreen[AppConfig]):
    DEFAULT_CSS = """
    ConfigScreen {
      align: center middle;
    }
    #card {
      width: 80%;
      max-width: 88;
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
    """

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        with Container(id="card"):
            yield Label("초기 설정", id="title")
            yield Label("API 기본 URL (예: https://api.example.com)")
            yield Input(value=self.cfg.api_base_url, placeholder="https://api.example.com", id="api_base_url")
            yield Label("Cloudflare Access Client ID")
            yield Input(value=self.cfg.access_client_id, placeholder="CF-Access-Client-Id", id="access_client_id")
            yield Label("Cloudflare Access Client Secret")
            yield Input(value=self.cfg.access_client_secret, password=True, placeholder="CF-Access-Client-Secret", id="access_client_secret")
            yield Label("(선택) X-Admin-Token")
            yield Input(value=self.cfg.admin_token, password=True, placeholder="ADMIN_TOKEN", id="admin_token")
            with Horizontal():
                yield Button("저장", variant="primary", id="save")
                yield Button("취소", id="cancel")

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
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
    def _cancel(self) -> None:
        self.dismiss(self.cfg)


class PromptScreen(ModalScreen[dict[str, str]]):
    DEFAULT_CSS = """
    PromptScreen {
      align: center middle;
    }
    #card {
      width: 80%;
      max-width: 78;
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
    """

    def __init__(self, title: str, fields: list[tuple[str, str, bool]], initial: dict[str, str] | None = None) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._initial = initial or {}

    def compose(self) -> ComposeResult:
        with Container(id="card"):
            yield Label(self._title, id="title")
            for key, label, secret in self._fields:
                yield Label(label)
                yield Input(value=self._initial.get(key, ""), password=secret, id=key)
            with Horizontal():
                yield Button("확인", variant="primary", id="ok")
                yield Button("취소", id="cancel")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        out: dict[str, str] = {}
        for key, _label, _secret in self._fields:
            out[key] = self.query_one(f"#{key}", Input).value.strip()
        self.dismiss(out)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss({})


class DataRefreshed(Message):
    pass


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
        ("q", "quit", "종료"),
        ("r", "refresh", "새로고침"),
        ("c", "config", "설정"),
    ]

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

    def _licenses_view(self) -> ComposeResult:
        table = DataTable(id="licenses_table", zebra_stripes=True)
        table.add_columns("ID", "업체명", "상태", "만료", "생성", "키 Prefix", "메모")
        return table

    def _domains_view(self) -> ComposeResult:
        table = DataTable(id="domains_table", zebra_stripes=True)
        table.add_columns("사이트", "도메인", "업데이트")
        return table

    async def on_mount(self) -> None:
        await self._ensure_api()
        await self.action_refresh()

    async def _show_modal(self, screen: Screen[Any]) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        def _on_dismiss(result: Any) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, callback=_on_dismiss)
        return await future

    async def _ensure_api(self) -> None:
        if not (self.cfg.api_base_url and self.cfg.access_client_id and self.cfg.access_client_secret):
            cfg = await self._show_modal(ConfigScreen(self.cfg))
            self.cfg = cfg

        self.api = JumpAdminApi(
            AdminApiConfig(
                base_url=self.cfg.api_base_url,
                access_client_id=self.cfg.access_client_id,
                access_client_secret=self.cfg.access_client_secret,
                admin_token=self.cfg.admin_token,
            )
        )

        # quick health check
        try:
            await asyncio.to_thread(self.api.health)
            self.toast.show("연결됨")
        except Exception as exc:
            self.toast.show(f"연결 실패: {exc}")

    async def action_config(self) -> None:
        cfg = await self._show_modal(ConfigScreen(self.cfg))
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

    # ---- Licenses: key bindings ----
    async def _selected_license_id(self) -> int | None:
        table = self.query_one("#licenses_table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.get_row_key(table.cursor_row)
        try:
            return int(str(row_key))
        except Exception:
            return None

    async def _selected_license_status(self) -> str:
        table = self.query_one("#licenses_table", DataTable)
        if table.row_count == 0:
            return ""
        row = table.get_row_at(table.cursor_row)
        # columns: ID, 업체명, 상태, ...
        return str(row[2]) if len(row) >= 3 else ""

    async def _selected_domain_site_key(self) -> str | None:
        table = self.query_one("#domains_table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.get_row_key(table.cursor_row)
        return str(row_key) if row_key is not None else None

    BINDINGS += [
        ("n", "license_new", "라이센스 생성"),
        ("e", "license_extend", "기간 연장"),
        ("s", "license_toggle_suspend", "정지/해제"),
        ("x", "license_revoke", "폐기"),
        ("E", "domain_edit", "도메인 수정"),
        ("d", "domain_delete", "도메인 삭제"),
    ]

    async def action_license_new(self) -> None:
        if not self.api:
            return
        fields = [
            ("company_name", "업체명", False),
            ("days", "유효기간(일)", False),
            ("note", "메모(선택)", False),
        ]
        data = await self._show_modal(PromptScreen("라이센스 생성", fields, initial={"days": "30"}))
        if not data:
            return
        company = data.get("company_name", "").strip()
        days_s = data.get("days", "").strip()
        note = data.get("note", "").strip()
        try:
            days = int(days_s)
        except ValueError:
            self.toast.show("days는 숫자여야 합니다.")
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
        if not self.api:
            return
        license_id = await self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        data = await self._show_modal(
            PromptScreen("기간 연장", [("days", "추가 일수", False)], initial={"days": "30"})
        )
        if not data:
            return
        try:
            days = int(data.get("days", "0"))
        except ValueError:
            self.toast.show("days는 숫자여야 합니다.")
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
        if not self.api:
            return
        license_id = await self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
            return
        status = (await self._selected_license_status()).lower().strip()

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
        if not self.api:
            return
        license_id = await self._selected_license_id()
        if not license_id:
            self.toast.show("선택된 라이센스가 없습니다.")
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

    # ---- Domains ----
    async def action_domain_edit(self) -> None:
        if not self.api:
            return
        site_key = await self._selected_domain_site_key()
        if not site_key:
            self.toast.show("선택된 사이트가 없습니다.")
            return
        data = await self._show_modal(
            PromptScreen("도메인 수정", [("domain", f"{site_key} 도메인", False)])
        )
        if not data:
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
        if not self.api:
            return
        site_key = await self._selected_domain_site_key()
        if not site_key:
            self.toast.show("선택된 사이트가 없습니다.")
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
    JumpAdminTui().run()
