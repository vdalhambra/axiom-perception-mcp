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
                id          TEXT PRIMARY KEY,
                task        TEXT NOT NULL,
                app         TEXT NOT NULL DEFAULT 'generic',
                category    TEXT NOT NULL DEFAULT 'general',
                steps       TEXT NOT NULL,
                success_rate REAL NOT NULL DEFAULT 1.0,
                execution_count INTEGER NOT NULL DEFAULT 0,
                avg_time_ms INTEGER NOT NULL DEFAULT 0,
                source      TEXT NOT NULL DEFAULT 'local',
                version     INTEGER NOT NULL DEFAULT 1,
                notes       TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id          TEXT PRIMARY KEY,
                pattern_id  TEXT NOT NULL,
                success     INTEGER NOT NULL,
                time_ms     INTEGER,
                error       TEXT,
                timestamp   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_patterns_app ON patterns(app);
            CREATE INDEX IF NOT EXISTS idx_patterns_task ON patterns(task);
            CREATE INDEX IF NOT EXISTS idx_executions_pattern ON executions(pattern_id);
        """)
        conn.commit()
    finally:
        conn.close()
