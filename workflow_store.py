import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from actor import Actor


DATABASE_PATH = Path(os.getenv("NEBULOUS_WORKFLOW_DB", "nebulous-workflows.sqlite3"))
APPROVAL_TTL_SECONDS = int(os.getenv("NEBULOUS_APPROVAL_TTL_SECONDS", "900"))
CLARIFICATION_TTL_SECONDS = int(os.getenv("NEBULOUS_CLARIFICATION_TTL_SECONDS", "1800"))


def now() -> str:
    return datetime.now(UTC).isoformat()


def expiry(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _json_default(value: Any):
    if isinstance(value, Actor):
        return {"subject": value.subject, "tenant_id": value.tenant_id, "display_name": value.display_name, "roles": sorted(value.roles)}
    raise TypeError(f"Cannot serialize {type(value)!r}")


def _restore_state(value: dict[str, Any]) -> dict[str, Any]:
    actor = value.get("actor")
    if isinstance(actor, dict):
        value["actor"] = Actor(
            subject=actor["subject"],
            tenant_id=actor.get("tenant_id"),
            display_name=actor.get("display_name"),
            roles=frozenset(actor.get("roles", [])),
        )
    return value


class WorkflowStore:
    """SQLite persistence for execution, approval, and clarification state.

    It intentionally owns only state transitions. The executor remains the
    synchronous workflow engine in Phase 3.
    """

    def __init__(self, database_path: Path | str = DATABASE_PATH):
        self.database_path = str(database_path)
        self.initialize()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    actor_subject TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
                );
                CREATE TABLE IF NOT EXISTS clarifications (
                    session_id TEXT PRIMARY KEY,
                    actor_subject TEXT NOT NULL,
                    original_request TEXT NOT NULL,
                    partial_plan_json TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
            """)

    def create_execution(self, state: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute("SELECT state_json FROM executions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if existing:
                    connection.execute("COMMIT")
                    value = _restore_state(json.loads(existing["state_json"]))
                    value.setdefault("_version", 1)
                    return value
            state["_version"] = 1
            serialized = json.dumps(state, default=_json_default, ensure_ascii=False)
            connection.execute(
                "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (state["execution_id"], state["session_id"], state["actor"].subject, idempotency_key, state["status"], 1, serialized, now(), now()),
            )
            connection.execute("COMMIT")
        return state

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT state_json, version FROM executions WHERE execution_id = ?", (execution_id,)).fetchone()
        if not row:
            return None
        value = _restore_state(json.loads(row["state_json"]))
        value["_version"] = row["version"]
        return value

    def save_execution(self, state: dict[str, Any]) -> None:
        expected_version = state.get("_version")
        if not isinstance(expected_version, int):
            raise ValueError("Workflow state has no version.")
        next_version = expected_version + 1
        state["_version"] = next_version
        serialized = json.dumps(state, default=_json_default, ensure_ascii=False)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE executions SET state_json = ?, status = ?, version = ?, updated_at = ? WHERE execution_id = ? AND version = ?",
                (serialized, state["status"], next_version, now(), state["execution_id"], expected_version),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise RuntimeError("Workflow state changed concurrently.")
            connection.execute("COMMIT")

    def create_approval(self, approval: dict[str, Any]) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL)",
                (approval["approval_id"], approval["execution_id"], approval["task_id"], approval["actor_subject"], json.dumps(approval["parameters"]), now(), approval["expires_at"]),
            )

    def save_execution_with_approval(self, state: dict[str, Any], approval: dict[str, Any]) -> None:
        """Persist the waiting task and exact approval atomically."""
        expected_version = state.get("_version")
        if not isinstance(expected_version, int):
            raise ValueError("Workflow state has no version.")
        next_version = expected_version + 1
        state["_version"] = next_version
        serialized = json.dumps(state, default=_json_default, ensure_ascii=False)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE executions SET state_json = ?, status = ?, version = ?, updated_at = ? WHERE execution_id = ? AND version = ?",
                (serialized, state["status"], next_version, now(), state["execution_id"], expected_version),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise RuntimeError("Workflow state changed concurrently.")
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL)",
                (approval["approval_id"], approval["execution_id"], approval["task_id"], approval["actor_subject"], json.dumps(approval["parameters"]), now(), approval["expires_at"]),
            )
            connection.execute("COMMIT")

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "parameters": json.loads(row["parameters_json"])}

    def get_pending_approval(self, execution_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE execution_id = ? AND status = 'pending' AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
                (execution_id, now()),
            ).fetchone()
        return {**dict(row), "parameters": json.loads(row["parameters_json"])} if row else None

    def claim_approval(self, approval_id: str, actor_subject: str, outcome: str) -> dict[str, Any] | None:
        """Atomically consume/reject one unexpired actor-bound approval."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if not row or row["actor_subject"] != actor_subject or row["status"] != "pending" or row["expires_at"] <= now():
                connection.execute("ROLLBACK")
                return None
            cursor = connection.execute(
                "UPDATE approvals SET status = ?, consumed_at = ? WHERE approval_id = ? AND status = 'pending'",
                (outcome, now(), approval_id),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                return None
            connection.execute("COMMIT")
        return {**dict(row), "parameters": json.loads(row["parameters_json"])}

    def save_clarification(self, session_id: str, actor: Actor, original_request: str, partial_plan: dict[str, Any], question: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO clarifications VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (session_id, actor.subject, original_request, json.dumps(partial_plan), question, expiry(CLARIFICATION_TTL_SECONDS)),
            )

    def claim_clarification(self, session_id: str, actor_subject: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM clarifications WHERE session_id = ?", (session_id,)).fetchone()
            if not row or row["actor_subject"] != actor_subject or row["status"] != "pending" or row["expires_at"] <= now():
                connection.execute("ROLLBACK")
                return None
            connection.execute("UPDATE clarifications SET status = 'consumed' WHERE session_id = ?", (session_id,))
            connection.execute("COMMIT")
        return {**dict(row), "partial_plan": json.loads(row["partial_plan_json"])}

    def get_clarification(self, session_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM clarifications WHERE session_id = ?", (session_id,)).fetchone()
        return {**dict(row), "partial_plan": json.loads(row["partial_plan_json"])} if row else None

    def reopen_clarification(self, session_id: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE clarifications SET status = 'pending' WHERE session_id = ? AND status = 'consumed'", (session_id,))

    def reset_for_tests(self) -> None:
        """Test-only cleanup for the process default store."""
        with self.connection() as connection:
            connection.executescript("DELETE FROM approvals; DELETE FROM clarifications; DELETE FROM executions;")


workflow_store = WorkflowStore()
