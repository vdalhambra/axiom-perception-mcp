"""Core memory tools — save, recall, update, list, search patterns."""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastmcp import FastMCP
from pydantic import Field

from perception.db import get_conn, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_memory_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def recall_pattern(
        task: Annotated[str, Field(description="What you're trying to do, e.g. 'post tweet', 'create github PR', 'fill login form'")],
        app: Annotated[Optional[str], Field(description="Optional: filter by app name, e.g. 'twitter', 'github', 'linkedin'")] = None,
    ) -> dict:
        """Retrieve the best known workflow pattern for a task BEFORE attempting it.

        Call this first before starting any multi-step task. If a pattern exists,
        follow its steps exactly to skip all trial-and-error. Patterns are ranked
        by success rate — the top result is the community-proven approach.

        Returns the step-by-step workflow if found, or status='not_found' with a
        suggestion to complete manually and then save_pattern() for next time.
        """
        init_db()
        # Filter stop words that cause noisy matches ("to", "on", "an", etc.)
        _stop = {"to", "an", "the", "of", "in", "on", "at", "for", "is", "it",
                 "as", "be", "by", "do", "go", "my", "up", "or", "a"}
        words = [w for w in task.lower().split() if len(w) >= 3 and w not in _stop]
        if not words:
            words = [task.lower()]

        # Fetch candidates: any word must appear in the task field
        word_conditions = []
        params: list = []
        for w in words:
            like = f"%{w}%"
            word_conditions.append("LOWER(task) LIKE ?")
            params.append(like)

        sql = f"SELECT * FROM patterns WHERE ({' OR '.join(word_conditions)})"
        if app:
            sql += " AND LOWER(app) = ?"
            params.append(app.lower())

        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "status": "not_found",
                "message": f"No pattern found for '{task}'" + (f" in app='{app}'" if app else ""),
                "next_step": (
                    "Complete the task manually. Once done, call save_pattern() "
                    "with the steps that worked so future executions skip this discovery."
                ),
            }

        # Score by how many query words appear in the task name
        def relevance(row: dict) -> float:
            task_lower = row["task"].lower()
            hits = sum(1 for w in words if w in task_lower)
            return hits / len(words)

        ranked = sorted(rows, key=lambda r: (relevance(r), r["success_rate"], r["execution_count"]), reverse=True)

        # Short queries (1-2 words): all words must appear (no generic false positives)
        # Longer queries (3+): at least half the words must match
        MIN_RELEVANCE = 1.0 if len(words) <= 2 else 0.5
        top_relevance = relevance(ranked[0])
        if top_relevance < MIN_RELEVANCE:
            return {
                "status": "not_found",
                "message": f"No sufficiently relevant pattern found for '{task}'",
                "best_partial_match": ranked[0]["task"],
                "next_step": (
                    "Complete the task manually. Once done, call save_pattern() "
                    "with the steps that worked so future executions skip this discovery."
                ),
            }

        best = ranked[0]
        result = {
            "status": "found",
            "pattern_id": best["id"],
            "task": best["task"],
            "app": best["app"],
            "category": best["category"],
            "steps": json.loads(best["steps"]),
            "success_rate": f"{round(best['success_rate'] * 100, 1)}%",
            "execution_count": best["execution_count"],
            "avg_time_ms": best["avg_time_ms"],
            "source": best["source"],
            "version": best["version"],
            "notes": best["notes"],
        }
        if len(ranked) > 1:
            result["alternatives_available"] = len(ranked) - 1
            result["tip"] = "Call list_patterns() or search_patterns() to see alternative approaches."
        return result

    @mcp.tool
    def save_pattern(
        task: Annotated[str, Field(description="Short task description, e.g. 'post tweet with image on x.com'")],
        steps: Annotated[list[str], Field(description="Ordered list of action steps that successfully completed the task")],
        app: Annotated[str, Field(description="Target application, e.g. 'twitter', 'github', 'linkedin', 'generic'")] = "generic",
        category: Annotated[str, Field(description="Workflow category: 'social', 'dev', 'productivity', 'research', 'ecommerce', 'general'")] = "general",
        notes: Annotated[Optional[str], Field(description="Caveats, known issues, prerequisites, or tips for this pattern")] = None,
    ) -> dict:
        """Save a workflow pattern that successfully completed a task.

        Use this after successfully completing a multi-step task to preserve
        the exact steps. Future executions skip trial-and-error by following
        this pattern directly.

        Write steps as plain language instructions Claude can follow with any
        automation tool (Playwright MCP, Computer Use, etc.). Be specific about
        what to click, where to navigate, what to wait for.

        Example steps for posting a tweet:
        - "Navigate to https://x.com and wait for timeline to load"
        - "Click the 'Post' button in the left sidebar to open composer"
        - "Type tweet text in the composer textarea"
        - "Click the blue 'Post' button to submit"
        """
        init_db()
        pattern_id = uuid.uuid4().hex[:8]
        now = _now()

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO patterns
                   (id, task, app, category, steps, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pattern_id, task, app.lower(), category.lower(),
                 json.dumps(steps), notes, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "saved",
            "pattern_id": pattern_id,
            "task": task,
            "app": app,
            "steps_count": len(steps),
            "tip": (
                f"Call record_outcome('{pattern_id}', success=True) after each use "
                "to build the success rate metric."
            ),
        }

    @mcp.tool
    def update_pattern(
        pattern_id: Annotated[str, Field(description="Pattern ID from recall_pattern or list_patterns")],
        steps: Annotated[list[str], Field(description="New improved steps that replace the current ones")],
        notes: Annotated[Optional[str], Field(description="Updated notes — appended to existing notes if provided")] = None,
        reason: Annotated[Optional[str], Field(description="Why this version is better, e.g. '3 fewer clicks', 'avoids modal bug'")] = None,
    ) -> dict:
        """Replace a pattern's steps with a better or faster solution.

        Use when you discover a more efficient way to complete the same task.
        The version number increments so the history is traceable.
        Success rate resets to 1.0 for the new version — it'll recalibrate as it's used.

        This is how the collective intelligence self-improves: if user B finds a
        faster path than user A, calling update_pattern promotes that approach for everyone.
        """
        init_db()
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
            if not row:
                return {"status": "error", "message": f"Pattern '{pattern_id}' not found."}

            existing_notes = row["notes"] or ""
            if reason:
                tag = f"[v{row['version'] + 1}] {reason}"
                new_notes = f"{existing_notes}\n{tag}".strip() if existing_notes else tag
            else:
                new_notes = notes if notes is not None else existing_notes

            conn.execute(
                """UPDATE patterns
                   SET steps = ?, notes = ?, version = version + 1,
                       success_rate = 1.0, execution_count = 0,
                       updated_at = ?
                   WHERE id = ?""",
                (json.dumps(steps), new_notes, _now(), pattern_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "updated",
            "pattern_id": pattern_id,
            "task": row["task"],
            "new_version": row["version"] + 1,
            "old_steps_count": len(json.loads(row["steps"])),
            "new_steps_count": len(steps),
        }

    @mcp.tool
    def record_outcome(
        pattern_id: Annotated[str, Field(description="ID of the pattern that was executed")],
        success: Annotated[bool, Field(description="True if the task completed successfully, False if it failed")],
        time_ms: Annotated[Optional[int], Field(description="Execution time in milliseconds (optional but valuable for ranking)", ge=0)] = None,
        error: Annotated[Optional[str], Field(description="Brief error description if success=False")] = None,
    ) -> dict:
        """Record the result of executing a pattern.

        Call this after every pattern execution — success or failure.
        Tracks success_rate and average execution time, which determine
        which patterns get promoted when multiple options exist for the same task.

        Patterns with >80% success rate are flagged as reliable.
        Patterns with <50% success rate after 5+ executions are flagged for review.
        """
        init_db()
        exec_id = uuid.uuid4().hex[:8]
        now = _now()

        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
            if not row:
                return {"status": "error", "message": f"Pattern '{pattern_id}' not found."}

            conn.execute(
                "INSERT INTO executions (id, pattern_id, success, time_ms, error, timestamp) VALUES (?,?,?,?,?,?)",
                (exec_id, pattern_id, int(success), time_ms, error, now),
            )

            stats = conn.execute(
                """SELECT COUNT(*) as n, SUM(success) as wins, AVG(time_ms) as avg_t
                   FROM executions WHERE pattern_id = ?""",
                (pattern_id,),
            ).fetchone()

            new_rate = stats["wins"] / stats["n"]
            new_avg = int(stats["avg_t"] or 0)
            conn.execute(
                "UPDATE patterns SET success_rate=?, avg_time_ms=?, execution_count=?, updated_at=? WHERE id=?",
                (new_rate, new_avg, stats["n"], now, pattern_id),
            )
            conn.commit()
        finally:
            conn.close()

        status_note = ""
        if stats["n"] >= 5:
            if new_rate < 0.5:
                status_note = "WARNING: success rate below 50% — consider updating this pattern."
            elif new_rate >= 0.9:
                status_note = "Pattern is highly reliable (>90% success rate)."

        return {
            "status": "recorded",
            "pattern_id": pattern_id,
            "execution_id": exec_id,
            "success": success,
            "new_success_rate": f"{round(new_rate * 100, 1)}%",
            "total_executions": stats["n"],
            "avg_time_ms": new_avg,
            "note": status_note or None,
        }

    @mcp.tool
    def list_patterns(
        app: Annotated[Optional[str], Field(description="Filter by app name, e.g. 'twitter', 'github'")] = None,
        category: Annotated[Optional[str], Field(description="Filter by category: 'social', 'dev', 'productivity'")] = None,
        source: Annotated[Optional[str], Field(description="Filter by source: 'local' (your patterns) or 'community'")] = None,
    ) -> dict:
        """Browse all known patterns, optionally filtered by app, category, or source.

        Use this to discover what workflows are already systematized before
        manually figuring out a task. Community patterns ship pre-loaded.
        """
        init_db()
        sql = "SELECT * FROM patterns WHERE 1=1"
        params: list = []

        if app:
            sql += " AND LOWER(app) = ?"
            params.append(app.lower())
        if category:
            sql += " AND LOWER(category) = ?"
            params.append(category.lower())
        if source:
            sql += " AND LOWER(source) = ?"
            params.append(source.lower())

        sql += " ORDER BY app ASC, success_rate DESC"

        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        patterns = [
            {
                "id": r["id"],
                "task": r["task"],
                "app": r["app"],
                "category": r["category"],
                "success_rate": f"{round(r['success_rate'] * 100)}%",
                "executions": r["execution_count"],
                "steps": len(json.loads(r["steps"])),
                "source": r["source"],
                "version": f"v{r['version']}",
            }
            for r in rows
        ]

        apps = sorted({p["app"] for p in patterns})
        return {
            "total": len(patterns),
            "apps_covered": apps,
            "patterns": patterns,
        }

    @mcp.tool
    def search_patterns(
        query: Annotated[str, Field(description="Search keyword, e.g. 'tweet', 'pull request', 'login form', 'screenshot'")],
    ) -> dict:
        """Search patterns by keyword across task name, app, category, and notes.

        Use this when recall_pattern returns not_found — broader search may find
        a related pattern that can be adapted for your use case.
        """
        init_db()
        words = [w for w in query.lower().split() if len(w) >= 2] or [query.lower()]
        word_conditions = []
        params: list = []
        for w in words:
            like = f"%{w}%"
            word_conditions.append(
                "(LOWER(task) LIKE ? OR LOWER(app) LIKE ? OR LOWER(category) LIKE ? OR LOWER(notes) LIKE ?)"
            )
            params.extend([like, like, like, like])

        sql = f"""SELECT * FROM patterns
                  WHERE {' OR '.join(word_conditions)}
                  ORDER BY success_rate DESC, execution_count DESC
                  LIMIT 10"""
        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        results = [
            {
                "id": r["id"],
                "task": r["task"],
                "app": r["app"],
                "success_rate": f"{round(r['success_rate'] * 100)}%",
                "steps_count": len(json.loads(r["steps"])),
                "source": r["source"],
                "version": f"v{r['version']}",
            }
            for r in rows
        ]

        return {
            "query": query,
            "results_count": len(results),
            "results": results,
        }

    @mcp.tool
    def export_pattern(
        pattern_id: Annotated[str, Field(description="ID of the pattern to export for sharing")],
    ) -> dict:
        """Export a pattern as a shareable JSON object for community contribution.

        After exporting, share this JSON in a GitHub issue at
        https://github.com/vdalhambra/axiom-perception-mcp with title
        'New Pattern: <task name>' — it'll be reviewed and added to the community database
        so all future users benefit from your discovery.
        """
        init_db()
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        finally:
            conn.close()

        if not row:
            return {"status": "error", "message": f"Pattern '{pattern_id}' not found."}

        return {
            "status": "ready_to_share",
            "contribution": {
                "task": row["task"],
                "app": row["app"],
                "category": row["category"],
                "steps": json.loads(row["steps"]),
                "notes": row["notes"],
                "version": row["version"],
                "success_rate": round(row["success_rate"], 3),
                "execution_count": row["execution_count"],
            },
            "instructions": (
                f"Open https://github.com/vdalhambra/axiom-perception-mcp/issues/new "
                f"with title 'New Pattern: {row['task']}' and paste the JSON above. "
                "Once merged, all axiom-perception-mcp users will have access to this pattern."
            ),
        }
