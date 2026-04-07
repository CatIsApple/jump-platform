from __future__ import annotations

# Allow running as a script from the repo root without installing the package.
# When running `python3 jump_worker_dashboard/main.py`, sys.path[0] becomes the
# package directory, so the repo root isn't on the import path by default.
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def _startup_diag() -> None:
    """기동 시 selenium 임포트 진단 — ~/jump_worker_dashboard/data/startup_diag.log 에 기록."""
    import traceback
    log_path = Path.home() / "jump_worker_dashboard" / "data" / "startup_diag.log"
    try:
        lines = [f"sys.path = {sys.path}\n", f"frozen = {getattr(sys, 'frozen', False)}\n"]
        try:
            from selenium import webdriver  # type: ignore
            lines.append(f"selenium import OK: {webdriver.__name__}\n")
        except Exception:
            lines.append(f"selenium import FAILED:\n{traceback.format_exc()}\n")
        try:
            from selenium.webdriver.chrome.options import Options  # type: ignore
            lines.append("Options import OK\n")
        except Exception:
            lines.append(f"Options import FAILED:\n{traceback.format_exc()}\n")
        log_path.write_text("".join(lines), encoding="utf-8")
    except Exception:
        pass


_startup_diag()

# Anti-RE guard (Windows only — debugger/tool detection)
try:
    from jump_worker_dashboard.app._guard import run_guard
    run_guard()
except Exception:
    pass

from jump_worker_dashboard.app.gui import main


if __name__ == "__main__":
    main()
