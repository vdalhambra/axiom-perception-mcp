"""Community patterns — fetch shared patterns from the GitHub database."""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, model_validator

from perception.db import get_conn, init_db

COMMUNITY_URL = (
    "https://raw.githubusercontent.com/vdalhambra/axiom-perception-mcp"
    "/main/patterns/community_patterns.json"
)

# ---- Schema constants ----

ALLOWED_CATEGORIES = {"social", "dev", "productivity", "research", "ecommerce", "general", "content"}
MAX_TASK_LEN = 200
MAX_APP_LEN = 50
MAX_NOTES_LEN = 1000
MAX_STEP_LEN = 500
MAX_STEPS = 50

# Patterns in step text that suggest shell injection or exfiltration attempts
_SUSPICIOUS_PATTERNS = [
    r"\$\(",          # shell command substitution $(...)
    r"`[^`]{1,200}`", # backtick execution
    r";\s*rm\s",      # ; rm
    r"&&\s*curl\s",   # && curl (exfiltration)
    r">\s*/etc/",     # redirect to /etc/
    r"wget\s+http",   # wget download
    r"curl\s+-[a-zA-Z]*[oO]", # curl output to file
]
_SUSPICIOUS_RE = re.compile("|".join(_SUSPICIOUS_PATTERNS), re.IGNORECASE)


# ---- Pydantic validation model ----

class CommunityPattern(BaseModel):
    task: str
    app: str
    category: str
    version: int = 1
    success_rate: float = 0.8
    execution_count: int = 0
    steps: list[str]
    notes: Optional[str] = None

    @field_validator("task")
    @classmethod
    def validate_task(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task cannot be empty")
        if len(v) > MAX_TASK_LEN:
            raise ValueError(f"task exceeds {MAX_TASK_LEN} chars")
        return v

    @field_validator("app")
    @classmethod
    def validate_app(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r'^[a-z0-9._\-]{1,50}$', v):
            raise ValueError(f"app must be alphanumeric (dots/hyphens allowed), max {MAX_APP_LEN} chars")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of: {sorted(ALLOWED_CATEGORIES)}")
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version must be >= 1")
        return v

    @field_validator("success_rate")
    @classmethod
    def validate_success_rate(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("success_rate must be between 0.0 and 1.0")
        # Cap community-claimed rates — real usage will recalibrate
        return min(v, 0.90)

    @field_validator("execution_count")
    @classmethod
    def validate_execution_count(cls, v: int) -> int:
        if v < 0:
            raise ValueError("execution_count cannot be negative")
        # Don't trust community-reported counts; start fresh
        return 0

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: list) -> list:
        if not v:
            raise ValueError("steps list cannot be empty")
        if len(v) > MAX_STEPS:
            raise ValueError(f"steps cannot exceed {MAX_STEPS} items")
        cleaned = []
        for i, step in enumerate(v):
            if not isinstance(step, str):
                raise ValueError(f"step {i} must be a string")
            if len(step) > MAX_STEP_LEN:
                raise ValueError(f"step {i} exceeds {MAX_STEP_LEN} chars")
            if _SUSPICIOUS_RE.search(step):
                raise ValueError(f"step {i} contains suspicious shell-like pattern")
            cleaned.append(step.strip())
        return cleaned

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if len(v) > MAX_NOTES_LEN:
                raise ValueError(f"notes exceeds {MAX_NOTES_LEN} chars")
        return v or None


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

        Security: every pattern is validated against a strict schema before import.
        Patterns with invalid structure, suspicious content, or oversized fields are
        rejected and reported — they are never written to your local database.
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

        if not isinstance(data, dict) or "patterns" not in data:
            return {
                "status": "error",
                "message": "Community patterns file has unexpected structure. Aborting import.",
            }

        all_raw = data.get("patterns", [])
        if not isinstance(all_raw, list):
            return {"status": "error", "message": "patterns field must be a list."}

        # Filter by app before validation (cheap check first)
        if app:
            app_lower = app.strip().lower()
            all_raw = [p for p in all_raw if isinstance(p, dict) and p.get("app", "").lower() == app_lower]

        # Validate each pattern against schema
        validated: list[CommunityPattern] = []
        rejected: list[dict] = []
        for i, raw in enumerate(all_raw):
            if not isinstance(raw, dict):
                rejected.append({"index": i, "reason": "not a dict"})
                continue
            try:
                validated.append(CommunityPattern(**raw))
            except Exception as exc:
                rejected.append({
                    "index": i,
                    "task": raw.get("task", "<unknown>")[:60],
                    "reason": str(exc),
                })

        imported = 0
        updated = 0
        skipped = 0

        conn = get_conn()
        try:
            for p in validated:
                existing = conn.execute(
                    """SELECT id, version FROM patterns
                       WHERE LOWER(task) = ? AND LOWER(app) = ? AND source = 'community'""",
                    (p.task.lower(), p.app),
                ).fetchone()

                if existing:
                    if not force_refresh and existing["version"] >= p.version:
                        skipped += 1
                        continue
                    conn.execute(
                        """UPDATE patterns
                           SET steps=?, notes=?, version=?, success_rate=?,
                               execution_count=0, updated_at=?
                           WHERE id=?""",
                        (
                            json.dumps(p.steps),
                            p.notes,
                            p.version,
                            p.success_rate,
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
                            p.task,
                            p.app,
                            p.category,
                            json.dumps(p.steps),
                            p.success_rate,
                            0,
                            "community",
                            p.version,
                            p.notes,
                            _now(),
                            _now(),
                        ),
                    )
                    imported += 1

            conn.commit()
        finally:
            conn.close()

        result: dict = {
            "status": "success",
            "community_version": data.get("version", "unknown"),
            "total_available": len(all_raw),
            "filtered_to_app": app or "all",
            "newly_imported": imported,
            "updated_to_newer_version": updated,
            "already_up_to_date": skipped,
            "tip": (
                "Call list_patterns() to browse what's available, "
                "or recall_pattern(task='...') before your next multi-step task."
            ),
        }
        if rejected:
            result["rejected_patterns"] = len(rejected)
            result["rejection_details"] = rejected
            result["security_note"] = (
                "Some patterns were rejected during schema validation. "
                "This may indicate a corrupt file or a security concern. "
                "Review rejection_details and report at "
                "https://github.com/vdalhambra/axiom-perception-mcp/issues if suspicious."
            )
        return result
