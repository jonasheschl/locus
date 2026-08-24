from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
    path TEXT PRIMARY KEY,
    space TEXT NOT NULL DEFAULT 'manual' CHECK(space IN ('manual', 'ingest', 'wiki')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    headings_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    indexed_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path UNINDEXED,
    title,
    content,
    tags,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS links (
    source_path TEXT NOT NULL,
    target TEXT NOT NULL,
    normalized_target TEXT,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (source_path, target, label),
    FOREIGN KEY (source_path) REFERENCES notes(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embeddings (
    note_path TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (note_path) REFERENCES notes(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS credentials (
    provider TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    account_id TEXT NOT NULL,
    account_label TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    context_paths_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ingest_items (
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    source_url TEXT,
    extraction_error TEXT,
    extractor_version TEXT NOT NULL DEFAULT '',
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_groups (
    folder_path TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    primary_path TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS ingest_fts USING fts5(
    path UNINDEXED,
    title,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS ingest_embeddings (
    item_path TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (item_path) REFERENCES ingest_items(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wiki_operations (
    id TEXT PRIMARY KEY,
    thread_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('ingest', 'maintain', 'manual-edit')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed', 'undone')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    undone_at TEXT,
    error TEXT,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS wiki_operation_changes (
    operation_id TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('created', 'updated')),
    before_content TEXT,
    after_content TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT NOT NULL,
    PRIMARY KEY (operation_id, path),
    FOREIGN KEY (operation_id) REFERENCES wiki_operations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wiki_operation_sources (
    operation_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    PRIMARY KEY (operation_id, source_path),
    FOREIGN KEY (operation_id) REFERENCES wiki_operations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_integrations (
    source_path TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    integrated_at TEXT NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES wiki_operations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manual_integrations (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    integrated_at TEXT NOT NULL,
    operation_id TEXT,
    FOREIGN KEY (operation_id) REFERENCES wiki_operations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS runtime_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_links_target ON links(normalized_target);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_wiki_operations_thread ON wiki_operations(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wiki_changes_path ON wiki_operation_changes(path);
CREATE INDEX IF NOT EXISTS idx_ingest_groups_created ON ingest_groups(created_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            note_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
            }
            if "space" not in note_columns:
                connection.execute(
                    "ALTER TABLE notes ADD COLUMN space TEXT NOT NULL DEFAULT 'manual'"
                )
            ingest_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(ingest_items)").fetchall()
            }
            if "extractor_version" not in ingest_columns:
                connection.execute(
                    "ALTER TABLE ingest_items ADD COLUMN extractor_version TEXT NOT NULL DEFAULT ''"
                )
            message_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(chat_messages)").fetchall()
            }
            if "context_paths_json" not in message_columns:
                connection.execute(
                    "ALTER TABLE chat_messages ADD COLUMN context_paths_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute(
                """
                UPDATE notes SET space = CASE
                    WHEN lower(path) LIKE 'ingest/%' THEN 'ingest'
                    WHEN lower(path) LIKE 'wiki/%' THEN 'wiki'
                    ELSE 'manual'
                END
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_notes_space ON notes(space)")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self.connect() as connection:
            yield connection

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    @staticmethod
    def decode_note(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["headings"] = json.loads(result.pop("headings_json", "[]"))
        result["tags"] = json.loads(result.pop("tags_json", "[]"))
        return result
