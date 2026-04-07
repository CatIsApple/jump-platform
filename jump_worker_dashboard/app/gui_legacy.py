from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from .db import Database, normalize_time_token
from .engine import WorkerEngine
from .sites import SITE_KEYS
from .log_bus import LogBus
from .models import Workflow


# ---------------------------
# Theme / Fonts
# ---------------------------

COLORS = {
    "bg_dark": "#0a0a0a",
    "bg_sidebar": "#0f0f0f",
    "bg_card": "#151515",
    "bg_card_hover": "#1a1a1a",
    "bg_input": "#1f1f1f",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "accent_subtle": "#1e3a5f",
    "success": "#22c55e",
    "success_hover": "#16a34a",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "text": "#ffffff",
    "text_secondary": "#a1a1aa",
    "text_muted": "#6b7280",
    "border": "#262626",
    "border_subtle": "#1f1f1f",
    "code_bg": "#0d0d0d",
}

STYLES = {
    "card_radius": 16,
    "input_radius": 10,
    "button_radius": 10,
    "entry_height": 40,
    "gap_sm": 8,
    "gap_md": 14,
    "gap_lg": 22,
}

LEVEL_KOR = {
    "DEBUG": "디버그",
    "INFO": "정보",
    "WARNING": "경고",
    "ERROR": "오류",
}


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

    # macOS: attempt in-process font registration via CoreText (optional)
    if sys.platform == "darwin":
        try:
            from Foundation import NSURL  # type: ignore
            from CoreText import CTFontManagerRegisterFontsForURL, kCTFontManagerScopeProcess  # type: ignore

            for p in (regular_path, bold_path):
                if p.exists():
                    url = NSURL.fileURLWithPath_(str(p))
                    CTFontManagerRegisterFontsForURL(url, kCTFontManagerScopeProcess, None)
        except Exception:
            # If PyObjC isn't available, system will fall back.
            pass

    # Windows: AddFontResourceEx (optional)
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


FONT_FAMILY = load_pretendard_font() or (
    "SF Pro Display" if sys.platform == "darwin" else "Segoe UI"
)


# ---------------------------
# UI Helpers
# ---------------------------


@dataclass
class ToastSpec:
    fg: str
    border: str
    icon: str


_TOASTS: dict[str, ToastSpec] = {
    "success": ToastSpec(fg="#166534", border=COLORS["success"], icon="✓"),
    "error": ToastSpec(fg="#991b1b", border=COLORS["error"], icon="✕"),
    "warning": ToastSpec(fg="#92400e", border=COLORS["warning"], icon="!"),
    "info": ToastSpec(fg="#1e40af", border=COLORS["accent"], icon="i"),
}


