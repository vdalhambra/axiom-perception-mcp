"""Community patterns — fetch shared patterns from the GitHub database."""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
from fastmcp import FastMCP
from pydantic import Field

from perception.db import get_conn, init_db

COMMUNITY_URL = (
    "https://raw.githubusercontent.com/vdalhambra/axiom-perception-mcp"
    "/main/patterns/community_patterns.json"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_community_tools(mcp: FastMCP) -> None:

    @mcp.tool
    def fetch_community_patterns(
        app: Annotated[Optional[str], Field(description="Import only patterns for this app, e.g. 'twitter', 'github'. Omit for all.")] = None,
        force_refresh: Annotated[bool, Field(description="Re-import patterns even if you already have that version locally")] = False,
    ) -> dict:
        """Download the community patterns database and import any new or updated patterns.

        Community patterns are contributed by users worldwide. Calling this means you
        start with battle-tested workflows for Twitter, GitHub, LinkedIn, and more —
        no cold start, no trial-and-error on day one.

        Run once after installing, then periodically (weekly) to get new patterns.
        Only imports patterns where the community version is newer than your local copy.

        Requires internet access to reach github.com.
        """
        init_db()

        try:
            resp = httpx.get(COMMUNITY_URL, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            return {
                "status": "error",
                "message": f"Could not fetch community patterns: {e}",
                "tip": "Check your internet connection or try again later.",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

        all_patterns = data.get("patterns", [])
        if app:
            filtered = [p for p in all_patterns if p.get("app", "").lower() == app.lower()]
        else:
            filtered = all_patterns

        imported = 0
        updated = 0
        skipped = 0

        conn = get_conn()
        try:
            for p in filtered:
                task = p.get("task", "")
                p_app = p.get("app", "generic").lower()
                p_version = p.get("version", 1)

                existing = conn.execute(
                    """SELECT id, version FROM patterns
                       WHERE LOWER(task) = ? AND LOWER(app) = ? AND source = 'community'""",
                    (task.lower(), p_app),
                ).fetchone()

                if existing:
                    if not force_refresh and existing["version"] >= p_version:
                        skipped += 1
                        continue
                    conn.execute(
                        """UPDATE patterns
                           SET steps=?, notes=?, version=?, success_rate=?,
                               execution_count=?, updated_at=?
                           WHERE id=?""",
                        (
                            json.dumps(p["steps"]),
                            p.get("notes"),
                            p_version,
                            p.get("success_rate", 0.95),
                            p.get("execution_count", 0),
                            _now(),
                            existing["id"],
                        ),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """INSERT INTO patterns
                           (id, task, app, category, steps, success_rate,
                            execution_count, source, version, notes, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            uuid.uuid4().hex[:8],
                            task,
                            p_app,
                            p.get("category", "general"),
                            json.dumps(p["steps"]),
                            p.get("success_rate", 0.95),
                            p.get("execution_count", 0),
                            "community",
                            p_version,
                            p.get("notes"),
                            _now(),
                            _now(),
                        ),
                    )
                    imported += 1

            conn.commit()
        finally:
            conn.close()

        return {
            "status": "success",
            "community_version": data.get("version", "unknown"),
            "total_available": len(all_patterns),
            "filtered_to_app": app or "all",
            "newly_imported": imported,
            "updated_to_newer_version": updated,
            "already_up_to_date": skipped,
            "tip": (
                "Call list_patterns() to browse what's available, "
                "or recall_pattern(task='...') before your next multi-step task."
            ),
        }
