from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .indexer import NoteIndexer, extract_excerpt


INDEX_TEMPLATE = """# Wiki Index

> Maintained by Locus after every Wiki-changing operation.

_No synthesized Wiki pages yet._
"""

LOG_TEMPLATE = """# Wiki Log

Append-only history of operations that changed or checked the Wiki.
"""

SPECIAL_WIKI_FILES = {"wiki/index.md", "wiki/log.md"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def content_hash(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_wiki_contract(workspace: Path) -> None:
    (workspace / "manual").mkdir(parents=True, exist_ok=True)
    (workspace / "ingest").mkdir(parents=True, exist_ok=True)
    wiki = workspace / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    for path, content in (
        (wiki / "index.md", INDEX_TEMPLATE),
        (wiki / "log.md", LOG_TEMPLATE),
    ):
        if not path.exists():
            atomic_write(path, content)


class OperationConflict(RuntimeError):
    pass


class OperationManager:
    def __init__(self, workspace: Path, database: Database, indexer: NoteIndexer):
        self.workspace = workspace
        self.database = database
        self.indexer = indexer

    def start(self, kind: str, title: str, thread_id: str | None) -> str:
        operation_id = str(uuid.uuid4())
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO wiki_operations(id, thread_id, kind, title, status, created_at)
                VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (operation_id, thread_id, kind, title[:180] or kind.title(), utc_now()),
            )
        return operation_id

    def record_source(self, operation_id: str | None, path: str) -> None:
        if not operation_id:
            return
        with self.database.write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO wiki_operation_sources(operation_id, source_path) VALUES (?, ?)",
                (operation_id, path),
            )

    def write_wiki(self, operation_id: str, path: str, content: str) -> dict[str, str]:
        target = self._wiki_path(path)
        relative = target.relative_to(self.workspace).as_posix()
        if relative in SPECIAL_WIKI_FILES:
            raise ValueError("wiki/index.md and wiki/log.md are maintained by the application")
        return self._write(operation_id, target, content)

    def write_manual(self, operation_id: str, path: str, content: str) -> dict[str, str]:
        note = self.database.fetch_one(
            "SELECT path, space FROM notes WHERE path = ?", (path,)
        )
        if not note or note["space"] != "manual":
            raise ValueError("Existing Manual note not found")
        target = (self.workspace / path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as error:
            raise ValueError("Manual path escapes the workspace") from error
        return self._write(operation_id, target, content)

    def _write(self, operation_id: str, target: Path, content: str) -> dict[str, str]:
        if len(content) > 2_000_000:
            raise ValueError("Markdown content exceeds the two MB limit")
        relative = target.relative_to(self.workspace).as_posix()
        before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        existing = self.database.fetch_one(
            "SELECT before_content FROM wiki_operation_changes WHERE operation_id = ? AND path = ?",
            (operation_id, relative),
        )
        original = existing["before_content"] if existing else before
        atomic_write(target, content)
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO wiki_operation_changes(
                    operation_id, path, action, before_content, after_content,
                    before_hash, after_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id, path) DO UPDATE SET
                    after_content=excluded.after_content, after_hash=excluded.after_hash
                """,
                (
                    operation_id,
                    relative,
                    "created" if original is None else "updated",
                    original,
                    content,
                    content_hash(original),
                    content_hash(content),
                ),
            )
        return {"status": "written", "path": relative}

    def complete(self, operation_id: str, summary: str) -> dict[str, Any]:
        self.indexer.scan()
        wiki_changes = self.database.fetch_one(
            """
            SELECT COUNT(*) AS count FROM wiki_operation_changes
            WHERE operation_id = ? AND path LIKE 'wiki/%'
              AND path NOT IN ('wiki/index.md', 'wiki/log.md')
            """,
            (operation_id,),
        ) or {"count": 0}
        if wiki_changes["count"]:
            self._rebuild_index(operation_id)
            self.indexer.scan()
        operation = self.database.fetch_one(
            "SELECT kind, title FROM wiki_operations WHERE id = ?", (operation_id,)
        )
        if not operation:
            raise KeyError("Operation not found")
        now = utc_now()
        sources = self.database.fetch_all(
            "SELECT source_path FROM wiki_operation_sources WHERE operation_id = ?",
            (operation_id,),
        )
        self._append_log(operation["kind"], operation["title"], operation_id)
        self.indexer.scan()
        with self.database.write() as connection:
            if operation["kind"] == "ingest" and wiki_changes["count"]:
                for source in sources:
                    path = source["source_path"]
                    if path.casefold().startswith("ingest/"):
                        connection.execute(
                            """
                            INSERT INTO source_integrations(source_path, operation_id, integrated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(source_path) DO UPDATE SET
                                operation_id=excluded.operation_id,
                                integrated_at=excluded.integrated_at
                            """,
                            (path, operation_id, now),
                        )
            connection.execute(
                """
                UPDATE wiki_operations SET status='completed', summary=?, completed_at=?
                WHERE id=?
                """,
                (summary[:20_000], now, operation_id),
            )
        return self.get(operation_id)

    def fail(self, operation_id: str, error: str) -> None:
        rollback_note = ""
        try:
            self._restore_changes(operation_id)
            self.indexer.scan()
        except OperationConflict as conflict:
            rollback_note = f" Rollback warning: {conflict}"
        with self.database.write() as connection:
            connection.execute(
                "DELETE FROM source_integrations WHERE operation_id = ?", (operation_id,)
            )
            connection.execute(
                "UPDATE wiki_operations SET status='failed', error=?, completed_at=? WHERE id=?",
                ((error + rollback_note)[:2_000], utc_now(), operation_id),
            )

    def undo(self, operation_id: str) -> dict[str, Any]:
        operation = self.database.fetch_one(
            "SELECT * FROM wiki_operations WHERE id = ?", (operation_id,)
        )
        if not operation:
            raise KeyError("Operation not found")
        if operation["status"] != "completed":
            raise OperationConflict("Only a completed operation can be undone")
        self._restore_changes(operation_id)
        with self.database.write() as connection:
            connection.execute(
                "UPDATE wiki_operations SET status='undone', undone_at=? WHERE id=?",
                (utc_now(), operation_id),
            )
            connection.execute(
                "DELETE FROM source_integrations WHERE operation_id = ?", (operation_id,)
            )
        self._append_log("undo", operation["title"], operation_id)
        self.indexer.scan()
        return self.get(operation_id)

    def get(self, operation_id: str) -> dict[str, Any]:
        operation = self.database.fetch_one(
            "SELECT * FROM wiki_operations WHERE id = ?", (operation_id,)
        )
        if not operation:
            raise KeyError("Operation not found")
        changes = self.database.fetch_all(
            """
            SELECT path, action FROM wiki_operation_changes
            WHERE operation_id = ? ORDER BY path
            """,
            (operation_id,),
        )
        sources = self.database.fetch_all(
            """
            SELECT source_path AS path FROM wiki_operation_sources
            WHERE operation_id = ? ORDER BY source_path
            """,
            (operation_id,),
        )
        return {**operation, "changes": changes, "sources": sources}

    def for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT id FROM wiki_operations WHERE thread_id = ? ORDER BY created_at",
            (thread_id,),
        )
        return [self.get(row["id"]) for row in rows]

    def file_history(self, path: str) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            """
            SELECT o.id, o.kind, o.title, o.status, o.summary, o.created_at, c.action
            FROM wiki_operation_changes c JOIN wiki_operations o ON o.id = c.operation_id
            WHERE c.path = ? ORDER BY o.created_at DESC LIMIT 20
            """,
            (path,),
        )

    def lint(self) -> dict[str, Any]:
        pages = self.database.fetch_all(
            """
            SELECT path, title, content FROM notes
            WHERE space='wiki' AND path NOT IN ('wiki/index.md', 'wiki/log.md')
            ORDER BY path
            """
        )
        index = self.database.fetch_one(
            "SELECT content FROM notes WHERE path='wiki/index.md'"
        ) or {"content": ""}
        incoming = {
            row["normalized_target"]
            for row in self.database.fetch_all(
                "SELECT DISTINCT normalized_target FROM links WHERE normalized_target IS NOT NULL"
            )
        }
        broken = self.database.fetch_all(
            """
            SELECT source_path, target FROM links
            WHERE source_path LIKE 'wiki/%' AND normalized_target IS NULL
              AND kind IN ('wiki', 'markdown')
            ORDER BY source_path, target
            """
        )
        source_links = {
            row["source_path"]
            for row in self.database.fetch_all(
                """
                SELECT DISTINCT l.source_path FROM links l
                LEFT JOIN notes n ON n.path = l.normalized_target
                WHERE l.source_path LIKE 'wiki/%'
                  AND (l.target LIKE 'ingest/%' OR n.space='manual' OR n.space='ingest')
                """
            )
        }
        ingest_paths = {
            row["path"]
            for row in self.database.fetch_all(
                "SELECT path FROM notes WHERE space='ingest'"
            )
        } | {
            row["path"] for row in self.database.fetch_all("SELECT path FROM ingest_items")
        }
        integrated = {
            row["source_path"]
            for row in self.database.fetch_all("SELECT source_path FROM source_integrations")
        }
        return {
            "pages": len(pages),
            "orphans": [page["path"] for page in pages if page["path"] not in incoming],
            "broken_links": broken,
            "missing_from_index": [
                page["path"] for page in pages if page["path"] not in index["content"]
            ],
            "missing_source_links": [
                page["path"] for page in pages if page["path"] not in source_links
            ],
            "unprocessed_sources": sorted(ingest_paths - integrated),
        }

    def _rebuild_index(self, operation_id: str) -> None:
        pages = self.database.fetch_all(
            """
            SELECT path, title, content FROM notes
            WHERE space='wiki' AND path NOT IN ('wiki/index.md', 'wiki/log.md')
            ORDER BY path COLLATE NOCASE
            """
        )
        lines = [
            "# Wiki Index",
            "",
            "> Maintained by Locus after every Wiki-changing operation.",
            "",
            f"Last compiled: {utc_now()}",
            "",
        ]
        if pages:
            lines.append("## Pages")
            lines.append("")
            for page in pages:
                description = extract_excerpt(page["content"]) or "Synthesized Wiki page."
                lines.append(f"- [[{page['path']}|{page['title']}]] — {description}")
        else:
            lines.append("_No synthesized Wiki pages yet._")
        lines.append("")
        self._write(operation_id, self.workspace / "wiki" / "index.md", "\n".join(lines))

    def _append_log(self, kind: str, title: str, operation_id: str) -> None:
        path = self.workspace / "wiki" / "log.md"
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else LOG_TEMPLATE
        date = datetime.now(UTC).date().isoformat()
        entry = f"\n## [{date}] {kind} | {title}\n\nOperation: `{operation_id}`\n"
        atomic_write(path, current.rstrip() + "\n" + entry)

    def _restore_changes(self, operation_id: str) -> None:
        changes = self.database.fetch_all(
            "SELECT * FROM wiki_operation_changes WHERE operation_id = ? ORDER BY path DESC",
            (operation_id,),
        )
        for change in changes:
            target = (self.workspace / change["path"]).resolve()
            current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
            if content_hash(current) != change["after_hash"]:
                raise OperationConflict(
                    f"{change['path']} changed after this operation; rollback would overwrite newer work"
                )
        for change in changes:
            target = (self.workspace / change["path"]).resolve()
            if change["before_content"] is None:
                if target.exists():
                    target.unlink()
            else:
                atomic_write(target, change["before_content"])

    def _wiki_path(self, path: str) -> Path:
        raw = path.replace("\\", "/").strip("/")
        if raw.casefold().startswith("wiki/"):
            raw = raw.split("/", 1)[1]
        if not raw.casefold().endswith(".md"):
            raw += ".md"
        target = (self.workspace / "wiki" / raw).resolve()
        root = (self.workspace / "wiki").resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("Wiki path must stay inside wiki/") from error
        return target
