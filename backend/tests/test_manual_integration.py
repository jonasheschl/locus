from pathlib import Path

import pytest

from app.database import Database
from app.indexer import NoteIndexer
from app.main import integrate_pending_manual_changes
from app.manual_integration import ManualIntegrationTracker


def integration_tracker(
    tmp_path: Path,
) -> tuple[Database, NoteIndexer, ManualIntegrationTracker]:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    indexer = NoteIndexer(tmp_path, database)
    return database, indexer, ManualIntegrationTracker(database)


def test_tracker_detects_created_modified_and_deleted_manual_notes(
    tmp_path: Path,
) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    note = manual / "Ideas.md"
    note.write_text("# Ideas\n\nFirst snapshot.\n", encoding="utf-8")
    database, indexer, tracker = integration_tracker(tmp_path)
    indexer.scan()

    created = tracker.pending()
    assert [(item["path"], item["change"]) for item in created] == [
        ("manual/Ideas.md", "created")
    ]
    tracker.mark_completed(created, None)
    assert tracker.pending() == []

    note.write_text("# Ideas\n\nSecond snapshot with more detail.\n", encoding="utf-8")
    indexer.scan()
    modified = tracker.pending()
    assert [(item["path"], item["change"]) for item in modified] == [
        ("manual/Ideas.md", "modified")
    ]
    assert modified[0]["content_hash"] != created[0]["content_hash"]
    tracker.mark_completed(modified, None)

    note.unlink()
    indexer.scan()
    deleted = tracker.pending()
    assert [(item["path"], item["change"]) for item in deleted] == [
        ("manual/Ideas.md", "deleted")
    ]
    tracker.mark_completed(deleted, None)
    assert tracker.pending() == []
    assert database.fetch_one("SELECT path FROM manual_integrations") is None


@pytest.mark.asyncio
async def test_pending_manual_changes_are_marked_only_after_agent_success(
    tmp_path: Path,
) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    (manual / "Research.md").write_text("# Research\n\nA durable claim.\n")
    _, indexer, tracker = integration_tracker(tmp_path)

    class SuccessfulAgent:
        calls: list[list[dict]] = []

        async def integrate_manual_changes(self, changes):
            self.calls.append(changes)
            return {"operation_id": None, "summary": "No Wiki write needed."}

    agent = SuccessfulAgent()
    result = await integrate_pending_manual_changes(indexer, tracker, agent)

    assert result == {"status": "completed", "changes": 1, "operation_id": None}
    assert agent.calls[0][0]["path"] == "manual/Research.md"
    assert tracker.pending() == []

    (manual / "Research.md").write_text("# Research\n\nA changed claim.\n")

    class FailingAgent:
        async def integrate_manual_changes(self, changes):
            raise RuntimeError("temporary model failure")

    with pytest.raises(RuntimeError, match="temporary model failure"):
        await integrate_pending_manual_changes(indexer, tracker, FailingAgent())
    assert tracker.pending()[0]["change"] == "modified"
