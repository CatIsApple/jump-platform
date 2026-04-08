from __future__ import annotations

import hashlib
import os
import platform
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from .backend_client import BackendConfig, BackendError, WorkerBackendClient, normalize_base_url
from .db import Database, normalize_time_token
from .engine import WorkerEngine
from .file_manager import artifacts_dir
from .log_bus import LogBus
from .models import Workflow
from .platform_domains import (
    ensure_platform_domains,
    is_platform_enabled,
    load_platform_domains_full,
    platform_domains_path,
    resolve_platform_domain,
    save_platform_domains_full,
)
from .sites import BROWSER_REQUIRED_SITES, SITE_KEYS


# ---------------------------
# Design System
# ---------------------------

COLORS = {
    # Backgrounds (layered depth)
    "bg": "#0a0a0a",
    "sidebar": "#0f0f0f",
    "card": "#151515",
    "card_hover": "#1a1a1a",
    "input": "#1f1f1f",
    "code_bg": "#0d0d0d",

    # Borders
    "border": "#262626",
    "border_soft": "#1f1f1f",

    # Text
    "text": "#ffffff",
    "text_2": "#a1a1aa",
    "text_3": "#6b7280",
    "text_4": "#52525b",

    # Accent
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "accent_soft": "#1e3a5f",
    "accent_muted": "#1e40af",

    # Semantic
    "success": "#22c55e",
    "success_hover": "#16a34a",
    "success_soft": "#14532d",
    "warning": "#f59e0b",
    "warning_soft": "#92400e",
    "error": "#ef4444",
    "error_soft": "#991b1b",
}

# 8px base grid spacing system
SP = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 20,
    "2xl": 24,
}

STYLES = {
    "card_radius": 16,
    "input_radius": 10,
    "button_radius": 8,
    "pill_radius": 8,
    "chip_radius": 16,
    "entry_height": 44,
    "button_height": 40,
    "button_height_sm": 34,
    "nav_height": 48,
}

# Log tag colors (matches sitemap-finder)
LOG_TAG_COLORS = {
    "정보": "#a1a1aa",
    "경고": "#f59e0b",
    "오류": "#ef4444",
    "디버그": "#3b82f6",
}
LOG_TIME_COLOR = "#6b7280"
LOG_SUCCESS_COLOR = "#22c55e"

LEVEL_KOR = {
    "DEBUG": "디버그",
    "INFO": "정보",
    "WARNING": "경고",
    "ERROR": "오류",
}

HARDCODED_BACKEND_URL = "https://api.guardian01.online"
HARDCODED_CAPTCHA_API_KEY = "0d832bea4650d16a3cd7fa6bcb70a06e"
SETTING_BACKEND_BASE_URL = "backend_base_url"
SETTING_BACKEND_LICENSE_KEY = "backend_license_key"
SETTING_BACKEND_DEVICE_ID = "backend_device_id"
SETTING_BACKEND_TOKEN = "backend_token"
SETTING_BACKEND_COMPANY = "backend_company_name"
SETTING_BACKEND_EXPIRES_AT = "backend_expires_at"
SETTING_BACKEND_STATUS = "backend_status"


# ---------------------------
# Font Loading
# ---------------------------

def _resource_base() -> Path:
    # PyInstaller: _MEIPASS points to the temp extraction directory
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    # Nuitka (standalone/onefile): assets are next to the executable
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "assets").is_dir():
        return exe_dir
    # Source / development mode
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> str:
    return str(_resource_base().joinpath(*parts))


def load_pretendard_font() -> Optional[str]:
    font_dir = Path(resource_path("assets", "fonts"))
    regular_path = font_dir / "Pretendard-Regular.otf"
    bold_path = font_dir / "Pretendard-Bold.otf"

    if not regular_path.exists():
        return None

    if sys.platform == "darwin":
        try:
            from Foundation import NSURL  # type: ignore
            from CoreText import CTFontManagerRegisterFontsForURL, kCTFontManagerScopeProcess  # type: ignore

            for p in (regular_path, bold_path):
                if p.exists():
                    url = NSURL.fileURLWithPath_(str(p))
                    CTFontManagerRegisterFontsForURL(url, kCTFontManagerScopeProcess, None)
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            import ctypes

            FR_PRIVATE = 0x10
            for p in (regular_path, bold_path):
                if p.exists():
                    ctypes.windll.gdi32.AddFontResourceExW(str(p), FR_PRIVATE, 0)
        except Exception:
            pass

    return "Pretendard"


FONT_FAMILY = load_pretendard_font() or ("SF Pro Display" if sys.platform == "darwin" else "Segoe UI")
MONO_FONT = "SF Mono" if sys.platform == "darwin" else "Consolas"


# ---------------------------
# Font Helpers
# ---------------------------

def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def _mono_font(size: int = 11) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FONT, size=size)


# ---------------------------
# UI Components
# ---------------------------


@dataclass
class ToastSpec:
    fg: str
    border: str
    icon: str
    icon_color: str


_TOASTS: dict[str, ToastSpec] = {
    "success": ToastSpec(fg=COLORS["card"], border=COLORS["border"], icon="✓", icon_color=COLORS["success"]),
    "error": ToastSpec(fg=COLORS["card"], border=COLORS["border"], icon="✕", icon_color=COLORS["error"]),
    "warning": ToastSpec(fg=COLORS["card"], border=COLORS["border"], icon="!", icon_color=COLORS["warning"]),
    "info": ToastSpec(fg=COLORS["card"], border=COLORS["border"], icon="i", icon_color=COLORS["accent"]),
}


