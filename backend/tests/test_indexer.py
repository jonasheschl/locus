from pathlib import Path

from app.database import Database
from app.indexer import NoteIndexer, extract_excerpt, extract_links, hash_embedding
from app.ingest import IngestIndexer


def build_index(tmp_path: Path) -> tuple[Database, NoteIndexer]:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    indexer = NoteIndexer(tmp_path, database)
    return database, indexer


def test_indexes_markdown_and_resolves_links(tmp_path: Path) -> None:
    (tmp_path / "manual" / "Research").mkdir(parents=True)
    (tmp_path / "manual" / "Research" / "Trust.md").write_text(
        "# Trust in agents\n\nCan agentic systems carry load-bearing trust? #safety\n",
        encoding="utf-8",
    )
    (tmp_path / "manual" / "Overview.md").write_text(
        "# Research overview\n\nSee [[Research/Trust|the trust note]].\n",
        encoding="utf-8",
    )
    database, indexer = build_index(tmp_path)

    result = indexer.scan()

    assert result == {"indexed": 2, "removed": 0, "total": 2}
    note = database.decode_note(
        database.fetch_one("SELECT * FROM notes WHERE path = ?", ("manual/Research/Trust.md",))
    )
    assert note["title"] == "Trust in agents"
    assert note["space"] == "manual"
    assert note["tags"] == ["safety"]
    assert note["headings"][0]["slug"] == "trust-in-agents"
    link = database.fetch_one("SELECT * FROM links WHERE source_path = ?", ("manual/Overview.md",))
    assert link["normalized_target"] == "manual/Research/Trust.md"


def test_hybrid_search_and_removal(tmp_path: Path) -> None:
    (tmp_path / "manual").mkdir()
    note = tmp_path / "manual" / "Markets.md"
    note.write_text("# Zero-day markets\n\nBrokers and vulnerability clearing houses.", encoding="utf-8")
    database, indexer = build_index(tmp_path)
    indexer.scan()

    results = indexer.search("vulnerability broker")
    assert results[0]["path"] == "manual/Markets.md"
    assert results[0]["score"] > 0

    note.unlink()
    result = indexer.scan()
    assert result["removed"] == 1
    assert database.fetch_one("SELECT path FROM notes") is None


def test_hash_embeddings_are_normalized() -> None:
    vector = hash_embedding("Trust systems trust agents")
    magnitude = sum(value * value for value in vector) ** 0.5
    assert len(vector) == 256
    assert abs(magnitude - 1.0) < 1e-6


def test_external_link_is_not_resolved() -> None:
    links = extract_links(
        "A.md",
        "[OpenAI](https://openai.com) and [[B]]",
        {"A.md", "B.md"},
    )
    by_kind = {link["kind"]: link for link in links}
    assert by_kind["external"]["normalized_target"] is None
    assert by_kind["wiki"]["normalized_target"] == "B.md"


def test_excerpt_flattens_wiki_links() -> None:
    excerpt = extract_excerpt(
        "# Topic\n\nGrounded in [[ingest/source-file.html|the full source]]."
    )

    assert excerpt == "Grounded in the full source."


def test_resolves_links_to_non_markdown_ingest_assets(tmp_path: Path) -> None:
    (tmp_path / "ingest").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "ingest" / "source.txt").write_text("Source material", encoding="utf-8")
    (tmp_path / "wiki" / "Topic.md").write_text(
        "# Topic\n\nSee [[ingest/source.txt|source]].\n", encoding="utf-8"
    )
    database, indexer = build_index(tmp_path)
    IngestIndexer(tmp_path, database).scan()

    indexer.scan()

    link = database.fetch_one(
        "SELECT normalized_target FROM links WHERE source_path = ?",
        ("wiki/Topic.md",),
    )
    assert link == {"normalized_target": "ingest/source.txt"}


def test_indexes_only_explicit_knowledge_spaces(tmp_path: Path) -> None:
    (tmp_path / "manual").mkdir()
    (tmp_path / "ingest").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "ingest" / "Source.md").write_text("# Raw source", encoding="utf-8")
    (tmp_path / "wiki" / "Topic.md").write_text("# Synthesized topic", encoding="utf-8")
    (tmp_path / "manual" / "My original.md").write_text("# Original", encoding="utf-8")
    (tmp_path / "application.md").write_text("# Not a note", encoding="utf-8")
    database, indexer = build_index(tmp_path)

    indexer.scan()
    rows = database.fetch_all("SELECT path, space FROM notes ORDER BY path")

    assert {row["path"]: row["space"] for row in rows} == {
        "manual/My original.md": "manual",
        "ingest/Source.md": "ingest",
        "wiki/Topic.md": "wiki",
    }
