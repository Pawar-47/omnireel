"""SQLite persistence layer.

Threading model: the FastAPI request handlers run in a thread pool and the
generation worker runs on the event loop, so every operation opens a short
lived connection and writes are serialised with a process-wide lock. WAL mode
keeps readers from blocking the worker.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    title             TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    style             TEXT NOT NULL DEFAULT 'cinematic',
    aspect_ratio      TEXT NOT NULL DEFAULT '16:9',
    resolution        TEXT NOT NULL DEFAULT '720p',
    target_seconds    REAL NOT NULL DEFAULT 120,
    mode              TEXT NOT NULL DEFAULT 'free_max',
    free_only         INTEGER NOT NULL DEFAULT 1,
    preferred_provider TEXT,
    status            TEXT NOT NULL DEFAULT 'planned',
    world_bible       TEXT NOT NULL DEFAULT '{}',
    settings          TEXT NOT NULL DEFAULT '{}',
    final_video_path  TEXT,
    final_duration    REAL,
    status_message    TEXT,
    next_retry_at     TEXT
);

CREATE TABLE IF NOT EXISTS scenes (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    index_no           INTEGER NOT NULL,
    duration_seconds   REAL NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    prompt             TEXT NOT NULL DEFAULT '',
    camera             TEXT NOT NULL DEFAULT '',
    subject_motion     TEXT NOT NULL DEFAULT '',
    lighting           TEXT NOT NULL DEFAULT '',
    environment        TEXT NOT NULL DEFAULT '',
    continuity         TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'planned',
    provider           TEXT,
    model              TEXT,
    actual_duration    REAL,
    clip_path          TEXT,
    remote_job_id      TEXT,
    remote_url         TEXT,
    reference_image_path TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    updated_at         TEXT NOT NULL,
    UNIQUE(project_id, index_no)
);

CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id, index_no);
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);

CREATE TABLE IF NOT EXISTS scene_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    scene_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    error       TEXT,
    remote_job_id TEXT,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_recent ON scene_attempts(started_at);

CREATE TABLE IF NOT EXISTS provider_state (
    provider         TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    cooldown_until   TEXT,
    last_error       TEXT,
    last_success_at  TEXT,
    last_checked_at  TEXT,
    disabled         INTEGER NOT NULL DEFAULT 0,
    disabled_reason  TEXT
);

CREATE TABLE IF NOT EXISTS quotas (
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    key_id         TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'recurring',
    limit_total    INTEGER,
    used_total     INTEGER NOT NULL DEFAULT 0,
    limit_window   INTEGER,
    window_seconds INTEGER NOT NULL DEFAULT 86400,
    window_start   TEXT,
    used_window    INTEGER NOT NULL DEFAULT 0,
    min_interval_seconds REAL NOT NULL DEFAULT 0,
    next_eligible_at TEXT,
    promo_expires_at TEXT,
    source         TEXT NOT NULL DEFAULT 'estimated',
    last_success_at TEXT,
    last_error     TEXT,
    last_error_kind TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (provider, model, key_id)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    provider  TEXT NOT NULL,
    model     TEXT NOT NULL,
    key_id    TEXT NOT NULL,
    project_id TEXT,
    scene_id  TEXT,
    kind      TEXT NOT NULL,
    duration_seconds REAL,
    note      TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider, at);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    scene_id      TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    key_id        TEXT NOT NULL,
    remote_job_id TEXT,
    status        TEXT NOT NULL,
    polls         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_poll_at  TEXT,
    error         TEXT,
    payload       TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, status);
"""


class Database:
    """Thin synchronous SQLite wrapper with JSON helpers."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- connections -----------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=30.0, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction."""
        with self._lock:
            with closing(self.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")

    def init(self) -> None:
        with self._lock:
            with closing(self.connect()) as connection:
                connection.executescript(SCHEMA_SQL)
                connection.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),),
                )

    # -- reads -----------------------------------------------------------
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            with closing(self.connect()) as connection:
                rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        if not row:
            return default
        return next(iter(row.values()), default)

    # -- writes ----------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.tx() as connection:
            cursor = connection.execute(sql, tuple(params))
            return cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount

    def execute_returning_rowcount(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.tx() as connection:
            cursor = connection.execute(sql, tuple(params))
            return cursor.rowcount


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {} if default is None else default
