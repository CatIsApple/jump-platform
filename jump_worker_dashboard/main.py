from __future__ import annotations

# Allow running as a script from the repo root without installing the package.
# When running `python3 jump_worker_dashboard/main.py`, sys.path[0] becomes the
# package directory, so the repo root isn't on the import path by default.
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from jump_worker_dashboard.app.gui import main


if __name__ == "__main__":
    main()
