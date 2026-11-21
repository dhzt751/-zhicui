import json
import sqlite3
from datetime import datetime
from typing import Any, Iterable

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT DEFAULT '',
    url TEXT DEFAULT '',
    external_id TEXT DEFAULT '',
    play_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    author TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    cover TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    stage TEXT DEFAULT '',
    transcript_source TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    idx INTEGER DEFAULT 0,
    start_ms INTEGER DEFAULT 0,
    end_ms INTEGER DEFAULT 0,
    text TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id);
CREATE TABLE IF NOT EXISTS knowledge_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    start_ms INTEGER DEFAULT 0,
    end_ms INTEGER DEFAULT 0,
    keywords TEXT DEFAULT '[]',
    difficulty TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    evidence TEXT DEFAULT '',
    is_noise INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_kp_video ON knowledge_points(video_id);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    stage TEXT DEFAULT '',
    text TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    size INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clusters_doc ON clusters(document_id);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    cluster_id INTEGER,
    number INTEGER DEFAULT 0,
    stem TEXT DEFAULT '',
    options TEXT DEFAULT '[]',
    answer TEXT DEFAULT '',
    analysis TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_doc ON questions(document_id);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT '',
    ref_type TEXT DEFAULT '',
    ref_id INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    stage TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
"""

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
        if "transcript_source" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN transcript_source TEXT DEFAULT ''")
        kp_columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_points)")}
        for name, ddl in (
            ("kind", "TEXT DEFAULT ''"),
            ("confidence", "REAL DEFAULT 0"),
            ("evidence", "TEXT DEFAULT ''"),
        ):
            if name not in kp_columns:
                conn.execute(f"ALTER TABLE knowledge_points ADD COLUMN {name} {ddl}")
        conn.commit()
    finally:
        conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [_decode(dict(row)) for row in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = connect()
    try:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _decode(row: dict) -> dict:
    for field in ("options", "tags", "keywords"):
        if field in row and isinstance(row[field], str):
            try:
                row[field] = json.loads(row[field])
            except Exception:
                row[field] = []
    return row


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def update_job(job_id: int, **fields: Any) -> None:
    fields["updated_at"] = now()
    keys = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {keys} WHERE id=?", [*fields.values(), job_id])


def job_payload(job: dict | None) -> dict | None:
    return job