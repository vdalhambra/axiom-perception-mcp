"""Multi-agent coordination — shared notes and progress tracking for multiple Claude instances."""

import json
import uuid
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

    # ── Agent Progress Broadcasting ──────────────────────────────────────────

    @mcp.tool
    def report_step(
        agent_id: Annotated[str, Field(
            description="Identifier for this agent, e.g. 'twitter-poster', 'linkedin-agent', 'reddit-submitter'",
            max_length=100,
        )],
        task: Annotated[str, Field(
            description="The overall task this agent is working on, e.g. 'post v2.0.0 announcement', 'submit to 5 directories'",
            max_length=200,
        )],
        step: Annotated[str, Field(
            description="What was just completed — be specific and irreversible: 'tweet 4/7 published', 'submitted to r/ClaudeAI, post URL: ...', 'step 3/5 done: Glama accepted'",
            max_length=300,
        )],
        result: Annotated[Optional[dict], Field(
            description="Any data worth preserving for recovery: URLs, IDs, confirmation codes, counts",
        )] = None,
    ) -> dict:
        """Record a completed step from a running agent — for crash recovery and orchestrator visibility.

        Call this ONLY after actions that are confirmed, completed, and irreversible.
        Not every micro-step — only the ones that matter for recovery:
        - A post that went live (URL to prove it)
        - A form successfully submitted (confirmation ID)
        - A file uploaded, an email sent, a PR created

        This is how the orchestrator knows what was done if a session crashes.
        Next session: get_agent_progress() returns the full log — skip what's done,
        resume from where the agent stopped.

        Token cost: ~100 tokens per call. Only log points of no return.
        """
        init_db()
        now = datetime.now(timezone.utc).isoformat()
        step_id = uuid.uuid4().hex[:8]

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO agent_progress (id, agent_id, task, step, result, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (step_id, agent_id.strip(), task.strip(), step.strip(),
                 json.dumps(result) if result else None, now),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "logged",
            "step_id": step_id,
            "agent_id": agent_id,
            "task": task,
            "step": step,
            "result_saved": result is not None,
        }

    @mcp.tool
    def get_agent_progress(
        task: Annotated[Optional[str], Field(
            description="Filter by task name (partial match), e.g. 'post announcement', 'submit directories'",
            max_length=200,
        )] = None,
        agent_id: Annotated[Optional[str], Field(
            description="Filter by specific agent ID, e.g. 'twitter-poster'",
            max_length=100,
        )] = None,
        last_minutes: Annotated[Optional[int], Field(
            description="Only show steps from the last N minutes. Omit to see full history.",
            ge=1,
            le=10080,
        )] = None,
    ) -> dict:
        """Retrieve the progress log of running or recently completed agents.

        Call this after a crash, at the start of a recovery session, or any time
        you need to know what an agent has already done before deciding what to do next.

        Returns a chronological log of completed steps with any saved result data.
        Use this to:
        - Know which platform posts went live before a crash
        - Skip already-completed steps when relaunching an agent
        - Audit what happened across a multi-agent run

        Example — after Playwright crashes mid-distribution:
        get_agent_progress(task='post v2.0.0 announcement')
        → "twitter: tweets 1-4 published. linkedin: not started. reddit: post submitted."
        → Relaunch only linkedin and reddit from step 1, continue twitter from tweet 5.
        """
        init_db()

        sql = "SELECT * FROM agent_progress WHERE 1=1"
        params: list = []

        if task:
            sql += " AND LOWER(task) LIKE ?"
            params.append(f"%{task.lower().strip()}%")
        if agent_id:
            sql += " AND LOWER(agent_id) LIKE ?"
            params.append(f"%{agent_id.lower().strip()}%")
        if last_minutes:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=last_minutes)
            ).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)

        sql += " ORDER BY created_at ASC LIMIT 200"

        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        steps = []
        for r in rows:
            result_data = None
            if r["result"]:
                try:
                    result_data = json.loads(r["result"])
                except Exception:
                    result_data = r["result"]
            elapsed = _elapsed_minutes(r["created_at"])
            steps.append({
                "step_id": r["id"],
                "agent_id": r["agent_id"],
                "task": r["task"],
                "step": r["step"],
                "result": result_data,
                "logged_ago_minutes": elapsed,
                "logged_at": r["created_at"],
            })

        # Group by agent for a cleaner summary
        agents_seen: dict = {}
        for s in steps:
            aid = s["agent_id"]
            if aid not in agents_seen:
                agents_seen[aid] = {"steps_completed": 0, "last_step": None}
            agents_seen[aid]["steps_completed"] += 1
            agents_seen[aid]["last_step"] = s["step"]

        return {
            "total_steps": len(steps),
            "agents": agents_seen,
            "log": steps,
            "tip": (
                "Use this log to skip completed steps when relaunching agents after a crash."
                if steps else
                "No progress logged yet for these filters."
            ),
        }

    @mcp.tool
    def clear_agent_progress(
        task: Annotated[str, Field(
            description="Task name to clear (exact or partial match), e.g. 'post v2.0.0 announcement'",
            max_length=200,
        )],
        agent_id: Annotated[Optional[str], Field(
            description="Only clear for a specific agent. Omit to clear all agents for this task.",
            max_length=100,
        )] = None,
    ) -> dict:
        """Clear the progress log for a completed or abandoned task.

        Use after a task is fully done and the log is no longer needed for recovery.
        Keeps the database clean — old progress logs have no value once the task
        is confirmed complete across all agents.
        """
        init_db()
        sql = "DELETE FROM agent_progress WHERE LOWER(task) LIKE ?"
        params: list = [f"%{task.lower().strip()}%"]

        if agent_id:
            sql += " AND LOWER(agent_id) LIKE ?"
            params.append(f"%{agent_id.lower().strip()}%")

        conn = get_conn()
        try:
            cursor = conn.execute(sql, params)
            deleted = cursor.rowcount
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "cleared",
            "task": task,
            "agent_id": agent_id,
            "steps_deleted": deleted,
        }
