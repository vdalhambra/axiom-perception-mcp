"""SQLite persistence layer for axiom-perception-mcp."""

import os
import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".axiom" / "perception"
DB_PATH = DB_DIR / "patterns.db"


def get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    # Restrict directory: only owner can read/write/execute
    os.chmod(DB_DIR, 0o700)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Restrict DB file: only owner can read/write
    if DB_PATH.exists():
        os.chmod(DB_PATH, 0o600)
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS patterns (
                id              TEXT PRIMARY KEY,
                task            TEXT NOT NULL,
                app             TEXT NOT NULL DEFAULT 'generic',
                category        TEXT NOT NULL DEFAULT 'general',
                steps           TEXT NOT NULL,
                success_rate    REAL NOT NULL DEFAULT 1.0,
                execution_count INTEGER NOT NULL DEFAULT 0,
                avg_time_ms     INTEGER NOT NULL DEFAULT 0,
                source          TEXT NOT NULL DEFAULT 'local',
                version         INTEGER NOT NULL DEFAULT 1,
                notes           TEXT,
                context_hints   TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id          TEXT PRIMARY KEY,
                pattern_id  TEXT NOT NULL,
                success     INTEGER NOT NULL,
                time_ms     INTEGER,
                error       TEXT,
                approach    TEXT,
                timestamp   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id           TEXT PRIMARY KEY,
                workflow     TEXT NOT NULL,
                total_steps  INTEGER NOT NULL DEFAULT 0,
                current_step INTEGER NOT NULL DEFAULT 0,
                context      TEXT,
                variables    TEXT,
                status       TEXT NOT NULL DEFAULT 'in_progress',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS shared_notes (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                agent_id   TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_progress (
                id         TEXT PRIMARY KEY,
                agent_id   TEXT NOT NULL,
                task       TEXT NOT NULL,
                step       TEXT NOT NULL,
                result     TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_patterns_app ON patterns(app);
            CREATE INDEX IF NOT EXISTS idx_patterns_task ON patterns(task);
            CREATE INDEX IF NOT EXISTS idx_executions_pattern ON executions(pattern_id);
            CREATE INDEX IF NOT EXISTS idx_checkpoints_workflow ON checkpoints(workflow);
            CREATE INDEX IF NOT EXISTS idx_checkpoints_status ON checkpoints(status);
            CREATE INDEX IF NOT EXISTS idx_agent_progress_task ON agent_progress(task);
            CREATE INDEX IF NOT EXISTS idx_agent_progress_agent ON agent_progress(agent_id);
        """)
        conn.commit()
        _run_migrations(conn)
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables. Safe to call on every startup (idempotent)."""
    migrations = [
        "ALTER TABLE patterns ADD COLUMN context_hints TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE executions ADD COLUMN approach TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            # Column already exists — skip silently
            pass
