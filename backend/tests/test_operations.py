from pathlib import Path

import pytest

from app.database import Database
from app.indexer import NoteIndexer
from app.operations import OperationConflict, OperationManager, ensure_wiki_contract


def operation_manager(tmp_path: Path) -> tuple[Database, NoteIndexer, OperationManager]:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    ensure_wiki_contract(tmp_path)
    indexer = NoteIndexer(tmp_path, database)
    indexer.scan()
    return database, indexer, OperationManager(tmp_path, database, indexer)


def test_ingest_operation_tracks_sources_rebuilds_index_and_undoes(tmp_path: Path) -> None:
    source = tmp_path / "ingest" / "paper.md"
    source.parent.mkdir()
    source.write_text("# Paper\n\nA claim about trustworthy agents.\n", encoding="utf-8")
    database, indexer, operations = operation_manager(tmp_path)
    indexer.scan()

    operation_id = operations.start("ingest", "Integrate paper", None)
    operations.record_source(operation_id, "ingest/paper.md")
    operations.write_wiki(
        operation_id,
        "concepts/trust.md",
        "# Agent trust\n\nGrounded in [[ingest/paper.md|the paper]].\n",
    )
    receipt = operations.complete(operation_id, "Added a durable concept page.")

    assert receipt["status"] == "completed"
    assert receipt["sources"] == [{"path": "ingest/paper.md"}]
    assert {change["path"] for change in receipt["changes"]} == {
        "wiki/concepts/trust.md",
        "wiki/index.md",
    }
    assert "[[wiki/concepts/trust.md|Agent trust]]" in (
        tmp_path / "wiki" / "index.md"
    ).read_text(encoding="utf-8")
    assert "Integrate paper" in (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert database.fetch_one(
        "SELECT operation_id FROM source_integrations WHERE source_path=?",
        ("ingest/paper.md",),
    ) == {"operation_id": operation_id}

    undone = operations.undo(operation_id)

    assert undone["status"] == "undone"
    assert not (tmp_path / "wiki" / "concepts" / "trust.md").exists()
    assert "_No synthesized Wiki pages yet._" in (
        tmp_path / "wiki" / "index.md"
    ).read_text(encoding="utf-8")
    assert database.fetch_one(
        "SELECT operation_id FROM source_integrations WHERE source_path=?",
        ("ingest/paper.md",),
    ) is None
    assert "undo | Integrate paper" in (
        tmp_path / "wiki" / "log.md"
    ).read_text(encoding="utf-8")


def test_undo_refuses_to_overwrite_newer_changes(tmp_path: Path) -> None:
    _, _, operations = operation_manager(tmp_path)
    operation_id = operations.start("maintain", "Create glossary", None)
    operations.write_wiki(operation_id, "glossary.md", "# Glossary\n")
    operations.complete(operation_id, "Created a glossary.")
    (tmp_path / "wiki" / "glossary.md").write_text(
        "# Glossary\n\nNewer human edit.\n", encoding="utf-8"
    )

    with pytest.raises(OperationConflict, match="newer work"):
        operations.undo(operation_id)

    assert "Newer human edit" in (tmp_path / "wiki" / "glossary.md").read_text(
        encoding="utf-8"
    )


def test_special_wiki_files_are_not_agent_writable(tmp_path: Path) -> None:
    _, _, operations = operation_manager(tmp_path)
    operation_id = operations.start("maintain", "Unsafe special edit", None)

    with pytest.raises(ValueError, match="maintained by the application"):
        operations.write_wiki(operation_id, "index.md", "# Replaced")
