"""Multi-agent coordination — shared notes for multiple Claude instances."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastmcp import FastMCP
from pydantic import Field

from perception.db import get_conn, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_minutes(ts: str) -> float:
    try:
        then = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        return round((now - then).total_seconds() / 60, 1)
    except Exception:
        return -1.0


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(expires_at)
    except Exception:
        return False


def register_coordination_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def share_note(
        key: Annotated[str, Field(
            description=(
                "Note key — use namespaced prefixes: "
                "'task:<name>' for assignments, 'result:<name>' for outputs, "
                "'lock:<name>' to claim exclusive access, 'status:<name>' for progress, "
                "'data:<name>' for collected data"
            ),
            max_length=100,
        )],
        value: Annotated[str, Field(
            description="Note content — plain text, JSON, status string, or any data to share",
            max_length=5000,
        )],
        agent_id: Annotated[Optional[str], Field(
            description="Identifier for this agent/session, e.g. 'researcher-1', 'distributor', 'cron-twitter'",
            max_length=50,
        )] = None,
        ttl_minutes: Annotated[Optional[int], Field(
            description="Minutes until this note auto-expires. None = never expires.",
            ge=1,
            le=10080,  # max 7 days
        )] = None,
    ) -> dict:
        """Write a shared note that any Claude agent can read in any session.

        Use for multi-agent coordination: one agent writes results or status,
        another reads them. Also useful for passing state between sequential
        sessions without a full workflow checkpoint.

        Key naming convention:
        - 'task:<name>'   → task assignment (who's doing what, prevents duplicate work)
        - 'result:<name>' → output from a completed task
        - 'lock:<name>'   → claim exclusive access before starting, delete when done
        - 'status:<name>' → live progress update
        - 'data:<name>'   → collected data for another agent to consume

        Example — agent 1 writes research, agent 2 reads and acts:
        share_note('result:competitor_analysis', json.dumps(findings), agent_id='researcher-1')
        """
        init_db()
        now = _now()
        expires_at = None
        if ttl_minutes:
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()

        conn = get_conn()
        try:
            # Preserve original created_at if key already exists
            existing = conn.execute(
                "SELECT created_at FROM shared_notes WHERE key = ?", (key,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now

            conn.execute(
                """INSERT OR REPLACE INTO shared_notes
                   (key, value, agent_id, expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, value, agent_id, expires_at, created_at, now),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "saved",
            "key": key,
            "value_length": len(value),
            "agent_id": agent_id,
            "expires_at": expires_at,
            "tip": f"Read with: read_note('{key}')",
        }

    @mcp.tool
    def read_note(
        key: Annotated[str, Field(
            description="Note key to read, e.g. 'result:competitor_analysis', 'lock:linkedin_post'",
            max_length=100,
        )],
    ) -> dict:
        """Read a shared note written by any agent in any session.

        Returns the note value plus metadata: who wrote it, when, how old it is.
        Expired notes are automatically cleaned up and returned as 'expired'.

        Use to consume results from another agent, check if a task is already claimed,
        or read state left by a previous session.
        """
        init_db()
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM shared_notes WHERE key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return {
                "status": "not_found",
                "key": key,
                "tip": "Use list_notes() to see all available keys.",
            }

        if _is_expired(row["expires_at"]):
            conn = get_conn()
            try:
                conn.execute("DELETE FROM shared_notes WHERE key = ?", (key,))
                conn.commit()
            finally:
                conn.close()
            return {
                "status": "expired",
                "key": key,
                "message": "Note has expired and was cleaned up.",
            }

        return {
            "status": "found",
            "key": key,
            "value": row["value"],
            "agent_id": row["agent_id"],
            "age_minutes": _elapsed_minutes(row["updated_at"]),
            "expires_at": row["expires_at"],
            "updated_at": row["updated_at"],
        }

    @mcp.tool
    def list_notes(
        prefix: Annotated[Optional[str], Field(
            description="Filter by key prefix, e.g. 'task:', 'result:', 'lock:'. Omit to see all.",
            max_length=50,
        )] = None,
    ) -> dict:
        """List all active shared notes, optionally filtered by key prefix.

        Use to see what data is available from other agents, check for active task locks,
        or audit the shared state before starting a coordinated workflow.
        """
        init_db()
        conn = get_conn()
        try:
            if prefix:
                rows = conn.execute(
                    "SELECT * FROM shared_notes WHERE key LIKE ? ORDER BY updated_at DESC LIMIT 100",
                    (f"{prefix}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM shared_notes ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
        finally:
            conn.close()

        notes = []
        for r in rows:
            if _is_expired(r["expires_at"]):
                continue
            notes.append({
                "key": r["key"],
                "value_preview": (r["value"] or "")[:100],
                "agent_id": r["agent_id"],
                "age_minutes": _elapsed_minutes(r["updated_at"]),
                "expires_at": r["expires_at"],
            })

        return {
            "total": len(notes),
            "prefix_filter": prefix,
            "notes": notes,
        }

    @mcp.tool
    def delete_note(
        key: Annotated[str, Field(
            description="Note key to delete, e.g. 'lock:linkedin_post' after completing the task",
            max_length=100,
        )],
    ) -> dict:
        """Delete a shared note (release a lock, clear consumed results, clean up).

        Use to release task locks after completing the task, or remove temporary
        coordination data that's no longer needed.
        """
        init_db()
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT key FROM shared_notes WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return {"status": "not_found", "key": key}
            conn.execute("DELETE FROM shared_notes WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()

        return {"status": "deleted", "key": key}