class Toast(ctk.CTkFrame):
    """Sonner-style toast: icon | title + message | close button."""

    def __init__(
        self,
        parent: ctk.CTkBaseClass,
        message: str,
        toast_type: str,
        title: str = "",
        on_close: object = None,
    ) -> None:
        spec = _TOASTS.get(toast_type, _TOASTS["info"])
        super().__init__(
            parent,
            corner_radius=14,
            fg_color=spec.fg,
            border_color=spec.border,
            border_width=1,
        )
        self._on_close = on_close

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=SP["lg"], pady=SP["md"])

        # Icon circle
        icon_bg = ctk.CTkFrame(
            row, width=28, height=28, corner_radius=14,
            fg_color=spec.icon_color,
        )
        icon_bg.pack(side="left", padx=(0, SP["md"]))
        icon_bg.pack_propagate(False)
        ctk.CTkLabel(
            icon_bg, text=spec.icon,
            font=_font(13, "bold"), text_color="#ffffff",
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Text block
        text_block = ctk.CTkFrame(row, fg_color="transparent")
        text_block.pack(side="left", fill="x", expand=True)

        display_title = title or {
            "success": "완료",
            "error": "오류",
            "warning": "주의",
            "info": "알림",
        }.get(toast_type, "알림")

        ctk.CTkLabel(
            text_block, text=display_title,
            font=_font(13, "bold"), text_color=COLORS["text"],
            anchor="w", height=18,
        ).pack(fill="x")

        ctk.CTkLabel(
            text_block, text=message,
            font=_font(14), text_color=COLORS["text_2"],
            anchor="w", wraplength=280, height=16,
        ).pack(fill="x")

        # Close button
        close_btn = ctk.CTkButton(
            row, text="✕", width=24, height=24,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            corner_radius=12, font=_font(14),
            text_color=COLORS["text_4"],
            command=self._close,
        )
        close_btn.pack(side="right", padx=(SP["sm"], 0))

    def _close(self) -> None:
        if callable(self._on_close):
            self._on_close()
        try:
            self.destroy()
        except Exception:
            pass


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent: ctk.CTk, title: str, message: str) -> None:
        super().__init__(parent)
        self._result: Optional[bool] = None
        self.title(title)
        self.geometry("440x210")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        self.transient(parent)
        self.grab_set()

        container = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            border_color=COLORS["border_soft"],
            border_width=1,
            corner_radius=STYLES["card_radius"],
        )
        container.pack(fill="both", expand=True, padx=SP["xl"], pady=SP["xl"])

        ctk.CTkLabel(
            container,
            text=message,
            font=_font(13),
            text_color=COLORS["text"],
            justify="left",
            wraplength=370,
        ).pack(anchor="w", padx=SP["2xl"], pady=(SP["2xl"], SP["lg"]))

        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=SP["2xl"], pady=(0, SP["2xl"]))

        ctk.CTkButton(
            btn_row,
            text="확인",
            height=STYLES["button_height"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self._confirm,
        ).pack(side="right")

        ctk.CTkButton(
            btn_row,
            text="취소",
            height=STYLES["button_height"],
            fg_color=COLORS["input"],
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self._cancel,
        ).pack(side="right", padx=(0, SP["sm"]))

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _confirm(self) -> None:
        self._result = True
        self.destroy()

    def _cancel(self) -> None:
        self._result = False
        self.destroy()

    def result(self) -> bool:
        return bool(self._result)


class LicenseGateDialog(ctk.CTkToplevel):
    def __init__(self, parent: "WorkerDashboardApp") -> None:
        super().__init__(parent)
        self.app = parent
        self._result = False

        self.title("라이센스 로그인")
        self.geometry("500x300")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        if parent.state() != "withdrawn":
            self.transient(parent)
        self.grab_set()
        self.lift()

        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            border_color=COLORS["border_soft"],
            border_width=1,
            corner_radius=STYLES["card_radius"],
        )
        card.pack(fill="both", expand=True, padx=SP["xl"], pady=SP["xl"])
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="라이센스 로그인",
            font=_font(17, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(SP["2xl"], SP["sm"]))

        ctk.CTkLabel(
            card,
            text="라이센스 키",
            font=_font(14),
            text_color=COLORS["text_2"],
        ).grid(row=1, column=0, sticky="w", padx=SP["2xl"], pady=SP["sm"])

        self.entry_license_key = self.app._make_entry(card, placeholder_text="JUMP-...")
        self.entry_license_key.grid(row=1, column=1, sticky="ew", padx=(0, SP["2xl"]), pady=SP["sm"])
        saved_key = self.app.db.get_setting(SETTING_BACKEND_LICENSE_KEY, "")
        if saved_key:
            self.entry_license_key.insert(0, saved_key)

        self.lbl_hint = ctk.CTkLabel(
            card,
            text="앱 사용 전 라이센스 인증이 필요합니다.",
            font=_font(13),
            text_color=COLORS["text_3"],
            anchor="w",
        )
        self.lbl_hint.grid(row=2, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(SP["md"], SP["xs"]))

        self.lbl_status = ctk.CTkLabel(
            card,
            text="",
            font=_font(13),
            text_color=COLORS["error"],
            anchor="w",
            wraplength=470,
            justify="left",
        )
        self.lbl_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(0, SP["sm"]))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=SP["2xl"], pady=(SP["md"], SP["2xl"]))

        ctk.CTkButton(
            btn_row,
            text="종료",
            height=STYLES["button_height"],
            fg_color=COLORS["input"],
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self._on_cancel,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="로그인",
            height=STYLES["button_height"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            font=_font(13, "bold"),
            command=self._on_login,
        ).pack(side="right")

        self.bind("<Return>", self._on_return)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.after(100, self._focus_default_entry)

    def _focus_default_entry(self) -> None:
        try:
            self.entry_license_key.focus_set()
        except Exception:
            pass

    def _save_fields(self) -> None:
        prev_key = self.app.db.get_setting(SETTING_BACKEND_LICENSE_KEY, "").strip()
        new_key = self.entry_license_key.get().strip()

        self.app.db.set_setting(SETTING_BACKEND_BASE_URL, HARDCODED_BACKEND_URL)
        self.app.db.set_setting(SETTING_BACKEND_LICENSE_KEY, new_key)
        if not self.app.db.get_setting(SETTING_BACKEND_DEVICE_ID, "").strip():
            self.app.db.set_setting(SETTING_BACKEND_DEVICE_ID, self.app._default_device_id())

        if new_key != prev_key:
            self.app._clear_backend_session()

    def _on_return(self, _event: object = None) -> None:
        self._on_login()

    def _on_login(self) -> None:
        self._save_fields()
        self.lbl_status.configure(text="", text_color=COLORS["error"])
        self.update_idletasks()

        ok, msg = self.app._backend_login(notify=False)
        if not ok:
            self.app._refresh_license_status_label()
            self.lbl_status.configure(text=msg or "라이센스 로그인 실패")
            return

        ok_sync, msg_sync = self.app._sync_platform_domains_from_backend(notify=False)
        if not ok_sync:
            self.app.log_bus.emit(msg_sync, "WARNING")

        self.app._refresh_license_status_label()
        self._result = True
        self.destroy()

    def _on_cancel(self) -> None:
        self._result = False
        self.destroy()

    def result(self) -> bool:
        return self._result


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent: "WorkerDashboardApp") -> None:
        super().__init__(parent)
        self.app = parent
        self.title("설정")
        self.geometry("620x420")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        self.transient(parent)
        self.grab_set()

        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["card"],
            border_color=COLORS["border_soft"],
            border_width=1,
            corner_radius=STYLES["card_radius"],
        )
        card.pack(fill="both", expand=True, padx=SP["xl"], pady=SP["xl"])
        card.grid_columnconfigure(1, weight=1)

        # Title
        ctk.CTkLabel(
            card,
            text="설정",
            font=_font(16, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(SP["2xl"], SP["lg"]))

        # Poll interval
        ctk.CTkLabel(
            card,
            text="스케줄 폴링 간격(초)",
            font=_font(14),
            text_color=COLORS["text_2"],
        ).grid(row=1, column=0, sticky="w", padx=SP["2xl"], pady=SP["sm"])

        self.entry_poll_interval = self.app._make_entry(card, placeholder_text="예: 1.0 (초)")
        self.entry_poll_interval.grid(row=1, column=1, sticky="ew", padx=(0, SP["2xl"]), pady=SP["sm"])
        self.entry_poll_interval.insert(0, self.app.db.get_setting("poll_interval", "1.0"))

        # Auto start
        # 기본값은 OFF: 앱 실행 직후에는 '중지' 상태가 기본이며, 사용자가 원할 때만 자동 시작.
        self.var_auto_start = ctk.BooleanVar(value=self.app.db.get_setting("auto_start", "0") == "1")
        ctk.CTkCheckBox(
            card,
            text="앱 실행 시 엔진 자동 시작",
            variable=self.var_auto_start,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=_font(13),
            text_color=COLORS["text"],
            corner_radius=6,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(SP["md"], SP["sm"]))

        # Simulation mode
        self.var_simulate = ctk.BooleanVar(value=self.app.db.get_setting("simulate_mode", "0") == "1")
        ctk.CTkCheckBox(
            card,
            text="시뮬레이션 모드 (실제 사이트 접속/자동화 없음)",
            variable=self.var_simulate,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=_font(13),
            text_color=COLORS["text"],
            corner_radius=6,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=SP["2xl"], pady=(0, SP["md"]))

        # Buttons
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=SP["2xl"], pady=(SP["lg"], SP["2xl"]))

        ctk.CTkButton(
            btn_row,
            text="저장 및 적용",
            height=STYLES["button_height"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self._apply,
        ).pack(side="right")

        ctk.CTkButton(
            btn_row,
            text="닫기",
            height=STYLES["button_height"],
            fg_color=COLORS["input"],
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self.destroy,
        ).pack(side="right", padx=(0, SP["sm"]))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _apply(self) -> None:
        try:
            poll_interval = float(self.entry_poll_interval.get().strip())
        except ValueError:
            self.app.toast("설정 값이 숫자 형식이 아닙니다.", "warning")
            return

        if poll_interval < 0.2:
            self.app.toast("폴링 간격은 0.2초 이상이어야 합니다.", "warning")
            return

        self.app.db.set_setting("poll_interval", str(poll_interval))
        self.app.db.set_setting("auto_start", "1" if self.var_auto_start.get() else "0")
        self.app.db.set_setting("simulate_mode", "1" if self.var_simulate.get() else "0")

        was_running = self.app.engine.is_running
        self.app.engine.stop()
        self.app.engine = WorkerEngine(
            db=self.app.db,
            log_bus=self.app.log_bus,
            poll_interval=poll_interval,
        )
        if was_running:
            self.app.engine.start()

        self.app._refresh_license_status_label()
        self.app.toast("설정이 저장되고 적용되었습니다.", "success")
        self.destroy()


# ---------------------------
# Main App
# ---------------------------


class WorkerDashboardApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")

        self.title("작업 대시보드")
        self.geometry("1320x900")
        self.minsize(1160, 780)
        self.configure(fg_color=COLORS["bg"])
        self.withdraw()

        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            # Compiled mode: PyInstaller (sys.frozen) or Nuitka (__compiled__)
            self.base_dir = Path(sys.executable).resolve().parent
            self.data_dir = Path.home() / "jump_worker_dashboard" / "data"
        else:
            self.base_dir = Path(__file__).resolve().parent.parent
            self.data_dir = self.base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["JUMP_WORKER_DATA_DIR"] = str(self.data_dir)

        self.db = Database(self.data_dir / "worker_dashboard.db")
        if not self.db.get_setting(SETTING_BACKEND_DEVICE_ID, "").strip():
            self.db.set_setting(SETTING_BACKEND_DEVICE_ID, self._default_device_id())
        self.log_bus = LogBus()
        # 플랫폼 도메인 매핑 파일(platform_domains.json) 보장 + 기존 워크플로 도메인으로 초기값 채움
        ensure_platform_domains(self.db)

        poll_interval = float(self.db.get_setting("poll_interval", "1.0"))
        self.engine = WorkerEngine(
            db=self.db,
            log_bus=self.log_bus,
            poll_interval=poll_interval,
        )

        self.selected_workflow_id: Optional[int] = None
        self._workflows_cache: list[Workflow] = []
        self._schedule_tokens: list[str] = []
        self._log_lines = deque(maxlen=3500)
        self._tick_count = 0
        self._toast_widget: Optional[Toast] = None

        self.current_view = "ops"

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_container()
        self._build_view_ops()
        self._build_view_scheduler()

        self._refresh_license_status_label()
        self.show_view("ops")
        self._reload_workflow_list()
        self._refresh_stats()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._ui_tick)
        self.after(900, self._startup_with_license_gate)

    # ===== Reusable Widgets =====

    def _card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card"],
            border_width=1,
            border_color=COLORS["border_soft"],
            corner_radius=STYLES["card_radius"],
        )
        card.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=SP["2xl"], pady=(SP["xl"], SP["md"]))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=title,
            font=_font(15, "bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w")

        return card

    def _make_entry(
        self,
        parent: ctk.CTkBaseClass,
        textvariable: Optional[ctk.StringVar] = None,
        placeholder_text: str = "",
        show: Optional[str] = None,
    ) -> ctk.CTkEntry:
        e = ctk.CTkEntry(
            parent,
            height=STYLES["entry_height"],
            textvariable=textvariable,
            placeholder_text=placeholder_text,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            border_width=1,
            corner_radius=STYLES["input_radius"],
            font=_font(13),
            text_color=COLORS["text"],
            placeholder_text_color=COLORS["text_4"],
        )
        if show is not None:
            e.configure(show=show)
        return e

    def _field_label(self, parent: ctk.CTkFrame, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            text_color=COLORS["text_2"],
            font=_font(14),
        )

    def _pill(self, parent: ctk.CTkFrame, text: str, fg: str, tc: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=_font(13),
            text_color=tc,
            fg_color=fg,
            corner_radius=6,
            padx=SP["sm"],
            pady=2,
            height=24,
        )

    def _section_divider(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        div = ctk.CTkFrame(parent, height=1, fg_color=COLORS["border_soft"])
        return div

    def _section_title(self, parent: ctk.CTkFrame, text: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            frame,
            text=text,
            font=_font(14, "bold"),
            text_color=COLORS["text_3"],
        ).pack(side="left")
        return frame

    # ===== Backend / License =====

    def _startup_with_license_gate(self) -> None:
        authenticated = False

        self.db.set_setting(SETTING_BACKEND_BASE_URL, HARDCODED_BACKEND_URL)
        license_key = self.db.get_setting(SETTING_BACKEND_LICENSE_KEY, "").strip()
        if license_key:
            ok, msg = self._backend_login(notify=False)
            if ok:
                authenticated = True
                ok_sync, msg_sync = self._sync_platform_domains_from_backend(notify=False)
                if not ok_sync:
                    self.log_bus.emit(msg_sync, "WARNING")
            else:
                self.log_bus.emit(f"백엔드 로그인 실패: {msg}", "WARNING")

        if not authenticated:
            gate = LicenseGateDialog(self)
            self.wait_window(gate)
            if not gate.result():
                self.destroy()
                return

        self._refresh_license_status_label()
        self.deiconify()
        self.lift()
        self.focus_force()

        if self.db.get_setting("auto_start", "0") == "1":
            self.engine.start()

    def _default_device_id(self) -> str:
        raw = f"{platform.node()}|{platform.system()}|{platform.machine()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"jump-{digest}"

    def _format_license_expiry(self, expires_at: str) -> str:
        text = (expires_at or "").strip()
        if not text:
            return "-"
        try:
            ts = int(text)
        except ValueError:
            return "-"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

    def _backend_client(self) -> WorkerBackendClient | None:
        return WorkerBackendClient(BackendConfig(base_url=HARDCODED_BACKEND_URL))

    def _clear_backend_session(self) -> None:
        self.db.set_setting(SETTING_BACKEND_TOKEN, "")
        self.db.set_setting(SETTING_BACKEND_COMPANY, "")
        self.db.set_setting(SETTING_BACKEND_EXPIRES_AT, "")
        self.db.set_setting(SETTING_BACKEND_STATUS, "")

    def _refresh_license_status_label(self) -> None:
        token = self.db.get_setting(SETTING_BACKEND_TOKEN, "").strip()
        company = self.db.get_setting(SETTING_BACKEND_COMPANY, "").strip()
        status = self.db.get_setting(SETTING_BACKEND_STATUS, "").strip()
        expires_text = self._format_license_expiry(self.db.get_setting(SETTING_BACKEND_EXPIRES_AT, ""))

        if token:
            STATUS_MAP = {"active": ("활성", COLORS["success"]), "suspended": ("정지", COLORS["warning"]), "revoked": ("폐기", COLORS["error"])}
            state_label, state_color = STATUS_MAP.get(status, (status or "unknown", COLORS["text_3"]))
            self.sidebar_lic_company.configure(text=f"업체: {company or '-'}", text_color=COLORS["text"])
            self.sidebar_lic_status.configure(text=f"상태: {state_label}", text_color=state_color)
            self.sidebar_lic_expiry.configure(text=f"만료: {expires_text}", text_color=COLORS["text_3"])
        else:
            self.sidebar_lic_company.configure(text="라이센스: 미로그인", text_color=COLORS["text_4"])
            self.sidebar_lic_status.configure(text="")
            self.sidebar_lic_expiry.configure(text="")

    def _backend_login(self, *, notify: bool = True) -> tuple[bool, str]:
        client = self._backend_client()
        if client is None:
            msg = "백엔드 URL이 설정되지 않았습니다."
            if notify:
                self.toast(msg, "warning")
            return False, msg

        license_key = self.db.get_setting(SETTING_BACKEND_LICENSE_KEY, "").strip()
        if not license_key:
            msg = "라이센스 키가 비어 있습니다."
            if notify:
                self.toast(msg, "warning")
            return False, msg

        device_id = self.db.get_setting(SETTING_BACKEND_DEVICE_ID, "").strip() or self._default_device_id()
        self.db.set_setting(SETTING_BACKEND_DEVICE_ID, device_id)

        try:
            data = client.login(license_key=license_key, device_id=device_id)
        except BackendError as exc:
            msg = str(exc)
            if exc.status_code in (401, 403):
                self._clear_backend_session()
                self._refresh_license_status_label()
            if notify:
                self.toast(msg, "error")
            return False, msg
        except Exception as exc:  # noqa: BLE001
            msg = f"라이센스 로그인 실패: {exc}"
            if notify:
                self.toast(msg, "error")
            return False, msg

        token = str(data.get("token") or "").strip()
        lic = data.get("license") if isinstance(data.get("license"), dict) else {}
        if not token:
            msg = "로그인 응답에 토큰이 없습니다."
            if notify:
                self.toast(msg, "error")
            return False, msg

        self.db.set_setting(SETTING_BACKEND_TOKEN, token)
        self.db.set_setting(SETTING_BACKEND_COMPANY, str(lic.get("company_name") or "").strip())
        self.db.set_setting(SETTING_BACKEND_EXPIRES_AT, str(lic.get("expires_at") or "").strip())
        self.db.set_setting(SETTING_BACKEND_STATUS, str(lic.get("status") or "").strip())
        self._refresh_license_status_label()

        msg = "라이센스 로그인 성공"
        if notify:
            self.toast(msg, "success")
        return True, msg

    def _backend_logout(self, *, notify: bool = True) -> tuple[bool, str]:
        token = self.db.get_setting(SETTING_BACKEND_TOKEN, "").strip()
        client = self._backend_client()

        if token and client is not None:
            try:
                client.logout(token)
            except Exception:
                # 서버 응답과 무관하게 로컬 세션은 정리한다.
                pass

        self._clear_backend_session()
        self._refresh_license_status_label()
        msg = "라이센스 로그아웃 완료"
        if notify:
            self.toast(msg, "success")
        return True, msg

    def _apply_domains_to_workflows(self, mapping: dict[str, str]) -> int:
        changed = 0
        for wf in self.db.list_workflows():
            # 비활성화된 사이트는 도메인 비우기
            if not is_platform_enabled(wf.site_key):
                if (wf.domain or "").strip():
                    wf.domain = ""
                    self.db.save_workflow(wf)
                    changed += 1
                continue
            new_domain = (mapping.get(wf.site_key) or "").strip()
            if not new_domain or new_domain == (wf.domain or "").strip():
                continue
            wf.domain = new_domain
            self.db.save_workflow(wf)
            changed += 1
        return changed

    def _sync_platform_domains_from_backend(self, *, notify: bool = True) -> tuple[bool, str]:
        client = self._backend_client()
        if client is None:
            msg = "백엔드 URL이 설정되지 않았습니다."
            if notify:
                self.toast(msg, "warning")
            return False, msg

        token = self.db.get_setting(SETTING_BACKEND_TOKEN, "").strip()
        if not token:
            ok, _ = self._backend_login(notify=False)
            if not ok:
                msg = "도메인 동기화 전에 라이센스 로그인이 필요합니다."
                if notify:
                    self.toast(msg, "error")
                return False, msg
            token = self.db.get_setting(SETTING_BACKEND_TOKEN, "").strip()

        last_exc: Exception | None = None
        data: dict[str, object] | None = None
        for attempt in range(2):
            try:
                data = client.platform_domains(token)
                break
            except BackendError as exc:
                last_exc = exc
                if exc.status_code in (401, 403) and attempt == 0:
                    self._clear_backend_session()
                    ok, _ = self._backend_login(notify=False)
                    if ok:
                        token = self.db.get_setting(SETTING_BACKEND_TOKEN, "").strip()
                        continue
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                break

        if data is None:
            msg = f"도메인 동기화 실패: {last_exc}" if last_exc else "도메인 동기화 실패"
            if notify:
                self.toast(msg, "error")
            return False, msg

        domains_raw = data.get("domains") if isinstance(data, dict) else None
        if not isinstance(domains_raw, dict):
            msg = "도메인 동기화 실패: domains 형식이 올바르지 않습니다."
            if notify:
                self.toast(msg, "error")
            return False, msg

        # 기존 로컬 enabled 상태 보존
        existing_full = load_platform_domains_full()

        mapping: dict[str, str] = {}
        full_mapping: dict[str, dict] = {}
        for k, v in domains_raw.items():
            if not isinstance(k, str):
                continue
            key = k.strip()
            if not key:
                continue
            # SITE_KEYS에 없는 키는 무시 (예: "오피가이드(개구리)" 등)
            if key not in SITE_KEYS:
                continue
            domain = str(v).strip() if v is not None else ""
            mapping[key] = domain
            # 로컬에 enabled 상태가 있으면 보존, 없으면 True
            local_enabled = existing_full.get(key, {}).get("enabled", True)
            full_mapping[key] = {"domain": domain, "enabled": local_enabled}

        save_platform_domains_full(full_mapping)
        changed = self._apply_domains_to_workflows(mapping)

        self._refresh_site_option_menu()
        self._sync_domain_from_platform()
        self._reload_workflow_list()
        self._refresh_license_status_label()

        msg = f"도메인 {len(mapping)}개 동기화 완료 (작업 {changed}개 갱신)"
        self.log_bus.emit(msg, "INFO")
        if notify:
            self.toast(msg, "success")
        return True, msg

    def _bootstrap_backend_sync(self) -> None:
        base_url = normalize_base_url(self.db.get_setting(SETTING_BACKEND_BASE_URL, ""))
        license_key = self.db.get_setting(SETTING_BACKEND_LICENSE_KEY, "").strip()
        if not base_url or not license_key:
            self._refresh_license_status_label()
            return

        ok, msg = self._backend_login(notify=False)
        if not ok:
            self.log_bus.emit(f"백엔드 로그인 실패: {msg}", "WARNING")
            return

        ok, msg = self._sync_platform_domains_from_backend(notify=False)
        if not ok:
            self.log_bus.emit(msg, "WARNING")

    # ===== Sidebar =====

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=280,
            fg_color=COLORS["sidebar"],
            corner_radius=0,
            border_width=0,
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        # Separator line on right edge
        sep = ctk.CTkFrame(sidebar, width=1, fg_color=COLORS["border_soft"])
        sep.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # Brand
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=SP["2xl"], pady=(32, SP["md"]))

        ctk.CTkLabel(
            brand,
            text="GUARDIAN",
            font=_font(20, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="점프 자동화 프로그램",
            font=_font(13),
            text_color=COLORS["text_3"],
        ).pack(anchor="w", pady=(SP["xs"], 0))

        self._section_divider(sidebar).pack(fill="x", padx=SP["2xl"], pady=(0, SP["xl"]))

        # Navigation
        nav = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav.pack(fill="x", padx=SP["lg"], pady=(0, SP["lg"]))

        self.btn_nav_ops = ctk.CTkButton(
            nav,
            text="  메인",
            height=STYLES["nav_height"],
            anchor="w",
            fg_color=COLORS["accent_soft"],
            hover_color=COLORS["card_hover"],
            text_color=COLORS["accent"],
            corner_radius=STYLES["input_radius"],
            font=_font(14),
            command=lambda: self.show_view("ops"),
        )
        self.btn_nav_ops.pack(fill="x", pady=(0, SP["xs"]))

        self.btn_nav_sched = ctk.CTkButton(
            nav,
            text="  작업 설정",
            height=STYLES["nav_height"],
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["text_3"],
            corner_radius=STYLES["input_radius"],
            font=_font(14),
            command=lambda: self.show_view("scheduler"),
        )
        self.btn_nav_sched.pack(fill="x")

        # Engine status
        self._section_divider(sidebar).pack(fill="x", padx=SP["2xl"], pady=(SP["lg"], SP["lg"]))

        info = ctk.CTkFrame(sidebar, fg_color="transparent")
        info.pack(fill="x", padx=SP["2xl"])

        ctk.CTkLabel(
            info,
            text="상태",
            font=_font(14),
            text_color=COLORS["text_4"],
        ).pack(anchor="w", pady=(0, SP["sm"]))

        self.sidebar_state = ctk.CTkLabel(
            info,
            text="엔진: 중지",
            font=_font(14, "bold"),
            text_color=COLORS["error"],
        )
        self.sidebar_state.pack(anchor="w")

        self.sidebar_queue = ctk.CTkLabel(
            info,
            text="대기열: 0",
            font=_font(14),
            text_color=COLORS["text_3"],
        )
        self.sidebar_queue.pack(anchor="w", pady=(SP["xs"], 0))

        self.sidebar_current = ctk.CTkLabel(
            info,
            text="현재: -",
            font=_font(14),
            text_color=COLORS["text_3"],
            wraplength=230,
            justify="left",
        )
        self.sidebar_current.pack(anchor="w", pady=(SP["xs"], 0))

        # License info block
        lic_frame = ctk.CTkFrame(info, fg_color="transparent")
        lic_frame.pack(anchor="w", fill="x", pady=(SP["md"], 0))

        self.sidebar_lic_company = ctk.CTkLabel(
            lic_frame,
            text="미로그인",
            font=_font(14, "bold"),
            text_color=COLORS["text_4"],
        )
        self.sidebar_lic_company.pack(anchor="w")

        self.sidebar_lic_status = ctk.CTkLabel(
            lic_frame,
            text="",
            font=_font(14),
            text_color=COLORS["text_4"],
        )
        self.sidebar_lic_status.pack(anchor="w", pady=(2, 0))

        self.sidebar_lic_expiry = ctk.CTkLabel(
            lic_frame,
            text="",
            font=_font(14),
            text_color=COLORS["text_4"],
        )
        self.sidebar_lic_expiry.pack(anchor="w", pady=(2, 0))

        # Bottom
        bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=SP["lg"], pady=SP["xl"])

        self._section_divider(bottom).pack(fill="x", padx=SP["sm"], pady=(0, SP["lg"]))

        ctk.CTkButton(
            bottom,
            text="설정",
            height=STYLES["button_height"],
            fg_color=COLORS["input"],
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            text_color=COLORS["text_2"],
            command=self._open_settings,
        ).pack(fill="x", padx=SP["sm"], pady=(0, SP["md"]))

        ctk.CTkButton(
            bottom,
            text="저장 폴더",
            height=STYLES["button_height"],
            fg_color=COLORS["input"],
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            text_color=COLORS["text_2"],
            command=self._open_artifacts_dir,
        ).pack(fill="x", padx=SP["sm"], pady=(0, SP["md"]))

        ctk.CTkButton(
            bottom,
            text="로그아웃",
            height=STYLES["button_height"],
            fg_color="#251414",
            hover_color="#3b1a1a",
            border_width=1,
            border_color="#3c1a1a",
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            text_color="#ef4444",
            command=self._backend_logout,
        ).pack(fill="x", padx=SP["sm"], pady=(0, SP["md"]))

        ctk.CTkLabel(
            bottom,
            text="v0.3.0 by Da0nn",
            font=_font(10),
            text_color=COLORS["text_4"],
        ).pack(anchor="w", padx=SP["sm"])

    # ===== Container =====

    def _build_container(self) -> None:
        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self.container.grid(row=0, column=1, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.view_ops = ctk.CTkFrame(self.container, fg_color="transparent")
        self.view_ops.grid(row=0, column=0, sticky="nsew")
        self.view_ops.grid_columnconfigure(0, weight=1)
        self.view_ops.grid_rowconfigure(2, weight=1)

        self.view_scheduler = ctk.CTkFrame(self.container, fg_color="transparent")
        self.view_scheduler.grid(row=0, column=0, sticky="nsew")
        self.view_scheduler.grid_columnconfigure(0, weight=1)
        self.view_scheduler.grid_rowconfigure(1, weight=1)

    def show_view(self, name: str) -> None:
        if name not in ("ops", "scheduler"):
            return
        self.current_view = name

        if name == "ops":
            self.view_ops.tkraise()
            self.btn_nav_ops.configure(fg_color=COLORS["accent_soft"], text_color=COLORS["accent"])
            self.btn_nav_sched.configure(fg_color="transparent", text_color=COLORS["text_3"])
            self._on_activity_mode_change()
        else:
            self.view_scheduler.tkraise()
            self.btn_nav_ops.configure(fg_color="transparent", text_color=COLORS["text_3"])
            self.btn_nav_sched.configure(fg_color=COLORS["accent_soft"], text_color=COLORS["accent"])
            self._reload_workflow_list()

    # ===== View: Operations =====

    def _build_view_ops(self) -> None:
        # Header
        header = ctk.CTkFrame(self.view_ops, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(30, SP["lg"]))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(
            left,
            text="메인",
            font=_font(26, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            left,
            text="엔진 상태와 실행 로그를 확인합니다.",
            font=_font(14),
            text_color=COLORS["text_3"],
        ).pack(anchor="w", pady=(SP["xs"], 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")

        self.ops_pill_time = ctk.CTkLabel(
            right, text="",
            font=_mono_font(14), text_color=COLORS["text_4"],
        )
        self.ops_pill_time.pack(side="left", padx=(0, SP["lg"]))

        self._header_engine_dot = ctk.CTkFrame(
            right, width=6, height=6, corner_radius=3,
            fg_color=COLORS["error"],
        )
        self._header_engine_dot.pack(side="left", padx=(0, SP["xs"]))
        self._header_engine_dot.pack_propagate(False)

        self.ops_pill_engine = ctk.CTkLabel(
            right, text="중지",
            font=_font(14), text_color=COLORS["text_3"],
        )
        self.ops_pill_engine.pack(side="left")

        # Top cards row
        top = ctk.CTkFrame(self.view_ops, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", padx=30, pady=(0, SP["lg"]))
        top.grid_columnconfigure(0, weight=3)
        top.grid_columnconfigure(1, weight=2)

        # Card: Engine control & status (compact inline)
        card_engine = self._card(top, "")
        card_engine.grid(row=0, column=0, sticky="nsew", padx=(0, SP["sm"]))
        card_engine.grid_columnconfigure(0, weight=1)
        # Hide the empty title row
        for w in card_engine.grid_slaves(row=0):
            w.grid_forget()

        # -- Status bar: dot + state + buttons inline --
        status_bar = ctk.CTkFrame(card_engine, fg_color="transparent")
        status_bar.grid(row=0, column=0, sticky="ew", padx=SP["2xl"], pady=(SP["xl"], SP["md"]))

        self._engine_dot = ctk.CTkFrame(
            status_bar, width=8, height=8, corner_radius=4,
            fg_color=COLORS["error"],
        )
        self._engine_dot.pack(side="left", padx=(0, SP["sm"]))
        self._engine_dot.pack_propagate(False)

        self._engine_state_lbl = ctk.CTkLabel(
            status_bar, text="중지",
            font=_font(14, "bold"), text_color=COLORS["text"],
        )
        self._engine_state_lbl.pack(side="left")

        # Action buttons (right-aligned, conditional visibility)
        btn_h = STYLES["button_height_sm"]

        self.btn_engine_start = ctk.CTkButton(
            status_bar, text="시작",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["pill_radius"], font=_font(14),
            command=self._start_engine, width=52, height=btn_h,
        )
        self.btn_engine_start.pack(side="right", padx=(SP["xs"], 0))

        self.btn_engine_stop = ctk.CTkButton(
            status_bar, text="중지",
            fg_color=COLORS["error_soft"], hover_color=COLORS["error"],
            corner_radius=STYLES["pill_radius"], font=_font(14),
            text_color="#fca5a5",
            command=self._stop_engine, width=52, height=btn_h,
        )

        self.btn_engine_restart = ctk.CTkButton(
            status_bar, text="재시작",
            fg_color=COLORS["accent_soft"], hover_color=COLORS["accent_muted"],
            corner_radius=STYLES["pill_radius"], font=_font(14),
            text_color="#93c5fd",
            command=self._restart_engine, width=64, height=btn_h,
        )

        # -- Status details (compact grid) --
        self._section_divider(card_engine).grid(
            row=1, column=0, sticky="ew", padx=SP["2xl"], pady=(0, SP["sm"]),
        )

        detail = ctk.CTkFrame(card_engine, fg_color="transparent")
        detail.grid(row=2, column=0, sticky="ew", padx=SP["2xl"], pady=(0, SP["xl"]))
        detail.grid_columnconfigure(1, weight=1)

        def _detail_row(parent: ctk.CTkFrame, label: str, r: int) -> ctk.CTkLabel:
            ctk.CTkLabel(
                parent, text=label,
                font=_font(13), text_color=COLORS["text_4"],
                width=72, anchor="w",
            ).grid(row=r, column=0, sticky="w", pady=(0, 4))
            val = ctk.CTkLabel(
                parent, text="-",
                font=_font(14), text_color=COLORS["text_2"],
                anchor="w",
            )
            val.grid(row=r, column=1, sticky="ew", pady=(0, 4))
            return val

        self.lbl_worker_current = _detail_row(detail, "현재 작업", 0)
        self.lbl_worker_queue = _detail_row(detail, "대기열", 1)
        self.lbl_worker_last = _detail_row(detail, "최근 결과", 2)

        self._engine_hint_default = ""
        self.lbl_engine_hint = ctk.CTkLabel(
            card_engine, text="",
            font=_font(13), text_color=COLORS["text_3"], justify="left",
        )
        self.lbl_engine_hint.grid(row=3, column=0, sticky="w", padx=SP["2xl"], pady=(0, SP["lg"]))

        # Card: Stats
        card_stats = self._card(top, "요약")
        card_stats.grid(row=0, column=1, sticky="nsew", padx=(SP["sm"], 0))

        # 요약 헤더에 초기화 버튼 추가
        stats_header = card_stats.grid_slaves(row=0, column=0)[0]
        ctk.CTkButton(
            stats_header,
            text="초기화",
            width=52,
            height=24,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["pill_radius"],
            font=_font(14),
            text_color=COLORS["text_4"],
            command=self._on_stats_reset,
        ).grid(row=0, column=1, sticky="e")

        stats_body = ctk.CTkFrame(card_stats, fg_color="transparent")
        stats_body.grid(row=1, column=0, sticky="nsew", padx=SP["2xl"], pady=(0, SP["2xl"]))

        def _stat_item(parent: ctk.CTkFrame, label: str, dot_color: str) -> ctk.CTkLabel:
            row_f = ctk.CTkFrame(parent, fg_color="transparent")
            row_f.pack(fill="x", pady=(0, 6))
            dot = ctk.CTkFrame(row_f, width=6, height=6, corner_radius=3, fg_color=dot_color)
            dot.pack(side="left", padx=(0, SP["sm"]))
            dot.pack_propagate(False)
            ctk.CTkLabel(row_f, text=label, font=_font(14), text_color=COLORS["text_3"]).pack(side="left")
            val = ctk.CTkLabel(row_f, text="0", font=_font(14, "bold"), text_color=COLORS["text"])
            val.pack(side="right")
            return val

        self.stat_total = _stat_item(stats_body, "전체 작업", COLORS["text_3"])
        self.stat_enabled = _stat_item(stats_body, "사용 중", COLORS["accent"])

        self._section_divider(stats_body).pack(fill="x", pady=(4, 8))

        self.stat_success = _stat_item(stats_body, "성공 (24h)", COLORS["success"])
        self.stat_blocked = _stat_item(stats_body, "차단됨 (24h)", COLORS["warning"])
        self.stat_unknown = _stat_item(stats_body, "확인 불가 (24h)", COLORS["text_3"])
        self.stat_cooldown = _stat_item(stats_body, "대기 룰 (24h)", COLORS["warning"])
        self.stat_insufficient = _stat_item(stats_body, "부족 (24h)", COLORS["warning"])
        self.stat_login_required = _stat_item(stats_body, "로그인 필요 (24h)", COLORS["error"])
        self.stat_fail = _stat_item(stats_body, "실패 (24h)", COLORS["error"])

        # Activity card (tabs + textbox)
        card_activity = self._card(self.view_ops, "")
        card_activity.grid(row=2, column=0, sticky="nsew", padx=30, pady=(0, 30))
        card_activity.grid_columnconfigure(0, weight=1)
        card_activity.grid_rowconfigure(2, weight=1)
        # Hide empty title
        for w in card_activity.grid_slaves(row=0):
            w.grid_forget()

        # -- Tab bar --
        tab_bar = ctk.CTkFrame(card_activity, fg_color="transparent")
        tab_bar.grid(row=0, column=0, sticky="ew", padx=SP["2xl"], pady=(SP["lg"], 0))

        self.activity_mode = ctk.StringVar(value="실시간 로그")

        def _make_tab(parent: ctk.CTkFrame, label: str, value: str) -> ctk.CTkButton:
            is_active = value == "실시간 로그"
            btn = ctk.CTkButton(
                parent, text=label,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                corner_radius=0, height=32, width=0,
                font=_font(13, "bold") if is_active else _font(13),
                text_color=COLORS["text"] if is_active else COLORS["text_4"],
                command=lambda: self._switch_activity_tab(value),
            )
            return btn

        self._tab_log = _make_tab(tab_bar, "실시간 로그", "실시간 로그")
        self._tab_log.pack(side="left")

        self._tab_history = _make_tab(tab_bar, "실행 기록", "실행 기록")
        self._tab_history.pack(side="left", padx=(SP["lg"], 0))

        # Active tab underline
        self._tab_underline = ctk.CTkFrame(
            tab_bar, height=2, width=0, fg_color=COLORS["accent"],
        )

        self.activity_action_btn = ctk.CTkButton(
            tab_bar,
            text="지우기",
            width=64,
            height=28,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["pill_radius"],
            font=_font(13),
            text_color=COLORS["text_4"],
            command=self._on_activity_action_clicked,
        )
        self.activity_action_btn.pack(side="right")

        self.activity_copy_btn = ctk.CTkButton(
            tab_bar,
            text="복사",
            width=52,
            height=28,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["pill_radius"],
            font=_font(13),
            text_color=COLORS["text_4"],
            command=self._on_activity_copy_clicked,
        )
        self.activity_copy_btn.pack(side="right", padx=(0, SP["sm"]))

        self.activity_reset_btn = ctk.CTkButton(
            tab_bar,
            text="초기화",
            width=52,
            height=28,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["pill_radius"],
            font=_font(13),
            text_color=COLORS["text_4"],
            command=self._on_activity_reset,
        )
        self.activity_reset_btn.pack(side="right", padx=(0, SP["sm"]))

        # Divider below tabs
        self._section_divider(card_activity).grid(
            row=1, column=0, sticky="ew", padx=SP["2xl"], pady=(SP["xs"], SP["md"]),
        )

        self.activity_text = ctk.CTkTextbox(
            card_activity,
            fg_color=COLORS["code_bg"],
            border_width=1,
            border_color=COLORS["border_soft"],
            corner_radius=STYLES["input_radius"],
            font=_mono_font(13),
            text_color=COLORS["text_2"],
        )
        self.activity_text.grid(row=2, column=0, sticky="nsew", padx=SP["2xl"], pady=(0, SP["2xl"]))

        # Configure log tag colors
        tb = self.activity_text._textbox  # underlying tk.Text
        tb.tag_configure("time", foreground=LOG_TIME_COLOR)
        tb.tag_configure("tag_info", foreground=LOG_TAG_COLORS["정보"])
        tb.tag_configure("tag_warn", foreground=LOG_TAG_COLORS["경고"])
        tb.tag_configure("tag_error", foreground=LOG_TAG_COLORS["오류"])
        tb.tag_configure("tag_debug", foreground=LOG_TAG_COLORS["디버그"])
        tb.tag_configure("msg_success", foreground=LOG_SUCCESS_COLOR)
        tb.tag_configure("msg_error", foreground=COLORS["error"])
        tb.tag_configure("msg_warn", foreground=COLORS["warning"])

        # Position underline after initial render
        self.after(50, lambda: self._update_tab_underline("실시간 로그"))

    # ===== View: Scheduler =====

    def _build_view_scheduler(self) -> None:
        # Header
        header = ctk.CTkFrame(self.view_scheduler, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(30, SP["lg"]))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(
            left,
            text="스케줄러",
            font=_font(26, "bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            left,
            text="작업(사이트/도메인/계정)과 스케줄 시간을 등록합니다.",
            font=_font(14),
            text_color=COLORS["text_3"],
        ).pack(anchor="w", pady=(SP["xs"], 0))

        # Body
        body = ctk.CTkFrame(self.view_scheduler, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 30))
        body.grid_columnconfigure((0, 1), weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left: workflow list
        self.card_list = self._card(body, "작업 목록")
        self.card_list.grid(row=0, column=0, sticky="nsew", padx=(0, SP["sm"]))
        self.card_list.grid_rowconfigure(2, weight=1)
        self.card_list.grid_columnconfigure(0, weight=1)

        list_top = ctk.CTkFrame(self.card_list, fg_color="transparent")
        list_top.grid(row=1, column=0, sticky="ew", padx=SP["2xl"], pady=(0, SP["md"]))
        list_top.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar(value="")
        self.entry_search = self._make_entry(
            list_top,
            textvariable=self.search_var,
            placeholder_text="이름/도메인/사이트 검색",
        )
        self.entry_search.grid(row=0, column=0, sticky="ew")
        self.entry_search.bind("<KeyRelease>", lambda _e: self._reload_workflow_list())

        ctk.CTkButton(
            list_top,
            text="+ 새 작업",
            width=92,
            height=STYLES["entry_height"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            font=_font(13),
            command=self._new_workflow,
        ).grid(row=0, column=1, padx=(SP["sm"], 0))

        self.list_scroll = ctk.CTkScrollableFrame(
            self.card_list,
            fg_color="transparent",
        )
        self.list_scroll.grid(row=2, column=0, sticky="nsew", padx=SP["lg"], pady=(0, SP["2xl"]))
        self.list_scroll.grid_columnconfigure(0, weight=1)

        # Right: editor
        self.card_edit = self._card(body, "작업 설정")
        self.card_edit.grid(row=0, column=1, sticky="nsew", padx=(SP["sm"], 0))
        self.card_edit.grid_columnconfigure(0, weight=1)
        self.card_edit.grid_rowconfigure(1, weight=1)

        self._build_editor(self.card_edit)

    def _build_editor(self, card: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=SP["sm"], pady=(0, SP["sm"]))
        scroll.grid_columnconfigure(0, weight=1)

        content = ctk.CTkFrame(scroll, fg_color="transparent")
        content.pack(fill="x", padx=SP["md"])
        content.grid_columnconfigure(1, weight=1)

        self.var_name = ctk.StringVar()
        self.var_site = ctk.StringVar(value=SITE_KEYS[0])
        self.var_domain = ctk.StringVar()
        self.var_shop = ctk.StringVar()
        self.var_user = ctk.StringVar()
        self.var_pass = ctk.StringVar()
        self.var_enabled = ctk.BooleanVar(value=True)
        self.var_use_browser = ctk.BooleanVar(value=True)

        row = 0
        field_pady = (0, SP["md"])
        label_padx = (0, SP["lg"])

        # -- 기본 정보 --
        self._section_title(content, "기본 정보").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, SP["md"]),
        )
        row += 1

        self._field_label(content, "작업 이름").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)
        self.entry_name = self._make_entry(content, textvariable=self.var_name, placeholder_text="예: 오피가이드 점프")
        self.entry_name.grid(row=row, column=1, sticky="ew", pady=field_pady)
        row += 1

        self._field_label(content, "사이트").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)
        self.option_site = ctk.CTkOptionMenu(
            content,
            values=self._get_synced_site_keys(),
            variable=self.var_site,
            command=self._on_site_selected,
            height=STYLES["entry_height"],
            corner_radius=STYLES["input_radius"],
            fg_color=COLORS["input"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["text_4"],
            dropdown_fg_color=COLORS["card"],
            dropdown_hover_color=COLORS["card_hover"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"],
            font=_font(13),
        )
        self.option_site.grid(row=row, column=1, sticky="ew", pady=field_pady)
        row += 1

        self._field_label(content, "도메인").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)

        domain_row = ctk.CTkFrame(content, fg_color="transparent")
        domain_row.grid(row=row, column=1, sticky="ew", pady=field_pady)
        domain_row.grid_columnconfigure(0, weight=1)

        self.entry_domain = self._make_entry(
            domain_row,
            textvariable=self.var_domain,
            placeholder_text="서버 동기화 시 자동 설정됩니다.",
        )
        self.entry_domain.grid(row=0, column=0, sticky="ew")
        try:
            self.entry_domain.configure(state="disabled")
        except Exception:
            pass

        ctk.CTkButton(
            domain_row,
            text="서버 동기화",
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["input_radius"],
            font=_font(14),
            text_color=COLORS["text_2"],
            command=self._sync_platform_domains_from_backend,
            width=104,
            height=STYLES["entry_height"],
        ).grid(row=0, column=1, padx=(SP["sm"], 0))
        row += 1

        self._field_label(content, "상호명").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)
        self.entry_shop = self._make_entry(content, textvariable=self.var_shop, placeholder_text="예: 홍길동안마")
        self.entry_shop.grid(row=row, column=1, sticky="ew", pady=field_pady)
        row += 1

        # -- 계정 정보 --
        self._section_divider(content).grid(row=row, column=0, columnspan=2, sticky="ew", pady=SP["md"])
        row += 1
        self._section_title(content, "계정 정보").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, SP["md"]),
        )
        row += 1

        self._field_label(content, "아이디").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)
        self.entry_user = self._make_entry(content, textvariable=self.var_user, placeholder_text="사이트 로그인 아이디")
        self.entry_user.grid(row=row, column=1, sticky="ew", pady=field_pady)
        row += 1

        self._field_label(content, "비밀번호").grid(row=row, column=0, sticky="w", pady=field_pady, padx=label_padx)
        self.entry_pass = self._make_entry(content, textvariable=self.var_pass, placeholder_text="사이트 로그인 비밀번호", show="*")
        self.entry_pass.grid(row=row, column=1, sticky="ew", pady=field_pady)
        row += 1

        # -- 실행 스케줄 --
        self._section_divider(content).grid(row=row, column=0, columnspan=2, sticky="ew", pady=SP["md"])
        row += 1
        self._section_title(content, "실행 스케줄").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, SP["md"]),
        )
        row += 1

        # Time picker card
        sched_card = ctk.CTkFrame(
            content, fg_color=COLORS["input"],
            corner_radius=STYLES["input_radius"],
            border_width=1, border_color=COLORS["border"],
        )
        sched_card.grid(row=row, column=0, columnspan=2, sticky="ew", pady=field_pady)
        row += 1

        picker_row = ctk.CTkFrame(sched_card, fg_color="transparent")
        picker_row.pack(fill="x", padx=SP["lg"], pady=SP["md"])

        # AM/PM toggle buttons
        self.var_ampm = ctk.StringVar(value="오전")
        ampm_frame = ctk.CTkFrame(picker_row, fg_color="transparent")
        ampm_frame.pack(side="left")

        self._btn_am = ctk.CTkButton(
            ampm_frame, text="오전", width=44, height=30,
            fg_color=COLORS["accent_soft"], hover_color=COLORS["accent_muted"],
            corner_radius=6, font=_font(14), text_color=COLORS["accent"],
            command=lambda: self._set_ampm("오전"),
        )
        self._btn_am.pack(side="left", padx=(0, 2))

        self._btn_pm = ctk.CTkButton(
            ampm_frame, text="오후", width=44, height=30,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            corner_radius=6, font=_font(14), text_color=COLORS["text_4"],
            command=lambda: self._set_ampm("오후"),
        )
        self._btn_pm.pack(side="left")

        # Hour : Minute input fields
        time_frame = ctk.CTkFrame(picker_row, fg_color="transparent")
        time_frame.pack(side="left", padx=(SP["lg"], 0))

        self.var_hour = ctk.StringVar(value="09")
        self.entry_hour = ctk.CTkEntry(
            time_frame,
            textvariable=self.var_hour,
            width=56, height=32,
            corner_radius=6,
            fg_color=COLORS["card"],
            border_color=COLORS["border"],
            border_width=1,
            text_color=COLORS["text"],
            font=_font(14, "bold"),
            justify="center",
            placeholder_text="시",
        )
        self.entry_hour.pack(side="left")
        self.entry_hour.bind("<FocusOut>", lambda e: self._clamp_time_input(self.var_hour, 1, 12))
        self.entry_hour.bind("<Return>", lambda e: self._clamp_time_input(self.var_hour, 1, 12))

        ctk.CTkLabel(
            time_frame, text=":",
            font=_font(16, "bold"), text_color=COLORS["text_4"],
        ).pack(side="left", padx=4)

        self.var_minute = ctk.StringVar(value="00")
        self.entry_minute = ctk.CTkEntry(
            time_frame,
            textvariable=self.var_minute,
            width=56, height=32,
            corner_radius=6,
            fg_color=COLORS["card"],
            border_color=COLORS["border"],
            border_width=1,
            text_color=COLORS["text"],
            font=_font(14, "bold"),
            justify="center",
            placeholder_text="분",
        )
        self.entry_minute.pack(side="left")
        self.entry_minute.bind("<FocusOut>", lambda e: self._clamp_time_input(self.var_minute, 0, 59))
        self.entry_minute.bind("<Return>", lambda e: self._clamp_time_input(self.var_minute, 0, 59))

        ctk.CTkButton(
            picker_row, text="+ 추가",
            width=64, height=32,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            corner_radius=6, font=_font(14),
            command=self._add_schedule_token,
        ).pack(side="right")

        # Chips
        self.schedule_chips = ctk.CTkFrame(content, fg_color="transparent")
        self.schedule_chips.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, SP["sm"]))
        self.schedule_chips.grid_columnconfigure(0, weight=1)
        row += 1

        # -- 옵션 --
        self._section_divider(content).grid(row=row, column=0, columnspan=2, sticky="ew", pady=SP["md"])
        row += 1
        self._section_title(content, "옵션").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, SP["md"]),
        )
        row += 1

        flags = ctk.CTkFrame(content, fg_color="transparent")
        flags.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, SP["xl"]))
        row += 1

        ctk.CTkSwitch(
            flags, text="사용 중",
            variable=self.var_enabled,
            fg_color=COLORS["border"], progress_color=COLORS["accent"],
            button_color="#ffffff", button_hover_color="#e4e4e7",
            font=_font(13), text_color=COLORS["text"],
        ).pack(side="left", padx=(0, SP["2xl"]))

        self.switch_use_browser = ctk.CTkSwitch(
            flags, text="브라우저 사용(옵션)",
            variable=self.var_use_browser,
            fg_color=COLORS["border"], progress_color=COLORS["accent"],
            button_color="#ffffff", button_hover_color="#e4e4e7",
            font=_font(13), text_color=COLORS["text"],
        )
        self.switch_use_browser.pack(side="left")

        # 사이트별로 브라우저 필요 여부가 고정이어서, 현재 UI에서는 토글을 자동/비활성 처리한다.
        self._sync_browser_option()
        # 도메인은 플랫폼별로 외부(현재는 JSON)에서 제공되는 값을 사용
        self._sync_domain_from_platform()

        # Actions
        action_row = ctk.CTkFrame(content, fg_color="transparent")
        action_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(SP["sm"], 0))

        ctk.CTkButton(
            action_row, text="저장",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"], font=_font(13),
            command=self._save_workflow, width=100, height=STYLES["button_height"],
        ).pack(side="left", padx=(0, SP["sm"]))

        ctk.CTkButton(
            action_row, text="삭제",
            fg_color=COLORS["input"], hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"], font=_font(13),
            text_color=COLORS["text_2"], command=self._delete_workflow,
            width=100, height=STYLES["button_height"],
        ).pack(side="left")

        ctk.CTkButton(
            action_row, text="지금 실행",
            fg_color=COLORS["success"], hover_color=COLORS["success_hover"],
            corner_radius=STYLES["button_radius"], font=_font(13),
            command=self._run_selected_now, width=120, height=STYLES["button_height"],
        ).pack(side="right")

        self._bind_mousewheel(scroll)

    @staticmethod
    def _bind_mousewheel(scrollable_frame: ctk.CTkScrollableFrame) -> None:
        """자식 위젯에서도 마우스 휠 스크롤이 작동하도록 이벤트를 재귀 바인딩."""
        try:
            canvas = scrollable_frame._parent_canvas
        except AttributeError:
            return

        def _on_wheel(event):
            try:
                delta = getattr(event, "delta", 0)
                if sys.platform == "darwin":
                    canvas.yview_scroll(-1 * delta, "units")
                else:
                    canvas.yview_scroll(-1 * (delta // 120), "units")
            except Exception:
                pass

        def _bind_recursive(widget):
            try:
                widget.bind("<MouseWheel>", _on_wheel, add="+")
                widget.bind("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"), add="+")
                widget.bind("<Button-5>", lambda e: canvas.yview_scroll(3, "units"), add="+")
                for child in widget.winfo_children():
                    _bind_recursive(child)
            except Exception:
                pass

        _bind_recursive(scrollable_frame)

    def _get_synced_site_keys(self) -> list[str]:
        """서버 동기화된 사이트만 반환 (도메인이 있고 활성화된 사이트)."""
        full = load_platform_domains_full()
        synced = [
            k for k, v in full.items()
            if v.get("domain", "").strip() and v.get("enabled", True)
        ]
        return synced if synced else SITE_KEYS[:1]

    def _refresh_site_option_menu(self) -> None:
        """서버 동기화 후 사이트 선택 목록을 갱신한다."""
        try:
            keys = self._get_synced_site_keys()
            self.option_site.configure(values=keys)
            if self.var_site.get() not in keys and keys:
                self.var_site.set(keys[0])
                self._on_site_selected()
        except Exception:
            pass

    def _on_site_selected(self, _value: str | None = None) -> None:
        self._sync_browser_option()
        self._sync_domain_from_platform()

    def _sync_domain_from_platform(self, fallback: str | None = None) -> None:
        """플랫폼 선택값으로 도메인을 자동 설정한다.

        로컬 매핑 파일(`data/platform_domains.json`)을 사용하며,
        파일은 백엔드 동기화 결과로 갱신될 수 있다.
        """
        try:
            site = (self.var_site.get() or "").strip()
        except Exception:
            return

        if fallback is None:
            try:
                fallback = self.var_domain.get()
            except Exception:
                fallback = ""

        resolved = resolve_platform_domain(site, fallback or "")
        try:
            self.var_domain.set(resolved)
        except Exception:
            pass

    def _open_platform_domains(self) -> None:
        """플랫폼 도메인 설정 파일을 연다."""
        try:
            ensure_platform_domains(self.db)
        except Exception:
            pass

        p = platform_domains_path()
        try:
            if sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", str(p)])
                return
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
                return

            import subprocess

            subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:  # noqa: BLE001
            self.toast(f"도메인 설정 파일을 열지 못했습니다: {exc}", "error")

    def _sync_browser_option(self) -> None:
        """브라우저 사용 토글을 사이트 기준으로 자동/비활성화."""
        try:
            site = (self.var_site.get() or "").strip()
        except Exception:
            return

        required = site in BROWSER_REQUIRED_SITES
        try:
            if required:
                self.var_use_browser.set(True)
                self.switch_use_browser.configure(text="브라우저 사용(필수)", state="disabled")
            else:
                # HTTP 기반 사이트는 현재 버전에서 브라우저 자동화를 사용하지 않는다.
                self.var_use_browser.set(False)
                self.switch_use_browser.configure(text="브라우저 사용(미지원)", state="disabled")
        except Exception:
            pass

    # ===== Workflow list / CRUD =====

    def _reload_workflow_list(self) -> None:
        for child in self.list_scroll.winfo_children():
            child.destroy()

        query = self.search_var.get().strip().lower()
        self._workflows_cache = self.db.list_workflows()

        workflows = self._workflows_cache
        if query:
            workflows = [
                w
                for w in workflows
                if (
                    query in w.name.lower()
                    or query in resolve_platform_domain(w.site_key, w.domain).lower()
                    or query in w.site_key.lower()
                )
            ]

        if not workflows:
            empty = ctk.CTkFrame(self.list_scroll, fg_color="transparent")
            empty.grid(sticky="ew", pady=SP["2xl"])
            ctk.CTkLabel(
                empty,
                text="등록된 작업이 없습니다.",
                font=_font(13),
                text_color=COLORS["text_3"],
            ).pack()
            ctk.CTkLabel(
                empty,
                text="'+ 새 작업' 버튼으로 추가하세요.",
                font=_font(13),
                text_color=COLORS["text_4"],
            ).pack(pady=(SP["xs"], 0))
            return

        for wf in workflows:
            is_selected = wf.id == self.selected_workflow_id
            item_bg = COLORS["input"] if is_selected else COLORS["card"]
            item_border = COLORS["accent"] if is_selected else COLORS["border_soft"]

            item = ctk.CTkFrame(
                self.list_scroll,
                fg_color=item_bg,
                corner_radius=STYLES["button_radius"],
                border_width=1,
                border_color=item_border,
                cursor="hand2",
            )
            item.grid(sticky="ew", pady=(0, 4))

            # Bind click to item and all children
            def _bind_click(widget: ctk.CTkBaseClass, workflow: Workflow = wf) -> None:
                widget.bind("<Button-1>", lambda _e, w=workflow: self._select_workflow(w))
                if hasattr(widget, "winfo_children"):
                    for child in widget.winfo_children():
                        _bind_click(child, workflow)

            # Top row: status dot + name + site badge
            top_row = ctk.CTkFrame(item, fg_color="transparent")
            top_row.pack(fill="x", padx=14, pady=(10, 0))

            # 리스트의 점은 '실행 중'이 아니라 '사용 설정' 의미. 초록(성공/실행중)과 혼동 방지.
            dot_color = COLORS["accent"] if wf.enabled else COLORS["text_4"]
            dot = ctk.CTkFrame(top_row, width=8, height=8, corner_radius=4, fg_color=dot_color)
            dot.pack(side="left", padx=(0, SP["sm"]))
            dot.pack_propagate(False)

            ctk.CTkLabel(
                top_row,
                text=wf.name,
                font=_font(13, "bold"),
                text_color=COLORS["text"],
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            # Right actions: site badge + quick run
            right_actions = ctk.CTkFrame(top_row, fg_color="transparent")
            right_actions.pack(side="right")

            ctk.CTkLabel(
                right_actions,
                text=wf.site_key,
                font=_font(10),
                text_color=COLORS["accent"],
                fg_color=COLORS["accent_soft"],
                corner_radius=4,
                padx=6,
                pady=1,
                height=20,
            ).pack(side="right")

            ctk.CTkButton(
                right_actions,
                text="▶ 실행",
                width=64,
                height=22,
                fg_color="transparent",
                hover_color=COLORS["card_hover"],
                border_width=1,
                border_color=COLORS["border"],
                corner_radius=6,
                font=_font(14, "bold"),
                text_color=COLORS["text_2"],
                command=lambda wid=int(wf.id): self._run_workflow_now(wid),
            ).pack(side="right", padx=(SP["sm"], 0))

            # Bottom row: domain + schedule
            bottom_row = ctk.CTkFrame(item, fg_color="transparent")
            bottom_row.pack(fill="x", padx=14, pady=(4, 10))

            ctk.CTkLabel(
                bottom_row,
                text=resolve_platform_domain(wf.site_key, wf.domain) or "도메인 없음",
                font=_font(13),
                text_color=COLORS["text_3"],
                anchor="w",
            ).pack(side="left")

            schedule_summary = ", ".join(wf.schedules[:3])
            if len(wf.schedules) > 3:
                schedule_summary += f" +{len(wf.schedules) - 3}"
            sched_display = schedule_summary if schedule_summary else "스케줄 없음"
            sched_color = COLORS["text_3"] if schedule_summary else COLORS["text_4"]

            ctk.CTkLabel(
                bottom_row,
                text=sched_display,
                font=_mono_font(10),
                text_color=sched_color,
                anchor="e",
            ).pack(side="right")

            # Bind click to entire item tree
            _bind_click(item)

    def _select_workflow(self, wf: Workflow) -> None:
        self.selected_workflow_id = int(wf.id)
        self.var_name.set(wf.name)
        self.var_site.set(wf.site_key)
        self.var_domain.set(wf.domain)
        self._sync_domain_from_platform(fallback=wf.domain)
        self.var_shop.set(wf.shop_name)
        self.var_user.set(wf.username)
        self.var_pass.set(wf.password)
        self.var_enabled.set(bool(wf.enabled))
        self.var_use_browser.set(bool(wf.use_browser))

        self._schedule_tokens = list(wf.schedules)
        self._render_schedule_chips()

        self._sync_browser_option()
        self._reload_workflow_list()

    def _new_workflow(self) -> None:
        self.selected_workflow_id = None
        self.var_name.set("")
        self.var_site.set(SITE_KEYS[0])
        self.var_domain.set("")
        self._sync_domain_from_platform(fallback="")
        self.var_shop.set("")
        self.var_user.set("")
        self.var_pass.set("")
        self.var_enabled.set(True)
        self.var_use_browser.set(True)

        self._schedule_tokens = []
        self._render_schedule_chips()

        self._sync_browser_option()
        self._reload_workflow_list()

    def _clamp_time_input(self, var: ctk.StringVar, lo: int, hi: int) -> None:
        """입력값을 lo~hi 범위로 자동 클램프한다."""
        raw = var.get().strip()
        if not raw:
            return
        try:
            val = int(raw)
        except ValueError:
            var.set(f"{lo:02d}")
            return
        clamped = max(lo, min(hi, val))
        var.set(f"{clamped:02d}")

    def _set_ampm(self, value: str) -> None:
        self.var_ampm.set(value)
        if value == "오전":
            self._btn_am.configure(fg_color=COLORS["accent_soft"], text_color=COLORS["accent"])
            self._btn_pm.configure(fg_color="transparent", text_color=COLORS["text_4"])
        else:
            self._btn_pm.configure(fg_color=COLORS["accent_soft"], text_color=COLORS["accent"])
            self._btn_am.configure(fg_color="transparent", text_color=COLORS["text_4"])

    def _add_schedule_token(self) -> None:
        ampm = self.var_ampm.get()
        raw_hour = self.var_hour.get().strip()
        raw_minute = self.var_minute.get().strip()

        try:
            hour_12 = int(raw_hour)
        except ValueError:
            self.toast("시간을 숫자로 입력해주세요.", "warning")
            return
        try:
            minute = int(raw_minute)
        except ValueError:
            self.toast("분을 숫자로 입력해주세요.", "warning")
            return

        # 범위 초과 시 자동 클램프
        hour_12 = max(1, min(12, hour_12))
        minute = max(0, min(59, minute))
        self.var_hour.set(f"{hour_12:02d}")
        self.var_minute.set(f"{minute:02d}")

        # Convert 12h -> 24h
        if ampm == "오전":
            hour_24 = 0 if hour_12 == 12 else hour_12
        else:
            hour_24 = 12 if hour_12 == 12 else hour_12 + 12

        normalized = f"{hour_24:02d}:{minute:02d}:00"

        if normalized in self._schedule_tokens:
            self.toast("이미 추가된 시간입니다.", "info")
            return

        self._schedule_tokens.append(normalized)
        self._schedule_tokens.sort()
        self._render_schedule_chips()

        # 입력 필드 초기화
        self.var_hour.set(f"{hour:02d}")
        self.var_minute.set("00")

    def _render_schedule_chips(self) -> None:
        for child in self.schedule_chips.winfo_children():
            child.destroy()

        if not self._schedule_tokens:
            ctk.CTkLabel(
                self.schedule_chips,
                text="등록된 스케줄이 없습니다.",
                font=_font(13),
                text_color=COLORS["text_4"],
            ).pack(anchor="w", pady=(SP["xs"], 0))
            return

        wrap_frame = ctk.CTkFrame(self.schedule_chips, fg_color="transparent")
        wrap_frame.pack(fill="x", pady=(SP["xs"], 0))

        flow_children: list[ctk.CTkBaseClass] = []

        for t in self._schedule_tokens:
            try:
                hh, mm = int(t[:2]), int(t[3:5])
                display = f"{hh:02d}:{mm:02d}"
            except Exception:
                display = t

            chip = ctk.CTkFrame(
                wrap_frame, fg_color=COLORS["card"],
                corner_radius=6, border_width=1,
                border_color=COLORS["border"],
            )
            ctk.CTkLabel(
                chip, text=display,
                font=_font(13), text_color=COLORS["text_2"],
                padx=SP["sm"], pady=2,
            ).pack(side="left")
            ctk.CTkButton(
                chip, text="✕", width=20, height=20,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                corner_radius=4, font=_font(10),
                text_color=COLORS["text_4"],
                command=lambda tt=t: self._remove_schedule(tt),
            ).pack(side="left", padx=(0, 4))
            flow_children.append(chip)

        if len(self._schedule_tokens) > 1:
            btn_clear = ctk.CTkButton(
                wrap_frame, text="모두 삭제",
                height=24, width=0,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                corner_radius=4, font=_font(13),
                text_color=COLORS["text_4"],
                command=self._clear_schedules,
            )
            flow_children.append(btn_clear)

        def _reflow(_event=None):
            avail = wrap_frame.winfo_width()
            if avail <= 1:
                return
            x, y, row_h = 0, 0, 0
            gap = SP["sm"]
            for w in flow_children:
                w.update_idletasks()
                ww = w.winfo_reqwidth()
                wh = w.winfo_reqheight()
                if x > 0 and x + ww > avail:
                    y += row_h + gap
                    x, row_h = 0, 0
                w.place(x=x, y=y)
                x += ww + gap
                row_h = max(row_h, wh)
            total_h = y + row_h if flow_children else 0
            wrap_frame.configure(height=max(total_h, 1))

        wrap_frame.bind("<Configure>", _reflow)
        wrap_frame.after(50, _reflow)

    def _remove_schedule(self, token: str) -> None:
        self._schedule_tokens = [t for t in self._schedule_tokens if t != token]
        self._render_schedule_chips()

    def _clear_schedules(self) -> None:
        self._schedule_tokens = []
        self._render_schedule_chips()

    def _save_workflow(self) -> None:
        name = self.var_name.get().strip()
        site_key = self.var_site.get().strip()
        domain = self.var_domain.get().strip()

        if not name:
            self.toast("작업 이름은 필수입니다.", "warning")
            return
        if not site_key:
            self.toast("사이트는 필수입니다.", "warning")
            return
        if not domain:
            self.toast("도메인은 필수입니다.", "warning")
            return

        wf = Workflow(
            id=self.selected_workflow_id,
            name=name,
            site_key=site_key,
            domain=domain,
            shop_name=self.var_shop.get().strip(),
            username=self.var_user.get().strip(),
            password=self.var_pass.get(),
            enabled=bool(self.var_enabled.get()),
            use_browser=bool(self.var_use_browser.get()),
            schedules=list(self._schedule_tokens),
        )

        workflow_id = self.db.save_workflow(wf)
        self.selected_workflow_id = workflow_id
        self.toast("저장되었습니다.", "success")
        self.log_bus.emit(f"작업 저장: {name} (id={workflow_id})", "INFO")
        self._reload_workflow_list()
        self._refresh_stats()

    def _delete_workflow(self) -> None:
        if self.selected_workflow_id is None:
            self.toast("삭제할 작업을 먼저 선택하세요.", "info")
            return

        dialog = ConfirmDialog(self, "삭제", "선택한 작업을 삭제하시겠습니까?\n삭제하면 실행 기록도 함께 삭제됩니다.")
        self.wait_window(dialog)
        if not dialog.result():
            return

        workflow_id = int(self.selected_workflow_id)
        self.db.delete_workflow(workflow_id)
        self.toast("삭제되었습니다.", "success")
        self.log_bus.emit(f"작업 삭제: id={workflow_id}", "WARNING")
        self._new_workflow()
        self._reload_workflow_list()
        self._refresh_stats()
        if self.activity_mode.get() == "실행 기록":
            self._render_history()

    def _run_selected_now(self) -> None:
        if self.selected_workflow_id is None:
            self.toast("실행할 작업을 먼저 선택하세요.", "info")
            return

        self._run_workflow_now(int(self.selected_workflow_id))

    def _run_workflow_now(self, workflow_id: int) -> None:
        """선택/목록 어디에서든 호출 가능한 '지금 실행' 헬퍼.

        일반 사용자 입장에서는 '엔진 시작'을 따로 누르지 않아도 실행되길 기대하므로,
        엔진이 중지 상태면 자동으로 시작한 뒤 대기열에 넣는다.
        """
        started_engine = False
        if not self.engine.is_running:
            self.engine.start()
            started_engine = True
            self._update_engine_buttons("running")

        self.engine.run_now(int(workflow_id))
        if started_engine:
            self.toast("엔진을 시작하고 대기열에 추가했습니다.", "success")
        else:
            self.toast("대기열에 추가했습니다.", "success")

    # ===== Engine / Activity =====

    def _start_engine(self) -> None:
        self.engine.start()
        self.toast("엔진이 시작되었습니다.", "success", title="엔진 시작")
        self._update_engine_buttons("running")

    def _stop_engine(self) -> None:
        self.engine.stop()
        self.toast("엔진이 중지되었습니다.", "warning", title="엔진 중지")
        self._update_engine_buttons("stopped")

    def _restart_engine(self) -> None:
        was_running = self.engine.is_running
        self.engine.stop()
        if was_running:
            self.after(150, self.engine.start)
        else:
            self.engine.start()
        self.toast("엔진이 재시작되었습니다.", "info", title="엔진 재시작")
        self._update_engine_buttons("running")

    def _update_engine_buttons(self, mode: str = "stopped") -> None:
        """엔진 버튼 노출 제어: stopped, running. 상태 변경 시에만 업데이트."""
        if getattr(self, "_engine_btn_mode", None) == mode:
            return
        self._engine_btn_mode = mode

        # Hide all buttons first
        for btn in (self.btn_engine_start, self.btn_engine_stop, self.btn_engine_restart):
            btn.pack_forget()

        if mode == "stopped":
            self.btn_engine_start.pack(side="right", padx=(SP["xs"], 0))
        elif mode == "running":
            self.btn_engine_restart.pack(side="right", padx=(SP["xs"], 0))
            self.btn_engine_stop.pack(side="right", padx=(SP["xs"], 0))

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _open_artifacts_dir(self) -> None:
        p = artifacts_dir()
        try:
            if sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", str(p)])
                return
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
                return

            import subprocess

            subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:  # noqa: BLE001
            self.toast(f"폴더를 열지 못했습니다: {exc}", "error")

    def _switch_activity_tab(self, value: str) -> None:
        """Tab click handler for activity tabs."""
        self.activity_mode.set(value)
        self._update_tab_underline(value)
        self._on_activity_mode_change()

    def _update_tab_underline(self, active: str) -> None:
        """Move underline under the active tab."""
        if active == "실시간 로그":
            active_btn = self._tab_log
            inactive_btn = self._tab_history
        else:
            active_btn = self._tab_history
            inactive_btn = self._tab_log

        active_btn.configure(font=_font(13, "bold"), text_color=COLORS["text"])
        inactive_btn.configure(font=_font(13), text_color=COLORS["text_4"])

        # Position underline
        try:
            active_btn.update_idletasks()
            x = active_btn.winfo_x()
            w = active_btn.winfo_width()
            self._tab_underline.place(x=x, rely=1.0, y=-2, width=w, height=2)
        except Exception:
            pass

    def _on_activity_mode_change(self, *_args: object) -> None:
        mode = self.activity_mode.get()
        if mode == "실행 기록":
            self.activity_action_btn.configure(text="새로고침")
            self._render_history()
        else:
            self.activity_action_btn.configure(text="지우기")
            self._render_logs(full=True)

    def _on_activity_copy_clicked(self) -> None:
        """현재 활동 텍스트박스 내용을 클립보드에 복사."""
        try:
            content = self.activity_text.get("1.0", "end").strip()
            if not content:
                self.toast("복사할 내용이 없습니다.", "warning")
                return
            self.clipboard_clear()
            self.clipboard_append(content)
            self.toast("클립보드에 복사되었습니다.", "success")
        except Exception:
            self.toast("복사에 실패했습니다.", "error")

    def _on_activity_action_clicked(self) -> None:
        mode = self.activity_mode.get()
        if mode == "실행 기록":
            self._render_history()
            return

        self._log_lines.clear()
        self._render_logs(full=True)
        self.toast("로그를 지웠습니다.", "success")

    def _render_history(self) -> None:
        history = self.db.list_history(limit=80)
        self.activity_text.delete("1.0", "end")
        tb = self.activity_text._textbox
        if not history:
            self.activity_text.insert("end", "아직 실행 기록이 없습니다.\n")
            return

        status_view = {
            "success": ("성공", "msg_success"),
            "blocked": ("차단됨", "msg_warn"),
            "unknown": ("확인 불가", "msg_warn"),
            "cooldown": ("대기 룰", "msg_warn"),
            "insufficient": ("횟수/회수 부족", "msg_warn"),
            "login_required": ("로그인 필요", "msg_error"),
            "failed": ("실패", "msg_error"),
        }

        for item in history:
            status_kor, status_tag = status_view.get(item.status, (item.status, "msg_error"))
            trigger_kor = "스케줄" if item.trigger_type == "scheduled" else "수동"

            tb.insert("end", f"[{item.started_at}]", "time")
            tb.insert("end", f" {item.workflow_name} ({trigger_kor}) -> ")
            tb.insert("end", status_kor, status_tag)
            tb.insert("end", f" | {item.message}\n")

    def _insert_colored_log(self, line: str) -> None:
        """Insert a log line with colored tags into activity_text."""
        tb = self.activity_text._textbox

        # Parse: [timestamp] [level] message\n
        if line.startswith("[") and "] [" in line:
            bracket1_end = line.index("]") + 1
            time_part = line[:bracket1_end]
            rest = line[bracket1_end:]

            # Find level bracket
            level_start = rest.find("[")
            level_end = rest.find("]", level_start) + 1 if level_start >= 0 else -1

            if level_start >= 0 and level_end > 0:
                space_before = rest[:level_start]
                level_part = rest[level_start:level_end]
                msg_part = rest[level_end:]

                level_text = level_part.strip("[] ")
                tag_name = {
                    "정보": "tag_info",
                    "경고": "tag_warn",
                    "오류": "tag_error",
                    "디버그": "tag_debug",
                }.get(level_text, "tag_info")

                tb.insert("end", time_part, "time")
                tb.insert("end", space_before)
                tb.insert("end", level_part, tag_name)
                tb.insert("end", msg_part)
                return

        tb.insert("end", line)

    def _render_logs(self, full: bool = False, new_lines: list[str] | None = None) -> None:
        if full:
            self.activity_text.delete("1.0", "end")
            for line in self._log_lines:
                self._insert_colored_log(line)
            self.activity_text.see("end")
            return

        if not new_lines:
            return
        for line in new_lines:
            self._insert_colored_log(line)
        self.activity_text.see("end")

    def _on_activity_reset(self) -> None:
        """현재 활성 탭에 따라 초기화 동작 분기."""
        if self.activity_mode.get() == "실행 기록":
            self._on_stats_reset()
        else:
            self._log_lines.clear()
            self._render_logs(full=True)
            self.toast("실시간 로그가 초기화되었습니다.", "success")

    def _on_stats_reset(self) -> None:
        """실행 기록 초기화 → 요약 카운트 리셋."""
        self.db.clear_history()
        self._refresh_stats()
        if self.activity_mode.get() == "실행 기록":
            self._render_history()
        self.toast("실행 기록이 초기화되었습니다.", "success")

    def _refresh_stats(self) -> None:
        stats = self.db.get_stats()
        self.stat_total.configure(text=str(stats["total"]))
        self.stat_enabled.configure(text=str(stats["enabled"]))
        self.stat_success.configure(text=str(stats["success_24h"]))
        self.stat_blocked.configure(text=str(stats.get("blocked_24h", 0)))
        self.stat_unknown.configure(text=str(stats.get("unknown_24h", 0)))
        self.stat_cooldown.configure(text=str(stats.get("cooldown_24h", 0)))
        self.stat_insufficient.configure(text=str(stats.get("insufficient_24h", 0)))
        self.stat_login_required.configure(text=str(stats.get("login_required_24h", 0)))
        self.stat_fail.configure(text=str(stats["fail_24h"]))

    def _ui_tick(self) -> None:
        self._tick_count += 1

        # Clock
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.ops_pill_time.configure(text=now_text)

        snap = self.engine.snapshot()
        state = snap["state"]
        qsize = snap["queue_size"]
        try:
            qn = int(qsize)
        except Exception:
            qn = 0

        # Engine state: dot + label + pill + buttons
        if state == "실행 중":
            self._engine_dot.configure(fg_color=COLORS["success"])
            self._engine_state_lbl.configure(text="실행 중", text_color=COLORS["success"])
            self._header_engine_dot.configure(fg_color=COLORS["success"])
            self.ops_pill_engine.configure(text="실행 중", text_color=COLORS["success"])
            self.sidebar_state.configure(text="엔진: 실행 중", text_color=COLORS["success"])
            self._update_engine_buttons("running")
        elif state == "대기":
            # 대기는 '정상 상태지만 실행 중은 아님'으로 보여야 해서 초록색(성공) 대신 파란색(준비/대기)을 사용
            waiting_label = "실행 중 (작업 대기중)"
            self._engine_dot.configure(fg_color=COLORS["accent"])
            self._engine_state_lbl.configure(text=waiting_label, text_color=COLORS["accent"])
            self._header_engine_dot.configure(fg_color=COLORS["accent"])
            self.ops_pill_engine.configure(text=waiting_label, text_color=COLORS["accent"])
            self.sidebar_state.configure(text=f"엔진: {waiting_label}", text_color=COLORS["accent"])
            self._update_engine_buttons("running")
        else:
            self._engine_dot.configure(fg_color=COLORS["error"])
            self._engine_state_lbl.configure(text="중지", text_color=COLORS["error"])
            self._header_engine_dot.configure(fg_color=COLORS["error"])
            self.ops_pill_engine.configure(text="중지", text_color=COLORS["text_4"])
            self.sidebar_state.configure(text="엔진: 중지", text_color=COLORS["error"])
            self._update_engine_buttons("stopped")

        current_label = "-"
        if snap["current_name"]:
            current_label = snap["current_name"]
            if snap["current_trigger"]:
                current_label += f" ({snap['current_trigger']})"

        self.sidebar_queue.configure(text=f"대기열: {qsize}")
        self.sidebar_current.configure(text=f"현재: {current_label}")

        self.lbl_worker_current.configure(text=current_label)
        self.lbl_worker_queue.configure(text=str(qsize))

        # 상태 힌트(짧고 명확하게)
        try:
            if state == "실행 중":
                self.lbl_engine_hint.configure(text=f"실행 중: {current_label}")
            elif state == "대기":
                if qn > 0:
                    self.lbl_engine_hint.configure(text=f"작업 대기중: 대기열 {qn}개 (곧 실행)")
                else:
                    self.lbl_engine_hint.configure(text="작업 대기중: 대기열 없음 (스케줄/테스트 실행 대기)")
            else:
                self.lbl_engine_hint.configure(text="중지됨: '시작'을 눌러 엔진을 가동하세요.")
        except Exception:
            pass

        last_txt = "-"
        if snap["last_name"]:
            last_status = snap["last_status"]
            status_kor = {
                "success": "성공",
                "blocked": "차단됨",
                "unknown": "확인 불가",
                "cooldown": "대기 룰",
                "insufficient": "횟수/회수 부족",
                "login_required": "로그인 필요",
                "failed": "실패",
            }.get(last_status, last_status or "-")
            last_txt = f"{snap['last_name']} → {status_kor} ({snap['last_finished_at']})"
        self.lbl_worker_last.configure(text=last_txt)

        # Poll log bus
        events = self.log_bus.poll()
        new_lines: list[str] = []
        for ev in events:
            level = LEVEL_KOR.get(ev.level, ev.level)
            line = f"[{ev.timestamp}] [{level}] {ev.message}\n"
            self._log_lines.append(line)
            new_lines.append(line)

        if self.current_view == "ops" and self.activity_mode.get() == "실시간 로그" and new_lines:
            self._render_logs(full=False, new_lines=new_lines)

        # Stats refresh
        # UI tick은 250ms 기준. 통계는 1초마다 갱신.
        if self._tick_count % 4 == 0:
            self._refresh_stats()

        # History refresh
        # 실행 기록은 2초마다 갱신.
        if self.current_view == "ops" and self.activity_mode.get() == "실행 기록" and self._tick_count % 8 == 0:
            self._render_history()

        self.after(250, self._ui_tick)

    # ===== Toast =====

    def toast(
        self,
        message: str,
        toast_type: str = "info",
        duration_ms: int = 3000,
        title: str = "",
    ) -> None:
        # 기존 토스트: place_forget으로 즉시 숨기고 지연 destroy (깜빡임 방지)
        if self._toast_widget is not None:
            old = self._toast_widget
            self._toast_widget = None
            try:
                old.place_forget()
            except Exception:
                pass
            self.after(50, lambda: _safe_destroy(old))

        _seq = getattr(self, "_toast_seq", 0) + 1
        self._toast_seq = _seq

        def _safe_destroy(w: object) -> None:
            try:
                w.destroy()  # type: ignore[attr-defined]
            except Exception:
                pass

        def _on_close() -> None:
            if self._toast_widget is t:
                self._toast_widget = None
            try:
                t.place_forget()
            except Exception:
                pass
            self.after(50, lambda: _safe_destroy(t))

        t = Toast(self, message, toast_type, title=title, on_close=_on_close)
        self._toast_widget = t

        # Slide-up animation
        end_y = -30
        start_y = 30
        t.place(relx=1.0, rely=1.0, anchor="se", x=-30, y=start_y)

        steps = 6

        def _animate_in(step: int = 0) -> None:
            if step >= steps or self._toast_seq != _seq:
                return
            ease = 1 - (1 - (step + 1) / steps) ** 3
            y = start_y + (end_y - start_y) * ease
            try:
                t.place_configure(y=int(y))
            except Exception:
                return
            self.after(20, lambda: _animate_in(step + 1))

        self.after(10, _animate_in)

        def _slide_out() -> None:
            if self._toast_seq != _seq:
                return

            def _out(step: int = 0) -> None:
                if step >= 5:
                    try:
                        t.place_forget()
                    except Exception:
                        pass
                    self.after(50, lambda: _safe_destroy(t))
                    if self._toast_widget is t:
                        self._toast_widget = None
                    return
                y = int(end_y + (start_y - end_y) * (step + 1) / 5)
                try:
                    t.place_configure(y=y)
                except Exception:
                    return
                self.after(20, lambda: _out(step + 1))

            _out()

        self.after(duration_ms, _slide_out)

    def _on_close(self) -> None:
        try:
            self.engine.stop()
            self.db.close()
        finally:
            self.destroy()


def main() -> None:
    app = WorkerDashboardApp()
    app.mainloop()
