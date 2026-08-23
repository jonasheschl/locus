from pathlib import Path

from app.database import Database
from app.ingest import IngestIndexer


def test_indexes_and_searches_non_markdown_ingest(tmp_path: Path) -> None:
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    source = ingest / "field-report.txt"
    source.write_text(
        "A field report about load-bearing trust in autonomous agents.", encoding="utf-8"
    )
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    indexer = IngestIndexer(tmp_path, database)

    result = indexer.scan()
    matches = indexer.search("autonomous trust")

    assert result == {"indexed": 1, "removed": 0, "total": 1}
    assert matches[0]["path"] == "ingest/field-report.txt"
    assert matches[0]["space"] == "ingest"
    assert database.fetch_one("SELECT source_type FROM ingest_items")["source_type"] == "text"

    source.unlink()
    assert indexer.scan()["removed"] == 1
    assert database.fetch_one("SELECT path FROM ingest_items") is None
