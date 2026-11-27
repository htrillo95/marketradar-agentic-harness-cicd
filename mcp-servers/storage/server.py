#!/usr/bin/env python3
"""Storage MCP server with role-based allow-list enforcement.

Authorization and auditing are Module 4 (AGENT_ROLE + allow-list.json).
Persistence is the Module 3 SQLite store (mcp/storage/server.py `get_db`):
WAL mode, schema created on open, soft delete, last_updated timestamps.
"""

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
except Exception:  # pragma: no cover - lets tests run without FastMCP installed
    class ToolError(Exception):
        pass
    class FastMCP:
        def __init__(self, name: str):
            self.name = name
        def tool(self, func=None):
            def decorate(fn):
                return fn
            return decorate(func) if func else decorate
        def run(self):
            print(f"FastMCP not installed; {self.name} functions are importable only.")

mcp = FastMCP("storage")
CALLING_ROLE = os.environ.get("AGENT_ROLE", "")
ALLOW_LIST_PATH = Path(__file__).parent / "allow-list.json"
ALLOW_LIST = json.loads(ALLOW_LIST_PATH.read_text(encoding="utf-8"))
AUDIT_LOG_PATH = Path(os.environ.get("STORAGE_AUDIT_LOG", "logs/storage-audit-log.jsonl"))
AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
STORE_PATH = Path(os.environ.get("STORAGE_STORE", str(AUDIT_LOG_PATH.parent / "storage-store.db")))
STORE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _get_db() -> sqlite3.Connection:
    """Open the persistent SQLite store and ensure its schema exists."""
    conn = sqlite3.connect(STORE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT NOT NULL
        )
        """
    )
    return conn


def _upsert(key: str, value: str) -> None:
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO entries (key, value, deleted, last_updated) VALUES (?, ?, 0, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, deleted = 0, "
            "last_updated = excluded.last_updated",
            (key, value, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _record(event: str, operation: str, outcome: str, detail: str | None = None) -> dict[str, Any]:
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": event,
        "operation": operation,
        "role": CALLING_ROLE,
        "outcome": outcome,
        "detail": detail,
        "policy_reference": "docs/governance-policy.md",
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return record


def _authorize(operation: str) -> None:
    allowed_roles = ALLOW_LIST.get(operation, [])
    if CALLING_ROLE not in allowed_roles:
        record = _record("authorization_denied", operation, "authorization_denied")
        raise ToolError(
            f"authorization_denied: role '{record['role']}' may not call "
            f"'{operation}'. See {record['policy_reference']}."
        )


@mcp.tool
def write_entry(key: str, value: str) -> dict:
    _authorize("write_entry")
    _upsert(key, value)
    _record("storage_write", "write_entry", "success")
    return {"ok": True, "key": key}


@mcp.tool
def read_entry(key: str) -> dict:
    _authorize("read_entry")
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT value FROM entries WHERE key = ? AND deleted = 0", (key,)
        ).fetchone()
    finally:
        conn.close()
    _record("storage_read", "read_entry", "success")
    return {"key": key, "value": row["value"] if row else None}


@mcp.tool
def list_entries() -> dict:
    _authorize("list_entries")
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT key FROM entries WHERE deleted = 0 ORDER BY key"
        ).fetchall()
    finally:
        conn.close()
    _record("storage_list", "list_entries", "success")
    return {"keys": [row["key"] for row in rows]}


@mcp.tool
def update_entry(key: str, value: str) -> dict:
    _authorize("update_entry")
    _upsert(key, value)
    _record("storage_update", "update_entry", "success")
    return {"ok": True, "key": key}


@mcp.tool
def delete_entry(key: str) -> dict:
    """Soft-delete: the row is marked deleted, not removed (Module 3 behaviour)."""
    _authorize("delete_entry")
    conn = _get_db()
    try:
        cursor = conn.execute(
            "UPDATE entries SET deleted = 1, last_updated = ? WHERE key = ? AND deleted = 0",
            (_now(), key),
        )
        conn.commit()
        existed = cursor.rowcount > 0
    finally:
        conn.close()
    _record("storage_delete", "delete_entry", "success")
    return {"ok": True, "deleted": existed}


@mcp.tool
def audit_read(limit: int = 50) -> dict:
    _authorize("audit_read")
    if not AUDIT_LOG_PATH.exists():
        return {"entries": []}
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    return {"entries": [json.loads(line) for line in lines if line.strip()]}


if __name__ == "__main__":
    mcp.run()
