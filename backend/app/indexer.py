from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from .database import Database


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
TAG_RE = re.compile(r"(?<![\w/])#([\w][\w/-]{1,48})", re.UNICODE)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
WORD_RE = re.compile(r"[\wÀ-ž][\wÀ-ž'-]*", re.UNICODE)
EMBEDDING_DIMENSIONS = 256
EMBEDDING_MODEL = "local-hash-v1"
KNOWLEDGE_SPACES = ("manual", "ingest", "wiki")
SPREADSHEET_EXTENSIONS = {".ods", ".xlsx", ".csv"}
EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".vscode",
    "backend",
    "frontend",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "locus",
}
EXCLUDED_FILES: set[str] = set()


def space_for_path(path: str) -> str:
    first = PurePosixPath(path).parts[0].casefold() if PurePosixPath(path).parts else ""
    return first if first in KNOWLEDGE_SPACES else "unknown"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def title_from_content(path: str, content: str) -> str:
    match = HEADING_RE.search(content)
    if match:
        return re.sub(r"\s+#+$", "", match.group(2)).strip()
    return Path(path).stem.replace("_", " ").replace("-", " ").strip()


def extract_excerpt(content: str, limit: int = 220) -> str:
    cleaned = re.sub(r"```.*?```", " ", content, flags=re.DOTALL)
    cleaned = HEADING_RE.sub(" ", cleaned)
    cleaned = WIKILINK_RE.sub(lambda match: match.group(2) or match.group(1), cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_>`~-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def extract_headings(content: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for match in HEADING_RE.finditer(content):
        text = re.sub(r"\s+#+$", "", match.group(2)).strip()
        slug = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        headings.append({"level": len(match.group(1)), "text": text, "slug": slug})
    return headings


def extract_tags(content: str) -> list[str]:
    return sorted({match.group(1) for match in TAG_RE.finditer(content)}, key=str.casefold)


def normalize_link_target(source_path: str, target: str, known_paths: set[str]) -> str | None:
    value = unquote(target).strip()
    if not value or value.startswith(("http://", "https://", "mailto:", "#")):
        return None
    value = value.split("#", 1)[0]
    source_parent = PurePosixPath(source_path).parent
    candidate = (source_parent / value).as_posix()
    candidate = PurePosixPath(candidate).as_posix()
    candidates = [candidate]
    if not candidate.lower().endswith(".md"):
        candidates.extend([f"{candidate}.md", f"{candidate}/index.md"])
    by_casefold = {path.casefold(): path for path in known_paths}
    for item in candidates:
        resolved_parts: list[str] = []
        for part in PurePosixPath(item).parts:
            if part == "..":
                if resolved_parts:
                    resolved_parts.pop()
            elif part not in ("", "."):
                resolved_parts.append(part)
        normalized = "/".join(resolved_parts)
        if normalized.casefold() in by_casefold:
            return by_casefold[normalized.casefold()]
    stem = PurePosixPath(value).stem.casefold()
    stem_matches = [path for path in known_paths if PurePosixPath(path).stem.casefold() == stem]
    return stem_matches[0] if len(stem_matches) == 1 else None


def extract_links(source_path: str, content: str, known_paths: set[str]) -> list[dict[str, str | None]]:
    links: list[dict[str, str | None]] = []
    for match in WIKILINK_RE.finditer(content):
        target, alias = match.group(1).strip(), match.group(2)
        links.append(
            {
                "target": target,
                "normalized_target": normalize_link_target(source_path, target, known_paths),
                "label": (alias or target).strip(),
                "kind": "wiki",
            }
        )
    for match in MARKDOWN_LINK_RE.finditer(content):
        label, target = match.group(1).strip(), match.group(2).strip("<>")
        kind = "external" if target.startswith(("http://", "https://")) else "markdown"
        links.append(
            {
                "target": target,
                "normalized_target": normalize_link_target(source_path, target, known_paths),
                "label": label,
                "kind": kind,
            }
        )
    unique: dict[tuple[str, str, str], dict[str, str | None]] = {}
    for link in links:
        unique[(str(link["target"]), str(link["label"]), str(link["kind"]))] = link
    return list(unique.values())


def hash_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    tokens = [token.casefold() for token in WORD_RE.findall(text)]
    features: Counter[str] = Counter(tokens)
    features.update(f"{a}::{b}" for a, b in zip(tokens, tokens[1:]))
    vector = [0.0] * dimensions
    for feature, count in features.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        sign = -1.0 if digest[4] & 1 else 1.0
        vector[bucket] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def pack_embedding(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes, dimensions: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dimensions}f", blob)


def cosine_similarity(left: list[float], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


class NoteIndexer:
    def __init__(self, workspace: Path, database: Database):
        self.workspace = workspace
        self.database = database

    def discover(self) -> list[Path]:
        files: list[Path] = []
        for space in KNOWLEDGE_SPACES:
            root = self.workspace / space
            if not root.is_dir() or root.is_symlink():
                continue
            allowed_extensions = {".md"}
            if space == "manual":
                allowed_extensions.update(SPREADSHEET_EXTENSIONS)
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.casefold() not in allowed_extensions:
                    continue
                relative = path.relative_to(self.workspace)
                if path.is_symlink() or path.name in EXCLUDED_FILES:
                    continue
                if any(
                    part.startswith(".") or part in EXCLUDED_PARTS
                    for part in relative.parts[1:-1]
                ):
                    continue
                files.append(path)
        return sorted(files, key=lambda item: item.as_posix().casefold())

    def scan(self) -> dict[str, int]:
        paths = self.discover()
        relative_paths = {path.relative_to(self.workspace).as_posix() for path in paths}
        existing = {
            row["path"]: (row["mtime_ns"], row["size"])
            for row in self.database.fetch_all("SELECT path, mtime_ns, size FROM notes")
        }
        changed = 0
        with self.database.write() as connection:
            for path in paths:
                relative = path.relative_to(self.workspace).as_posix()
                stat = path.stat()
                if existing.get(relative) == (stat.st_mtime_ns, stat.st_size):
                    continue
                if path.suffix.casefold() in SPREADSHEET_EXTENSIONS:
                    from .spreadsheets import SpreadsheetError, spreadsheet_to_markdown

                    try:
                        content = spreadsheet_to_markdown(path)
                    except SpreadsheetError as error:
                        content = f"# {path.stem}\n\n> Spreadsheet preview unavailable: {error}\n"
                else:
                    content = path.read_text(encoding="utf-8", errors="replace")
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                title = title_from_content(relative, content)
                headings = extract_headings(content)
                tags = extract_tags(content)
                connection.execute(
                    """
                    INSERT INTO notes(path, space, title, content, excerpt, word_count, mtime_ns, size,
                                      content_hash, headings_json, tags_json, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        space=excluded.space, title=excluded.title, content=excluded.content, excerpt=excluded.excerpt,
                        word_count=excluded.word_count, mtime_ns=excluded.mtime_ns,
                        size=excluded.size, content_hash=excluded.content_hash,
                        headings_json=excluded.headings_json, tags_json=excluded.tags_json,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        relative,
                        space_for_path(relative),
                        title,
                        content,
                        extract_excerpt(content),
                        len(WORD_RE.findall(content)),
                        stat.st_mtime_ns,
                        stat.st_size,
                        content_hash,
                        json.dumps(headings, ensure_ascii=False),
                        json.dumps(tags, ensure_ascii=False),
                        utc_now(),
                    ),
                )
                connection.execute("DELETE FROM notes_fts WHERE path = ?", (relative,))
                connection.execute(
                    "INSERT INTO notes_fts(path, title, content, tags) VALUES (?, ?, ?, ?)",
                    (relative, title, content, " ".join(tags)),
                )
                vector = pack_embedding(hash_embedding(f"{title}\n{content}"))
                connection.execute(
                    """
                    INSERT INTO embeddings(note_path, model, dimensions, vector, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(note_path) DO UPDATE SET model=excluded.model,
                        dimensions=excluded.dimensions, vector=excluded.vector,
                        updated_at=excluded.updated_at
                    """,
                    (relative, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, vector, utc_now()),
                )
                changed += 1

            removed = set(existing) - relative_paths
            for relative in removed:
                connection.execute("DELETE FROM notes_fts WHERE path = ?", (relative,))
                connection.execute("DELETE FROM notes WHERE path = ?", (relative,))

            all_notes = connection.execute("SELECT path, content FROM notes").fetchall()
            all_paths = {row["path"] for row in all_notes} | {
                row["path"]
                for row in connection.execute("SELECT path FROM ingest_items").fetchall()
            }
            connection.execute("DELETE FROM links")
            for row in all_notes:
                for link in extract_links(row["path"], row["content"], all_paths):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO links(source_path, target, normalized_target, label, kind)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["path"],
                            link["target"],
                            link["normalized_target"],
                            link["label"],
                            link["kind"],
                        ),
                    )
        return {"indexed": changed, "removed": len(set(existing) - relative_paths), "total": len(paths)}

    def search(
        self, query: str, limit: int = 12, spaces: set[str] | None = None
    ) -> list[dict[str, Any]]:
        query = query.strip()
        selected = sorted(spaces or {"manual", "ingest", "wiki"})
        placeholders = ",".join("?" for _ in selected)
        if not query:
            rows = self.database.fetch_all(
                f"""
                SELECT path, space, title, excerpt, word_count, indexed_at FROM notes
                WHERE space IN ({placeholders}) ORDER BY mtime_ns DESC LIMIT ?
                """,
                (*selected, limit),
            )
            return [{**row, "score": 0.0, "match": row["excerpt"]} for row in rows]

        fts_query = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in WORD_RE.findall(query)[:12]
        )
        lexical: dict[str, dict[str, Any]] = {}
        if fts_query:
            try:
                rows = self.database.fetch_all(
                    f"""
                    SELECT n.path, n.space, n.title, n.excerpt, n.word_count, n.indexed_at,
                           bm25(notes_fts, 0.0, 4.0, 1.0, 2.0) AS rank,
                           snippet(notes_fts, 2, '<mark>', '</mark>', ' … ', 22) AS match
                    FROM notes_fts JOIN notes n ON n.path = notes_fts.path
                    WHERE notes_fts MATCH ? AND n.space IN ({placeholders})
                    ORDER BY rank LIMIT ?
                    """,
                    (fts_query, *selected, limit * 3),
                )
                for row in rows:
                    lexical[row["path"]] = row
            except Exception:
                lexical = {}

        query_vector = hash_embedding(query)
        semantic_rows = self.database.fetch_all(
            f"""
            SELECT n.path, n.space, n.title, n.excerpt, n.word_count, n.indexed_at,
                   e.dimensions, e.vector
            FROM notes n JOIN embeddings e ON e.note_path = n.path
            WHERE n.space IN ({placeholders})
            """,
            tuple(selected),
        )
        semantic = {
            row["path"]: cosine_similarity(query_vector, unpack_embedding(row["vector"], row["dimensions"]))
            for row in semantic_rows
        }
        candidates = set(lexical) | {path for path, score in semantic.items() if score > 0}
        results: list[dict[str, Any]] = []
        by_path = {row["path"]: row for row in semantic_rows}
        for path in candidates:
            lexical_position = list(lexical).index(path) if path in lexical else limit * 3
            lexical_score = 1.0 / (1.0 + lexical_position) if path in lexical else 0.0
            semantic_score = max(0.0, semantic.get(path, 0.0))
            base = lexical.get(path) or by_path[path]
            results.append(
                {
                    "path": path,
                    "space": base["space"],
                    "title": base["title"],
                    "excerpt": base["excerpt"],
                    "word_count": base["word_count"],
                    "indexed_at": base["indexed_at"],
                    "match": lexical.get(path, {}).get("match", base["excerpt"]),
                    "score": round(0.7 * lexical_score + 0.3 * semantic_score, 5),
                }
            )
        return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]