class Toast(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkBaseClass, message: str, toast_type: str) -> None:
        spec = _TOASTS.get(toast_type, _TOASTS["info"])
        super().__init__(parent, corner_radius=10)
        self.configure(fg_color=spec.fg, border_color=spec.border, border_width=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(padx=16, pady=12)

        ctk.CTkLabel(
            content,
            text=spec.icon,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color="#ffffff",
            width=20,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            content,
            text=message,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color="#ffffff",
        ).pack(side="left")


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent: ctk.CTk, title: str, message: str) -> None:
        super().__init__(parent)
        self._result: Optional[bool] = None
        self.title(title)
        self.geometry("420x190")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        container = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            corner_radius=STYLES["card_radius"],
        )
        container.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            container,
            text=message,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text"],
            justify="left",
        ).pack(anchor="w", padx=16, pady=(18, 10))

        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=16, pady=16)

        ctk.CTkButton(
            btn_row,
            text="취소",
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            command=self._cancel,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="확인",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._confirm,
        ).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _confirm(self) -> None:
        self._result = True
        self.destroy()

    def _cancel(self) -> None:
        self._result = False
        self.destroy()

    def result(self) -> bool:
        return bool(self._result)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent: "WorkerDashboardApp") -> None:
        super().__init__(parent)
        self.app = parent
        self.title("설정")
        self.geometry("520x320")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])

        self.transient(parent)
        self.grab_set()

        card = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            corner_radius=STYLES["card_radius"],
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)
        card.grid_columnconfigure(1, weight=1)

        label_font = ctk.CTkFont(family=FONT_FAMILY, size=12)

        ctk.CTkLabel(
            card,
            text="워커 개수",
            font=label_font,
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 10))
        self.entry_worker_count = self.app._make_entry(card)
        self.entry_worker_count.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(16, 10))
        self.entry_worker_count.insert(0, self.app.db.get_setting("worker_count", "2"))

        ctk.CTkLabel(
            card,
            text="스케줄 폴링 간격(초)",
            font=label_font,
            text_color=COLORS["text_secondary"],
        ).grid(row=1, column=0, sticky="w", padx=16, pady=10)
        self.entry_poll_interval = self.app._make_entry(card)
        self.entry_poll_interval.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=10)
        self.entry_poll_interval.insert(0, self.app.db.get_setting("poll_interval", "1.0"))

        self.var_auto_start = ctk.BooleanVar(value=self.app.db.get_setting("auto_start", "1") == "1")
        ctk.CTkCheckBox(
            card,
            text="앱 실행 시 엔진 자동 시작",
            variable=self.var_auto_start,
            fg_color=COLORS["accent"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text"],
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(6, 10))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 16))

        ctk.CTkButton(
            btn_row,
            text="저장 및 적용",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._apply,
        ).pack(side="right")

        ctk.CTkButton(
            btn_row,
            text="닫기",
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _apply(self) -> None:
        try:
            worker_count = int(self.entry_worker_count.get().strip())
            poll_interval = float(self.entry_poll_interval.get().strip())
        except ValueError:
            self.app.toast("설정 값이 숫자 형식이 아닙니다.", "warning")
            return

        if worker_count < 1:
            self.app.toast("워커 개수는 1 이상이어야 합니다.", "warning")
            return
        if poll_interval < 0.2:
            self.app.toast("폴링 간격은 0.2초 이상이어야 합니다.", "warning")
            return

        self.app.db.set_setting("worker_count", str(worker_count))
        self.app.db.set_setting("poll_interval", str(poll_interval))
        self.app.db.set_setting("auto_start", "1" if self.var_auto_start.get() else "0")

        was_running = self.app.engine.is_running
        self.app.engine.stop()
        self.app.engine = WorkerEngine(
            db=self.app.db,
            log_bus=self.app.log_bus,
            worker_count=worker_count,
            poll_interval=poll_interval,
        )
        if was_running:
            self.app.engine.start()

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
        self.configure(fg_color=COLORS["bg_dark"])

        self.base_dir = Path(__file__).resolve().parent.parent
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            self.data_dir = Path.home() / "jump_worker_dashboard" / "data"
        else:
            self.data_dir = self.base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db = Database(self.data_dir / "worker_dashboard.db")
        self.log_bus = LogBus()

        worker_count = int(self.db.get_setting("worker_count", "2"))
        poll_interval = float(self.db.get_setting("poll_interval", "1.0"))
        self.engine = WorkerEngine(
            db=self.db,
            log_bus=self.log_bus,
            worker_count=worker_count,
            poll_interval=poll_interval,
        )

        self.selected_workflow_id: Optional[int] = None
        self._workflows_cache: list[Workflow] = []
        self._schedule_tokens: list[str] = []
        self._log_lines = deque(maxlen=2500)
        self._tick_count = 0

        self._toast_widget: Optional[Toast] = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_dashboard()

        self._reload_workflow_list()
        self._refresh_stats()
        self._on_activity_mode_change()

        if self.db.get_setting("auto_start", "1") == "1":
            self.engine.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._ui_tick)

    # ----- Sidebar -----
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=280,
            fg_color=COLORS["bg_sidebar"],
            corner_radius=0,
            border_width=1,
            border_color=COLORS["border_subtle"],
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        title = ctk.CTkFrame(sidebar, fg_color="transparent")
        title.pack(fill="x", padx=24, pady=(28, 16))

        ctk.CTkLabel(
            title,
            text="Guardian JUMP",
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            title,
            text="대시보드",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkFrame(sidebar, height=1, fg_color=COLORS["border"]).pack(
            fill="x", padx=24, pady=(0, 14)
        )

        # Quick info
        info = ctk.CTkFrame(sidebar, fg_color="transparent")
        info.pack(fill="x", padx=24, pady=(0, 14))

        self.sidebar_engine = ctk.CTkLabel(
            info,
            text="엔진: 중지",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["error"],
        )
        self.sidebar_engine.pack(anchor="w")

        self.sidebar_queue = ctk.CTkLabel(
            info,
            text="대기열: 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_muted"],
        )
        self.sidebar_queue.pack(anchor="w", pady=(4, 0))

        # Buttons
        btns = ctk.CTkFrame(sidebar, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(0, 10))

        ctk.CTkButton(
            btns,
            text="엔진 시작",
            height=40,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            command=self._start_engine,
        ).pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            btns,
            text="엔진 중지",
            height=40,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            command=self._stop_engine,
        ).pack(fill="x")

        bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=24, pady=24)

        ctk.CTkFrame(bottom, height=1, fg_color=COLORS["border"]).pack(fill="x", pady=(0, 14))

        ctk.CTkButton(
            bottom,
            text="설정",
            height=38,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            command=self._open_settings,
        ).pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            bottom,
            text="v0.1.0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w")

    # ----- Dashboard Layout -----
    def _build_dashboard(self) -> None:
        main = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # Header
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(24, 10))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left")

        ctk.CTkLabel(
            left,
            text="작업 대시보드",
            font=ctk.CTkFont(family=FONT_FAMILY, size=28, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            left,
            text="워크플로우를 등록하고 스케줄 또는 즉시 실행으로 자동화를 운영합니다.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_muted"],
        ).pack(anchor="w", pady=(4, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")

        self.pill_time = ctk.CTkLabel(
            right,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["accent"],
            fg_color=COLORS["accent_subtle"],
            corner_radius=8,
            padx=10,
            pady=6,
        )
        self.pill_time.pack(side="left", padx=(0, 10))

        self.pill_selected = ctk.CTkLabel(
            right,
            text="선택: 없음",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["bg_card"],
            corner_radius=8,
            padx=10,
            pady=6,
        )
        self.pill_selected.pack(side="left")

        # Stats row
        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 12))
        stats.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.stat_total = self._stat_card(stats, 0, "전체 작업", "0")
        self.stat_enabled = self._stat_card(stats, 1, "사용 중", "0")
        self.stat_queue = self._stat_card(stats, 2, "대기열", "0")
        self.stat_success = self._stat_card(stats, 3, "성공(24시간)", "0")
        self.stat_fail = self._stat_card(stats, 4, "실패(24시간)", "0")

        # Body row
        body = ctk.CTkFrame(main, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=26, pady=(0, 24))
        body.grid_columnconfigure((0, 1, 2), weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left: workflow list
        self.card_list = self._card(body, "작업 목록")
        self.card_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.card_list.grid_rowconfigure(2, weight=1)
        self.card_list.grid_columnconfigure(0, weight=1)

        list_top = ctk.CTkFrame(self.card_list, fg_color="transparent")
        list_top.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        list_top.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar(value="")
        self.entry_search = self._make_entry(
            list_top,
            textvariable=self.search_var,
            placeholder_text="이름/도메인 검색",
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
            command=self._new_workflow,
        ).grid(row=0, column=1, padx=(10, 0))

        self.list_scroll = ctk.CTkScrollableFrame(
            self.card_list,
            fg_color=COLORS["bg_input"],
            corner_radius=STYLES["input_radius"],
            border_width=1,
            border_color=COLORS["border"],
        )
        self.list_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.list_scroll.grid_columnconfigure(0, weight=1)

        # Middle: editor
        self.card_edit = self._card(body, "작업 편집")
        self.card_edit.grid(row=0, column=1, sticky="nsew", padx=8)
        self.card_edit.grid_columnconfigure(0, weight=1)
        self.card_edit.grid_rowconfigure(1, weight=1)

        self._build_editor(self.card_edit)

        # Right: activity
        self.card_activity = self._card(body, "활동")
        self.card_activity.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        self.card_activity.grid_columnconfigure(0, weight=1)
        self.card_activity.grid_rowconfigure(2, weight=1)

        seg_row = ctk.CTkFrame(self.card_activity, fg_color="transparent")
        seg_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        seg_row.grid_columnconfigure(0, weight=1)

        self.activity_mode = ctk.StringVar(value="실행 기록")
        self.segment = ctk.CTkSegmentedButton(
            seg_row,
            values=["실행 기록", "실시간 로그"],
            variable=self.activity_mode,
            command=self._on_activity_mode_change,
            fg_color=COLORS["bg_input"],
            selected_color=COLORS["accent_subtle"],
            selected_hover_color=COLORS["accent_subtle"],
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            text_color_disabled=COLORS["text_muted"],
        )
        self.segment.grid(row=0, column=0, sticky="ew")

        self.activity_action_btn = ctk.CTkButton(
            seg_row,
            text="새로고침",
            width=70,
            height=32,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
            command=self._on_activity_action_clicked,
        )
        self.activity_action_btn.grid(row=0, column=1, padx=(10, 0))

        self.activity_text = ctk.CTkTextbox(
            self.card_activity,
            fg_color=COLORS["code_bg"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["input_radius"],
            font=ctk.CTkFont(family="SF Mono" if sys.platform == "darwin" else "Consolas", size=11),
        )
        self.activity_text.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))

    def _stat_card(self, parent: ctk.CTkFrame, col: int, title: str, value: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_card"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            corner_radius=STYLES["card_radius"],
        )
        card.grid(row=0, column=col, sticky="ew", padx=6)

        ctk.CTkLabel(
            card,
            text=title,
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(anchor="w", padx=14, pady=(12, 2))

        lbl = ctk.CTkLabel(
            card,
            text=value,
            text_color=COLORS["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=26, weight="bold"),
        )
        lbl.pack(anchor="w", padx=14, pady=(0, 12))
        return lbl

    def _card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["bg_card"],
            border_width=1,
            border_color=COLORS["border_subtle"],
            corner_radius=STYLES["card_radius"],
        )
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 10))
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
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            border_width=1,
            corner_radius=STYLES["input_radius"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text"],
        )
        if show is not None:
            e.configure(show=show)
        return e

    def _field_label(self, parent: ctk.CTkFrame, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )

    def _build_editor(self, card: ctk.CTkFrame) -> None:
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
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

        self._field_label(content, "작업 이름").grid(row=row, column=0, sticky="w", pady=(0, 6))
        self.entry_name = self._make_entry(content, textvariable=self.var_name, placeholder_text="예: 오피가이드 점프")
        self.entry_name.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        row += 1

        self._field_label(content, "사이트").grid(row=row, column=0, sticky="w", pady=6)
        self.combo_site = ctk.CTkComboBox(
            content,
            values=SITE_KEYS,
            variable=self.var_site,
            state="readonly",
            height=STYLES["entry_height"],
            corner_radius=STYLES["input_radius"],
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            button_color=COLORS["bg_input"],
            button_hover_color=COLORS["bg_card_hover"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.combo_site.grid(row=row, column=1, sticky="ew", pady=6)
        row += 1

        self._field_label(content, "도메인").grid(row=row, column=0, sticky="w", pady=6)
        self.entry_domain = self._make_entry(content, textvariable=self.var_domain, placeholder_text="예: https://example.com")
        self.entry_domain.grid(row=row, column=1, sticky="ew", pady=6)
        row += 1

        self._field_label(content, "상호명(옵션)").grid(row=row, column=0, sticky="w", pady=6)
        self.entry_shop = self._make_entry(content, textvariable=self.var_shop, placeholder_text="예: 홍길동안마")
        self.entry_shop.grid(row=row, column=1, sticky="ew", pady=6)
        row += 1

        self._field_label(content, "아이디(옵션)").grid(row=row, column=0, sticky="w", pady=6)
        self.entry_user = self._make_entry(content, textvariable=self.var_user, placeholder_text="사이트 로그인 아이디")
        self.entry_user.grid(row=row, column=1, sticky="ew", pady=6)
        row += 1

        self._field_label(content, "비밀번호(옵션)").grid(row=row, column=0, sticky="w", pady=6)
        self.entry_pass = self._make_entry(content, textvariable=self.var_pass, placeholder_text="사이트 로그인 비밀번호", show="*")
        self.entry_pass.grid(row=row, column=1, sticky="ew", pady=6)
        row += 1

        # Schedules
        self._field_label(content, "실행 시간(스케줄)").grid(row=row, column=0, sticky="w", pady=(10, 6))
        sched_row = ctk.CTkFrame(content, fg_color="transparent")
        sched_row.grid(row=row, column=1, sticky="ew", pady=(10, 6))
        sched_row.grid_columnconfigure(0, weight=1)

        self.entry_sched = self._make_entry(sched_row, placeholder_text="HH:MM 또는 HH:MM:SS")
        self.entry_sched.grid(row=0, column=0, sticky="ew")
        self.entry_sched.bind("<Return>", lambda _e: self._add_schedule_token())

        ctk.CTkButton(
            sched_row,
            text="추가",
            width=70,
            height=STYLES["entry_height"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            command=self._add_schedule_token,
        ).grid(row=0, column=1, padx=(10, 0))

        row += 1

        self.schedule_chips = ctk.CTkFrame(content, fg_color="transparent")
        self.schedule_chips.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.schedule_chips.grid_columnconfigure(0, weight=1)
        row += 1

        flags = ctk.CTkFrame(content, fg_color="transparent")
        flags.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 14))

        ctk.CTkSwitch(
            flags,
            text="사용 중",
            variable=self.var_enabled,
            fg_color=COLORS["accent"],
            progress_color=COLORS["accent"],
            button_color=COLORS["bg_input"],
            button_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text"],
        ).pack(side="left", padx=(0, 14))

        ctk.CTkSwitch(
            flags,
            text="브라우저 사용",
            variable=self.var_use_browser,
            fg_color=COLORS["accent"],
            progress_color=COLORS["accent"],
            button_color=COLORS["bg_input"],
            button_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text"],
        ).pack(side="left")

        # Actions
        action_row = ctk.CTkFrame(content, fg_color="transparent")
        action_row.grid(row=row, column=0, columnspan=2, sticky="ew")

        ctk.CTkButton(
            action_row,
            text="저장",
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            corner_radius=STYLES["button_radius"],
            command=self._save_workflow,
        ).pack(side="left")

        ctk.CTkButton(
            action_row,
            text="삭제",
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=STYLES["button_radius"],
            command=self._delete_workflow,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            action_row,
            text="즉시 실행",
            fg_color=COLORS["success"],
            hover_color=COLORS["success_hover"],
            corner_radius=STYLES["button_radius"],
            command=self._run_selected_now,
        ).pack(side="right")

    # ----- Workflow list / CRUD -----
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
                if query in w.name.lower() or query in w.domain.lower() or query in w.site_key.lower()
            ]

        if not workflows:
            ctk.CTkLabel(
                self.list_scroll,
                text="등록된 작업이 없습니다.",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_muted"],
            ).grid(sticky="ew", pady=12)
            return

        for wf in workflows:
            is_selected = wf.id == self.selected_workflow_id
            fg = COLORS["accent_subtle"] if is_selected else COLORS["bg_card"]

            status = "사용" if wf.enabled else "중지"
            schedule_summary = ", ".join(wf.schedules[:3])
            if len(wf.schedules) > 3:
                schedule_summary += f" 외 {len(wf.schedules) - 3}개"

            text = (
                f"[{status}] {wf.name}\n"
                f"{wf.site_key} | {wf.domain}\n"
                f"스케줄: {schedule_summary if schedule_summary else '없음'}"
            )

            row = ctk.CTkFrame(
                self.list_scroll,
                fg_color=fg,
                corner_radius=12,
                border_width=1,
                border_color=COLORS["border_subtle"],
            )
            row.grid(sticky="ew", pady=6)
            row.grid_columnconfigure(0, weight=1)

            btn_select = ctk.CTkButton(
                row,
                text=text,
                anchor="w",
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                height=74,
                command=lambda w=wf: self._select_workflow(w),
            )
            btn_select.grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=8)

            ctk.CTkButton(
                row,
                text="실행",
                width=64,
                height=32,
                fg_color=COLORS["success"],
                hover_color=COLORS["success_hover"],
                corner_radius=10,
                command=lambda wid=int(wf.id): self.engine.run_now(wid),
            ).grid(row=0, column=1, sticky="e", padx=10, pady=10)

    def _select_workflow(self, wf: Workflow) -> None:
        self.selected_workflow_id = int(wf.id)
        self.var_name.set(wf.name)
        self.var_site.set(wf.site_key)
        self.var_domain.set(wf.domain)
        self.var_shop.set(wf.shop_name)
        self.var_user.set(wf.username)
        self.var_pass.set(wf.password)
        self.var_enabled.set(bool(wf.enabled))
        self.var_use_browser.set(bool(wf.use_browser))

        self._schedule_tokens = list(wf.schedules)
        self._render_schedule_chips()

        self.pill_selected.configure(text=f"선택: {wf.name}")
        self._reload_workflow_list()

    def _new_workflow(self) -> None:
        self.selected_workflow_id = None
        self.var_name.set("")
        self.var_site.set(SITE_KEYS[0])
        self.var_domain.set("")
        self.var_shop.set("")
        self.var_user.set("")
        self.var_pass.set("")
        self.var_enabled.set(True)
        self.var_use_browser.set(True)

        self._schedule_tokens = []
        self._render_schedule_chips()

        self.pill_selected.configure(text="선택: 없음")
        self._reload_workflow_list()

    def _add_schedule_token(self) -> None:
        token = self.entry_sched.get().strip()
        if not token:
            return

        normalized = normalize_time_token(token)
        if normalized is None:
            self.toast("시간 형식이 올바르지 않습니다. (HH:MM 또는 HH:MM:SS)", "warning")
            return

        if normalized in self._schedule_tokens:
            self.toast("이미 추가된 시간입니다.", "info")
            self.entry_sched.delete(0, "end")
            return

        self._schedule_tokens.append(normalized)
        self._schedule_tokens.sort()
        self.entry_sched.delete(0, "end")
        self._render_schedule_chips()

    def _render_schedule_chips(self) -> None:
        for child in self.schedule_chips.winfo_children():
            child.destroy()

        if not self._schedule_tokens:
            ctk.CTkLabel(
                self.schedule_chips,
                text="스케줄이 없습니다. 저장 후 '즉시 실행'으로도 실행할 수 있습니다.",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_muted"],
            ).pack(anchor="w")
            return

        chip_row = ctk.CTkFrame(self.schedule_chips, fg_color="transparent")
        chip_row.pack(fill="x")

        for t in self._schedule_tokens:
            ctk.CTkButton(
                chip_row,
                text=f"{t}  x",
                height=28,
                fg_color=COLORS["bg_input"],
                hover_color=COLORS["bg_card_hover"],
                border_width=1,
                border_color=COLORS["border"],
                corner_radius=14,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                command=lambda tt=t: self._remove_schedule(tt),
            ).pack(side="left", padx=(0, 8), pady=(0, 6))

        ctk.CTkButton(
            chip_row,
            text="모두 지우기",
            height=28,
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=14,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            command=self._clear_schedules,
        ).pack(side="left", pady=(0, 6))

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

        self.engine.run_now(int(self.selected_workflow_id))
        self.toast("대기열에 추가했습니다.", "success")

    # ----- Engine / Activity -----
    def _start_engine(self) -> None:
        self.engine.start()

    def _stop_engine(self) -> None:
        self.engine.stop()

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _on_activity_mode_change(self, *_args: object) -> None:
        mode = self.activity_mode.get()
        if mode == "실행 기록":
            self.activity_action_btn.configure(text="새로고침")
            self._render_history()
        else:
            self.activity_action_btn.configure(text="로그 지우기")
            self._render_logs(full=True)

    def _on_activity_action_clicked(self) -> None:
        mode = self.activity_mode.get()
        if mode == "실행 기록":
            self._render_history()
            return

        self._log_lines.clear()
        self._render_logs(full=True)
        self.toast("로그를 지웠습니다.", "success")

    def _render_history(self) -> None:
        history = self.db.list_history(limit=60)
        self.activity_text.delete("1.0", "end")
        if not history:
            self.activity_text.insert("end", "아직 실행 기록이 없습니다.\n")
            return

        for item in history:
            status_kor = "성공" if item.status == "success" else "실패"
            trigger_kor = "스케줄" if item.trigger_type == "scheduled" else "수동"
            line = f"[{item.started_at}] {item.workflow_name} ({trigger_kor}) -> {status_kor} | {item.message}\n"
            self.activity_text.insert("end", line)

    def _render_logs(self, full: bool = False, new_lines: list[str] | None = None) -> None:
        if full:
            self.activity_text.delete("1.0", "end")
            for line in self._log_lines:
                self.activity_text.insert("end", line)
            self.activity_text.see("end")
            return

        if not new_lines:
            return
        for line in new_lines:
            self.activity_text.insert("end", line)
        self.activity_text.see("end")

    def _refresh_stats(self) -> None:
        stats = self.db.get_stats()
        self.stat_total.configure(text=str(stats["total"]))
        self.stat_enabled.configure(text=str(stats["enabled"]))
        self.stat_queue.configure(text=str(self.engine.queue_size))
        self.stat_success.configure(text=str(stats["success_24h"]))
        self.stat_fail.configure(text=str(stats["fail_24h"]))

        # Sidebar indicators
        self.sidebar_queue.configure(text=f"대기열: {self.engine.queue_size}")

    def _ui_tick(self) -> None:
        self._tick_count += 1

        # Header clock
        self.pill_time.configure(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Engine status labels
        if self.engine.is_running:
            self.sidebar_engine.configure(text="엔진: 실행 중", text_color=COLORS["success"])
        else:
            self.sidebar_engine.configure(text="엔진: 중지", text_color=COLORS["error"])

        # Poll log bus (항상 수집해서 버퍼에 저장)
        events = self.log_bus.poll()
        new_lines: list[str] = []
        for ev in events:
            level = LEVEL_KOR.get(ev.level, ev.level)
            line = f"[{ev.timestamp}] [{level}] {ev.message}\n"
            self._log_lines.append(line)
            new_lines.append(line)

        if self.activity_mode.get() == "실시간 로그" and new_lines:
            self._render_logs(full=False, new_lines=new_lines)

        # Stats refresh (1초마다)
        if self._tick_count % 2 == 0:
            self._refresh_stats()

        # History refresh (2초마다, 실행 기록 모드일 때만)
        if self.activity_mode.get() == "실행 기록" and self._tick_count % 4 == 0:
            self._render_history()

        self.after(500, self._ui_tick)

    # ----- Toast -----
    def toast(self, message: str, toast_type: str = "info", duration_ms: int = 2400) -> None:
        if self._toast_widget is not None:
            try:
                self._toast_widget.destroy()
            except Exception:
                pass
            self._toast_widget = None

        toast = Toast(self, message, toast_type)
        toast.place(relx=0.5, rely=0.965, anchor="s")
        self._toast_widget = toast

        def _remove() -> None:
            if self._toast_widget is toast:
                try:
                    toast.destroy()
                except Exception:
                    pass
                self._toast_widget = None

        self.after(duration_ms, _remove)

    def _on_close(self) -> None:
        try:
            self.engine.stop()
            self.db.close()
        finally:
            self.destroy()


def main() -> None:
    app = WorkerDashboardApp()
    app.mainloop()
