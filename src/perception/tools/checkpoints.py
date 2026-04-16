"""Workflow checkpointing — save and resume multi-step tasks across sessions."""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastmcp import FastMCP
from pydantic import Field

from perception.db import get_conn, init_db

_CHECKPOINT_ID_RE = re.compile(r'^[a-f0-9]{8}$')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_minutes(ts: str) -> float:
    try:
        then = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        return round((now - then).total_seconds() / 60, 1)
    except Exception:
        return -1.0


def register_checkpoint_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def save_checkpoint(
        workflow: Annotated[str, Field(
            description="Workflow name, e.g. 'submit perception mcp to 5 directories', 'configure glama dockerfile'",
            max_length=200,
        )],
        current_step: Annotated[int, Field(
            description="The step you just completed (1-based). Step 3 means you finished step 3.",
            ge=1,
        )],
        total_steps: Annotated[int, Field(
            description="Total number of steps in this workflow.",
            ge=1,
        )],
        context: Annotated[Optional[str], Field(
            description="WHERE you are right now: current URL, app state, what you were looking at",
            max_length=500,
        )] = None,
        variables: Annotated[Optional[dict], Field(
            description="Any key-value data you'll need to continue: IDs, collected URLs, pending items, counts",
        )] = None,
    ) -> dict:
        """Save progress in a multi-step workflow so you can resume after any interruption.

        Call this after each significant step in long workflows. If the session ends,
        the browser closes, or Claude times out — resume_checkpoint() picks up exactly
        where you left off with full context and saved variables.

        This is empirical state. CLAUDE.md holds static rules — this holds live progress.

        Example — submitting to 5 directories (step 2 done, 3 remaining):
        save_checkpoint(
            workflow='submit perception mcp to directories',
            current_step=2,
            total_steps=5,
            context='Just submitted to Glama. Smithery next.',
            variables={'submitted_to': ['glama', 'mcpize'], 'glama_url': 'glama.ai/mcp/...'}
        )
        """
        init_db()
        now = _now()
        variables_json = json.dumps(variables or {})

        conn = get_conn()
        try:
            # Upsert: update if in-progress checkpoint exists for this workflow
            existing = conn.execute(
                "SELECT id FROM checkpoints WHERE LOWER(workflow) = ? AND status = 'in_progress'",
                (workflow.lower().strip(),),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE checkpoints
                       SET current_step=?, total_steps=?, context=?, variables=?, updated_at=?
                       WHERE id=?""",
                    (current_step, total_steps, context, variables_json, now, existing["id"]),
                )
                checkpoint_id = existing["id"]
                action = "updated"
            else:
                checkpoint_id = uuid.uuid4().hex[:8]
                conn.execute(
                    """INSERT INTO checkpoints
                       (id, workflow, total_steps, current_step, context, variables, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?, ?)""",
                    (checkpoint_id, workflow.strip(), total_steps, current_step, context, variables_json, now, now),
                )
                action = "created"

            conn.commit()
        finally:
            conn.close()

        remaining = total_steps - current_step
        msg = (
            f"{remaining} step(s) remaining. Call resume_checkpoint() in any future session to continue."
            if remaining > 0
            else "All steps completed! Call complete_checkpoint() to mark this workflow as done."
        )
        return {
            "status": "saved",
            "action": action,
            "checkpoint_id": checkpoint_id,
            "workflow": workflow,
            "progress": f"{current_step}/{total_steps}",
            "steps_remaining": remaining,
            "variables_saved": sorted((variables or {}).keys()),
            "tip": msg,
        }

    @mcp.tool
    def resume_checkpoint(
        workflow: Annotated[str, Field(
            description="Workflow name (partial match OK), e.g. 'submit perception' or 'glama'",
            max_length=200,
        )],
    ) -> dict:
        """Retrieve the most recent in-progress checkpoint for a workflow.

        Call at the start of any session where you might be continuing a long task.
        Returns exactly where you left off: step N of M, what you were looking at,
        and any variables you saved.

        Returns status='not_found' if nothing is in progress — start fresh.

        This is how Claude picks up mid-workflow after session ends:
        no re-discovery, no re-doing completed steps, no lost progress.
        """
        init_db()
        conn = get_conn()
        try:
            row = conn.execute(
                """SELECT * FROM checkpoints
                   WHERE LOWER(workflow) LIKE ? AND status = 'in_progress'
                   ORDER BY updated_at DESC LIMIT 1""",
                (f"%{workflow.lower().strip()}%",),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return {
                "status": "not_found",
                "workflow": workflow,
                "message": "No in-progress checkpoint found. Starting fresh.",
                "tip": "Call save_checkpoint() after each step to enable resuming in future sessions.",
            }

        variables: dict = {}
        try:
            variables = json.loads(row["variables"] or "{}")
        except Exception:
            pass

        elapsed = _elapsed_minutes(row["updated_at"])
        next_step = row["current_step"] + 1
        remaining = row["total_steps"] - row["current_step"]

        return {
            "status": "found",
            "checkpoint_id": row["id"],
            "workflow": row["workflow"],
            "completed_steps": row["current_step"],
            "total_steps": row["total_steps"],
            "next_step": next_step,
            "steps_remaining": remaining,
            "context": row["context"],
            "variables": variables,
            "last_saved_ago_minutes": elapsed,
            "tip": (
                f"Resume from step {next_step}/{row['total_steps']}. "
                f"You completed {row['current_step']} step(s) {elapsed:.0f} min ago."
            ),
        }

    @mcp.tool
    def complete_checkpoint(
        checkpoint_id: Annotated[str, Field(
            description="8-char hex checkpoint ID from save_checkpoint or resume_checkpoint",
            max_length=8,
        )],
    ) -> dict:
        """Mark a workflow as fully completed.

        Call when you've finished all steps. Keeps the record (don't delete —
        completions are useful history) but removes it from the active list.
        """
        init_db()
        if not _CHECKPOINT_ID_RE.match(checkpoint_id):
            return {"status": "error", "message": f"Invalid checkpoint_id '{checkpoint_id}'."}

        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
            if not row:
                return {"status": "error", "message": f"Checkpoint '{checkpoint_id}' not found."}
            conn.execute(
                "UPDATE checkpoints SET status='completed', updated_at=? WHERE id=?",
                (_now(), checkpoint_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "completed",
            "checkpoint_id": checkpoint_id,
            "workflow": row["workflow"],
            "total_steps": row["total_steps"],
        }

    @mcp.tool
    def list_checkpoints(
        status: Annotated[str, Field(
            description="Filter: 'in_progress' (default), 'completed', 'abandoned', or 'all'",
        )] = "in_progress",
    ) -> dict:
        """Browse workflow checkpoints, filtered by status.

        Use at session start to check if any workflows need resuming,
        or to audit completed/abandoned workflows.
        """
        init_db()
        conn = get_conn()
        try:
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM checkpoints ORDER BY updated_at DESC LIMIT 50"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM checkpoints WHERE status = ? ORDER BY updated_at DESC LIMIT 50",
                    (status,),
                ).fetchall()
        finally:
            conn.close()

        checkpoints = [
            {
                "id": r["id"],
                "workflow": r["workflow"],
                "progress": f"{r['current_step']}/{r['total_steps']}",
                "status": r["status"],
                "last_saved_ago_minutes": _elapsed_minutes(r["updated_at"]),
                "context_preview": (r["context"] or "")[:80] or None,
            }
            for r in rows
        ]

        tip = (
            "Use resume_checkpoint(workflow='...') to continue any in-progress workflow."
            if checkpoints and status == "in_progress"
            else "No checkpoints found." if not checkpoints
            else None
        )
        return {
            "total": len(checkpoints),
            "filter": status,
            "checkpoints": checkpoints,
            "tip": tip,
        }

    @mcp.tool
    def abandon_checkpoint(
        checkpoint_id: Annotated[str, Field(
            description="8-char hex checkpoint ID to abandon",
            max_length=8,
        )],
        reason: Annotated[Optional[str], Field(
            description="Why abandoned, e.g. 'approach changed', 'no longer needed'",
            max_length=200,
        )] = None,
    ) -> dict:
        """Mark a checkpoint as abandoned (no longer needed).

        Keeps the record for audit but removes it from the active list.
        Use when you decide not to continue a workflow.
        """
        init_db()
        if not _CHECKPOINT_ID_RE.match(checkpoint_id):
            return {"status": "error", "message": f"Invalid checkpoint_id '{checkpoint_id}'."}

        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
            if not row:
                return {"status": "error", "message": f"Checkpoint '{checkpoint_id}' not found."}

            new_context = row["context"] or ""
            if reason:
                new_context = f"{new_context}\n[ABANDONED: {reason}]".strip()

            conn.execute(
                "UPDATE checkpoints SET status='abandoned', context=?, updated_at=? WHERE id=?",
                (new_context, _now(), checkpoint_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "abandoned",
            "checkpoint_id": checkpoint_id,
            "workflow": row["workflow"],
            "reason": reason,
        }
