from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from docling.backend.html_backend import HTMLDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling.datamodel.settings import DocumentLimits
from pypdf import PdfReader

from .database import Database
from .indexer import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    WORD_RE,
    cosine_similarity,
    extract_excerpt,
    hash_embedding,
    pack_embedding,
    unpack_embedding,
    utc_now,
)


SUPPORTED_INGEST_EXTENSIONS = {".pdf", ".txt", ".html", ".htm", ".csv", ".json"}
SOURCE_TYPES = {
    ".pdf": "pdf",
    ".html": "website-export",
    ".htm": "website-export",
    ".txt": "text",
    ".csv": "data",
    ".json": "data",
}
MAX_EXTRACTED_CHARACTERS = 2_000_000
MAX_DOWNLOAD_BYTES = 50_000_000
HTML_EXTRACTOR_VERSION = "docling-html-2.118.0"
PDF_EXTRACTOR_VERSION = "pypdf-6.15.0"
PLAIN_EXTRACTOR_VERSION = "plain-v1"


def extractor_version(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".html", ".htm"}:
        return HTML_EXTRACTOR_VERSION
    if suffix == ".pdf":
        return PDF_EXTRACTOR_VERSION
    return PLAIN_EXTRACTOR_VERSION


def extract_html_markdown(path: Path) -> str:
    source = InputDocument(
        path_or_stream=path,
        format=InputFormat.HTML,
        backend=HTMLDocumentBackend,
        limits=DocumentLimits(max_file_size=MAX_DOWNLOAD_BYTES),
    )
    if not source.valid:
        raise ValueError("Docling could not read the HTML document")
    backend = source._backend
    try:
        markdown = backend.convert().export_to_markdown().replace("\x00", "").strip()
    finally:
        backend.unload()
    if not markdown:
        raise ValueError("Docling produced empty Markdown")
    return markdown


def extract_ingest_text(path: Path) -> tuple[str, str | None]:
    try:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            reader = PdfReader(path)
            content = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix in {".html", ".htm"}:
            content = extract_html_markdown(path)
        else:
            content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_EXTRACTED_CHARACTERS:
            content = content[:MAX_EXTRACTED_CHARACTERS]
            return content, "Extracted text was truncated at two million characters."
        return content, None
    except Exception as error:
        return "", f"Text extraction failed: {type(error).__name__}"


