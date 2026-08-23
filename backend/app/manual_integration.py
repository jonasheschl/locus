from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .database import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ManualIntegrationTracker:
    """Persist the exact Manual snapshots successfully considered by the agent."""

    def __init__(self, database: Database):
        self.database = database

    def pending(self) -> list[dict[str, Any]]:
        current = {
            row["path"]: row
            for row in self.database.fetch_all(
                """
                SELECT path, title, content_hash, indexed_at
                FROM notes WHERE space='manual'
                ORDER BY path COLLATE NOCASE
                """
            )
        }
        integrated = {
            row["path"]: row
            for row in self.database.fetch_all(
                "SELECT path, content_hash FROM manual_integrations"
            )
        }
        changes: list[dict[str, Any]] = []
        for path, note in current.items():
            previous = integrated.get(path)
            if previous and previous["content_hash"] == note["content_hash"]:
                continue
            changes.append(
                {
                    "path": path,
                    "title": note["title"],
                    "content_hash": note["content_hash"],
                    "change": "modified" if previous else "created",
                }
            )
        for path in sorted(set(integrated) - set(current), key=str.casefold):
            changes.append(
                {
                    "path": path,
                    "title": path.rsplit("/", 1)[-1],
                    "content_hash": None,
                    "change": "deleted",
                }
            )
        return changes

    def mark_completed(
        self, changes: list[dict[str, Any]], operation_id: str | None
    ) -> None:
        now = utc_now()
        with self.database.write() as connection:
            for change in changes:
                if change["change"] == "deleted":
                    connection.execute(
                        "DELETE FROM manual_integrations WHERE path = ?",
                        (change["path"],),
                    )
                    continue
                connection.execute(
                    """
                    INSERT INTO manual_integrations(path, content_hash, integrated_at, operation_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        content_hash=excluded.content_hash,
                        integrated_at=excluded.integrated_at,
                        operation_id=excluded.operation_id
                    """,
                    (
                        change["path"],
                        change["content_hash"],
                        now,
                        operation_id,
                    ),
                )

    def status(self) -> dict[str, Any]:
        last = self.database.fetch_one(
            "SELECT MAX(integrated_at) AS integrated_at FROM manual_integrations"
        ) or {"integrated_at": None}
        return {
            "pending": len(self.pending()),
            "tracked": (
                self.database.fetch_one(
                    "SELECT COUNT(*) AS count FROM manual_integrations"
                )
                or {"count": 0}
            )["count"],
            "last_integrated_at": last["integrated_at"],
        }
