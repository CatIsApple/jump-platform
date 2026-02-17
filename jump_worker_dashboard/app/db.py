from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .models import HistoryItem, Workflow


def normalize_time_token(value: str) -> str | None:
    token = value.strip()
    if not token:
        return None

    parts = token.split(":")
    if len(parts) not in (2, 3):
        return None

    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None

    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None

    return f"{hh:02d}:{mm:02d}:{ss:02d}"


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        sql = """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            site_key TEXT NOT NULL,
            domain TEXT NOT NULL,
            shop_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            password TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            use_browser INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id INTEGER NOT NULL,
            run_time TEXT NOT NULL,
            UNIQUE(workflow_id, run_time),
            FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id INTEGER NOT NULL,
            workflow_name TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def list_workflows(self) -> list[Workflow]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, name, site_key, domain, shop_name, username, password,
                       enabled, use_browser
                FROM workflows
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()

            workflows: list[Workflow] = []
            for row in rows:
                schedules = self.list_schedules(row["id"])
                workflows.append(
                    Workflow(
                        id=row["id"],
                        name=row["name"],
                        site_key=row["site_key"],
                        domain=row["domain"],
                        shop_name=row["shop_name"],
                        username=row["username"],
                        password=row["password"],
                        enabled=bool(row["enabled"]),
                        use_browser=bool(row["use_browser"]),
                        schedules=schedules,
                    )
                )
            return workflows

    def get_workflow(self, workflow_id: int) -> Workflow | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, name, site_key, domain, shop_name, username, password,
                       enabled, use_browser
                FROM workflows
                WHERE id = ?
                """,
                (workflow_id,),
            ).fetchone()
            if row is None:
                return None

            return Workflow(
                id=row["id"],
                name=row["name"],
                site_key=row["site_key"],
                domain=row["domain"],
                shop_name=row["shop_name"],
                username=row["username"],
                password=row["password"],
                enabled=bool(row["enabled"]),
                use_browser=bool(row["use_browser"]),
                schedules=self.list_schedules(row["id"]),
            )

    def save_workflow(self, workflow: Workflow) -> int:
        now = self._now()
        with self._lock:
            if workflow.id is None:
                cur = self._conn.execute(
                    """
                    INSERT INTO workflows
                        (name, site_key, domain, shop_name, username, password,
                         enabled, use_browser, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow.name.strip(),
                        workflow.site_key.strip(),
                        workflow.domain.strip(),
                        workflow.shop_name.strip(),
                        workflow.username.strip(),
                        workflow.password,
                        int(workflow.enabled),
                        int(workflow.use_browser),
                        now,
                        now,
                    ),
                )
                workflow_id = int(cur.lastrowid)
            else:
                self._conn.execute(
                    """
                    UPDATE workflows
                    SET name = ?,
                        site_key = ?,
                        domain = ?,
                        shop_name = ?,
                        username = ?,
                        password = ?,
                        enabled = ?,
                        use_browser = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        workflow.name.strip(),
                        workflow.site_key.strip(),
                        workflow.domain.strip(),
                        workflow.shop_name.strip(),
                        workflow.username.strip(),
                        workflow.password,
                        int(workflow.enabled),
                        int(workflow.use_browser),
                        now,
                        workflow.id,
                    ),
                )
                workflow_id = workflow.id

            self.replace_schedules(workflow_id, workflow.schedules)
            self._conn.commit()
            return workflow_id

    def delete_workflow(self, workflow_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            self._conn.commit()

    def list_schedules(self, workflow_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_time FROM workflow_schedules WHERE workflow_id = ? ORDER BY run_time",
                (workflow_id,),
            ).fetchall()
            return [row["run_time"] for row in rows]

    def replace_schedules(self, workflow_id: int, schedules: list[str]) -> None:
        normalized: list[str] = []
        for item in schedules:
            t = normalize_time_token(item)
            if t and t not in normalized:
                normalized.append(t)

        with self._lock:
            self._conn.execute("DELETE FROM workflow_schedules WHERE workflow_id = ?", (workflow_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO workflow_schedules(workflow_id, run_time) VALUES (?, ?)",
                [(workflow_id, run_time) for run_time in normalized],
            )

    def list_due_workflows(self, now_hms: str) -> list[Workflow]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT w.id
                FROM workflows w
                JOIN workflow_schedules s ON s.workflow_id = w.id
                WHERE w.enabled = 1
                  AND s.run_time = ?
                ORDER BY w.id
                """,
                (now_hms,),
            ).fetchall()

        results: list[Workflow] = []
        for row in rows:
            wf = self.get_workflow(int(row["id"]))
            if wf:
                results.append(wf)
        return results

    def add_history(
        self,
        workflow_id: int,
        workflow_name: str,
        trigger_type: str,
        scheduled_for: str,
        started_at: str,
        finished_at: str,
        status: str,
        message: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO run_history(
                    workflow_id, workflow_name, trigger_type, scheduled_for,
                    started_at, finished_at, status, message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    workflow_name,
                    trigger_type,
                    scheduled_for,
                    started_at,
                    finished_at,
                    status,
                    message,
                ),
            )
            self._conn.commit()

    def list_history(self, limit: int = 200) -> list[HistoryItem]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, workflow_id, workflow_name, trigger_type, scheduled_for,
                       started_at, finished_at, status, message
                FROM run_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                HistoryItem(
                    id=row["id"],
                    workflow_id=row["workflow_id"],
                    workflow_name=row["workflow_name"],
                    trigger_type=row["trigger_type"],
                    scheduled_for=row["scheduled_for"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    status=row["status"],
                    message=row["message"],
                )
                for row in rows
            ]

    def clear_history(self) -> None:
        """실행 기록 전체 삭제."""
        with self._lock:
            self._conn.execute("DELETE FROM run_history")
            self._conn.commit()

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            total = int(self._conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0])
            enabled = int(
                self._conn.execute("SELECT COUNT(*) FROM workflows WHERE enabled = 1").fetchone()[0]
            )
            success_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'success'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            blocked_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'blocked'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            unknown_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'unknown'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            cooldown_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'cooldown'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            insufficient_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'insufficient'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            login_required_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'login_required'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )
            fail_24h = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_history
                    WHERE status = 'failed'
                      AND started_at >= datetime('now', '-1 day', 'localtime')
                    """
                ).fetchone()[0]
            )

        return {
            "total": total,
            "enabled": enabled,
            "success_24h": success_24h,
            "blocked_24h": blocked_24h,
            "unknown_24h": unknown_24h,
            "cooldown_24h": cooldown_24h,
            "insufficient_24h": insufficient_24h,
            "login_required_24h": login_required_24h,
            "fail_24h": fail_24h,
        }

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            return row["value"]

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO app_settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()