class IngestIndexer:
    def __init__(self, workspace: Path, database: Database):
        self.workspace = workspace
        self.root = workspace / "ingest"
        self.database = database

    def discover(self) -> list[Path]:
        self.root.mkdir(parents=True, exist_ok=True)
        return sorted(
            (
                path
                for path in self.root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() in SUPPORTED_INGEST_EXTENSIONS
                and not any(part.startswith(".") for part in path.relative_to(self.root).parts)
            ),
            key=lambda path: path.as_posix().casefold(),
        )

    def scan(self) -> dict[str, int]:
        files = self.discover()
        relative_paths = {path.relative_to(self.workspace).as_posix() for path in files}
        existing = {
            row["path"]: (row["mtime_ns"], row["size"], row["extractor_version"])
            for row in self.database.fetch_all(
                "SELECT path, mtime_ns, size, extractor_version FROM ingest_items"
            )
        }
        changed = 0
        with self.database.write() as connection:
            for path in files:
                relative = path.relative_to(self.workspace).as_posix()
                stat = path.stat()
                current_extractor = extractor_version(path)
                if existing.get(relative) == (stat.st_mtime_ns, stat.st_size, current_extractor):
                    continue
                content, extraction_error = extract_ingest_text(path)
                title = path.stem.replace("_", " ").replace("-", " ").strip()
                suffix = path.suffix.casefold()
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                word_count = len(WORD_RE.findall(content))
                connection.execute(
                    """
                    INSERT INTO ingest_items(
                        path, title, source_type, media_type, content, excerpt, word_count,
                        source_url, extraction_error, extractor_version, mtime_ns, size,
                        content_hash, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET title=excluded.title,
                        source_type=excluded.source_type, media_type=excluded.media_type,
                        content=excluded.content, excerpt=excluded.excerpt,
                        word_count=excluded.word_count,
                        extraction_error=excluded.extraction_error, mtime_ns=excluded.mtime_ns,
                        extractor_version=excluded.extractor_version, size=excluded.size,
                        content_hash=excluded.content_hash,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        relative,
                        title,
                        SOURCE_TYPES[suffix],
                        media_type,
                        content,
                        extract_excerpt(content),
                        word_count,
                        extraction_error,
                        current_extractor,
                        stat.st_mtime_ns,
                        stat.st_size,
                        content_hash,
                        utc_now(),
                    ),
                )
                connection.execute("DELETE FROM ingest_fts WHERE path = ?", (relative,))
                connection.execute(
                    "INSERT INTO ingest_fts(path, title, content) VALUES (?, ?, ?)",
                    (relative, title, content),
                )
                connection.execute(
                    """
                    INSERT INTO ingest_embeddings(item_path, model, dimensions, vector, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(item_path) DO UPDATE SET model=excluded.model,
                        dimensions=excluded.dimensions, vector=excluded.vector,
                        updated_at=excluded.updated_at
                    """,
                    (
                        relative,
                        EMBEDDING_MODEL,
                        EMBEDDING_DIMENSIONS,
                        pack_embedding(hash_embedding(f"{title}\n{content}")),
                        utc_now(),
                    ),
                )
                changed += 1

            removed = set(existing) - relative_paths
            for relative in removed:
                connection.execute("DELETE FROM ingest_fts WHERE path = ?", (relative,))
                connection.execute("DELETE FROM ingest_items WHERE path = ?", (relative,))
        return {"indexed": changed, "removed": len(removed), "total": len(files)}

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            rows = self.database.fetch_all(
                """
                SELECT path, title, source_type, excerpt, word_count, indexed_at
                FROM ingest_items ORDER BY mtime_ns DESC LIMIT ?
                """,
                (limit,),
            )
            return [
                {**row, "space": "ingest", "score": 0.0, "match": row["excerpt"]}
                for row in rows
            ]

        tokens = WORD_RE.findall(query)[:12]
        fts_query = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens
        )
        lexical: dict[str, dict[str, Any]] = {}
        if fts_query:
            try:
                for row in self.database.fetch_all(
                    """
                    SELECT i.path, i.title, i.source_type, i.excerpt, i.word_count, i.indexed_at,
                           bm25(ingest_fts, 0.0, 4.0, 1.0) AS rank,
                           snippet(ingest_fts, 2, '<mark>', '</mark>', ' … ', 22) AS match
                    FROM ingest_fts JOIN ingest_items i ON i.path = ingest_fts.path
                    WHERE ingest_fts MATCH ? ORDER BY rank LIMIT ?
                    """,
                    (fts_query, limit * 3),
                ):
                    lexical[row["path"]] = row
            except Exception:
                lexical = {}

        query_vector = hash_embedding(query)
        semantic_rows = self.database.fetch_all(
            """
            SELECT i.path, i.title, i.source_type, i.excerpt, i.word_count, i.indexed_at,
                   e.dimensions, e.vector
            FROM ingest_items i JOIN ingest_embeddings e ON e.item_path = i.path
            """
        )
        semantic = {
            row["path"]: cosine_similarity(
                query_vector, unpack_embedding(row["vector"], row["dimensions"])
            )
            for row in semantic_rows
        }
        by_path = {row["path"]: row for row in semantic_rows}
        candidates = set(lexical) | {path for path, score in semantic.items() if score > 0}
        results: list[dict[str, Any]] = []
        for path in candidates:
            lexical_position = list(lexical).index(path) if path in lexical else limit * 3
            lexical_score = 1.0 / (1.0 + lexical_position) if path in lexical else 0.0
            base = lexical.get(path) or by_path[path]
            results.append(
                {
                    "path": path,
                    "space": "ingest",
                    "title": base["title"],
                    "source_type": base["source_type"],
                    "excerpt": base["excerpt"],
                    "word_count": base["word_count"],
                    "indexed_at": base["indexed_at"],
                    "match": lexical.get(path, {}).get("match", base["excerpt"]),
                    "score": round(
                        0.7 * lexical_score + 0.3 * max(0.0, semantic.get(path, 0.0)), 5
                    ),
                }
            )
        return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]
