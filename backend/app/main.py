from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent_service import WikiAgent
from .auth import AuthError, CodexAuth
from .config import Settings, settings as default_settings
from .database import Database
from .ingest import IngestIndexer, SUPPORTED_INGEST_EXTENSIONS
from .indexer import (
    EXCLUDED_FILES,
    EXCLUDED_PARTS,
    KNOWLEDGE_SPACES,
    SPREADSHEET_EXTENSIONS,
    NoteIndexer,
    space_for_path,
)
from .manual_integration import ManualIntegrationTracker
from .operations import OperationConflict, OperationManager, ensure_wiki_contract
from .spreadsheets import SpreadsheetError, read_spreadsheet
from .web_ingest import WebIngestError, download_web_source, extract_http_urls
from .workspace_shell import WorkspaceShell


logger = logging.getLogger(__name__)


class NoteCreate(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=2_000_000)


class NoteUpdate(BaseModel):
    content: str = Field(max_length=2_000_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = None
    current_note: str | None = None
    context_paths: list[str] = Field(default_factory=list, max_length=20)
    write_mode: Literal["auto", "review", "integrate"] = "auto"


class URLIngest(BaseModel):
    url: str = Field(min_length=8, max_length=4_000)
    title: str | None = Field(default=None, max_length=300)
    notes: str = Field(default="", max_length=100_000)


class RuntimeSettingsUpdate(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]
    fast_mode: bool = False


class DirectoryCreate(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class MoveEntry(BaseModel):
    source_path: str = Field(min_length=1, max_length=500)
    target_path: str = Field(min_length=1, max_length=500)
    is_directory: bool = False


AVAILABLE_MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]
REASONING_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]


async def periodic_scan(
    indexer: NoteIndexer, ingest_indexer: IngestIndexer, interval: float
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            indexer.scan()
            ingest_indexer.scan()
        except Exception:
            # A transient partial write should not terminate the background watcher.
            continue


async def integrate_pending_manual_changes(
    indexer: NoteIndexer,
    tracker: ManualIntegrationTracker,
    agent: WikiAgent,
) -> dict[str, Any]:
    indexer.scan()
    changes = tracker.pending()
    if not changes:
        return {"status": "idle", "changes": 0, "operation_id": None}
    result = await agent.integrate_manual_changes(changes)
    tracker.mark_completed(changes, result["operation_id"])
    return {
        "status": "completed",
        "changes": len(changes),
        "operation_id": result["operation_id"],
    }


async def periodic_manual_integration(
    indexer: NoteIndexer,
    tracker: ManualIntegrationTracker,
    agent: WikiAgent,
    interval: float,
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            result = await integrate_pending_manual_changes(indexer, tracker, agent)
            if result["changes"]:
                logger.info(
                    "Automatically integrated %s Manual change(s)", result["changes"]
                )
        except AuthError:
            logger.info("Automatic Manual integration is waiting for Codex login")
        except Exception:
            logger.exception("Automatic Manual integration failed; it will retry")


def create_app(app_settings: Settings = default_settings) -> FastAPI:
    database = Database(app_settings.database)
    indexer = NoteIndexer(app_settings.workspace, database)
    ingest_indexer = IngestIndexer(app_settings.workspace, database)
    manual_integrations = ManualIntegrationTracker(database)
    operations = OperationManager(app_settings.workspace, database, indexer)
    auth = CodexAuth(database)
    contract_path = app_settings.contract or Path(__file__).resolve().parents[2] / "AGENTS.md"
    workspace_shell = None
    if app_settings.agent_workspace and app_settings.workspace_runtime_socket:
        app_settings.agent_workspace.mkdir(parents=True, exist_ok=True)
        workspace_shell = WorkspaceShell(
            app_settings.workspace_runtime_socket,
            app_settings.agent_workspace,
            app_settings.workspace,
            ingest_indexer,
        )
    agent = WikiAgent(
        database,
        indexer,
        ingest_indexer,
        operations,
        auth,
        app_settings.model,
        contract_path,
        workspace_shell,
    )

    async def store_web_source(url: str, requested_title: str | None = None) -> dict[str, Any]:
        normalized_url = url.strip()
        existing = database.fetch_one(
            "SELECT path FROM ingest_items WHERE source_url = ? ORDER BY indexed_at DESC LIMIT 1",
            (normalized_url,),
        )
        if existing and (app_settings.workspace / existing["path"]).is_file():
            return file_payload(database, existing["path"], ingest_indexer)

        downloaded = await download_web_source(normalized_url)
        title = (requested_title or downloaded.title).strip()[:300] or "Web source"
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:80] or "web-source"
        group = ingest_indexer.create_group(title)
        target_dir = app_settings.workspace / group["folder_path"]
        target = target_dir / f"{slug}{downloaded.extension}"
        atomic_write_bytes(target, downloaded.content)
        ingest_indexer.scan()
        relative = target.relative_to(app_settings.workspace).as_posix()
        item = database.fetch_one(
            "SELECT content FROM ingest_items WHERE path = ?", (relative,)
        )
        if not item:
            raise WebIngestError("The downloaded website could not be indexed", 500)
        with database.write() as connection:
            connection.execute(
                "UPDATE ingest_items SET title = ?, source_url = ? WHERE path = ?",
                (title, normalized_url, relative),
            )
            connection.execute("DELETE FROM ingest_fts WHERE path = ?", (relative,))
            connection.execute(
                "INSERT INTO ingest_fts(path, title, content) VALUES (?, ?, ?)",
                (relative, title, item["content"]),
            )
        ingest_indexer.register_group(group["folder_path"], relative)
        return file_payload(database, relative, ingest_indexer)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        ensure_wiki_contract(app_settings.workspace)
        indexer.scan()
        ingest_indexer.scan()
        scan_task = asyncio.create_task(
            periodic_scan(indexer, ingest_indexer, app_settings.scan_interval_seconds)
        )
        manual_integration_task = asyncio.create_task(
            periodic_manual_integration(
                indexer,
                manual_integrations,
                agent,
                app_settings.manual_integration_interval_seconds,
            )
        )
        try:
            yield
        finally:
            scan_task.cancel()
            manual_integration_task.cancel()
            with suppress(asyncio.CancelledError):
                await scan_task
            with suppress(asyncio.CancelledError):
                await manual_integration_task
            if workspace_shell:
                await workspace_shell.close()
            await auth.close()

    app = FastAPI(
        title="Locus Knowledge Wiki",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "testserver", "wiki"],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:7332", "http://127.0.0.1:7332"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path in {"/service-worker.js", "/manifest.webmanifest"}:
            response.headers["Cache-Control"] = "no-cache"
            if request.url.path == "/service-worker.js":
                response.headers["Service-Worker-Allowed"] = "/"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, error: AuthError):
        return JSONResponse(status_code=401, content={"detail": str(error)})

    @app.exception_handler(OperationConflict)
    async def operation_conflict_handler(_: Request, error: OperationConflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        count = database.fetch_one("SELECT COUNT(*) AS count FROM notes") or {"count": 0}
        ingests = database.fetch_one("SELECT COUNT(*) AS count FROM ingest_items") or {"count": 0}
        selected_model, _, _ = agent.preferences()
        return {
            "status": "ok",
            "notes": count["count"],
            "ingest_items": ingests["count"],
            "database": app_settings.database.name,
            "model": selected_model,
        }

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        totals = database.fetch_one(
            """
            SELECT COUNT(*) AS notes, COALESCE(SUM(word_count), 0) AS words,
                   COALESCE(SUM(size), 0) AS bytes FROM notes
            """
        ) or {"notes": 0, "words": 0, "bytes": 0}
        link_count = database.fetch_one("SELECT COUNT(*) AS count FROM links") or {"count": 0}
        tag_rows = database.fetch_all("SELECT tags_json FROM notes")
        space_rows = database.fetch_all(
            "SELECT space, COUNT(*) AS count FROM notes GROUP BY space"
        )
        ingest_assets = database.fetch_one("SELECT COUNT(*) AS count FROM ingest_items") or {
            "count": 0
        }
        spaces = {"manual": 0, "ingest": ingest_assets["count"], "wiki": 0}
        for row in space_rows:
            spaces[row["space"]] = spaces.get(row["space"], 0) + row["count"]
        tags = set()
        for row in tag_rows:
            tags.update(json.loads(row["tags_json"]))
        return {**totals, "links": link_count["count"], "tags": len(tags), "spaces": spaces}

    @app.post("/api/index/refresh")
    async def refresh_index() -> dict[str, Any]:
        return {"markdown": indexer.scan(), "ingest": ingest_indexer.scan()}

    @app.get("/api/search")
    async def search(
        q: str = Query(default="", max_length=500),
        limit: int = Query(12, ge=1, le=50),
        spaces: str = Query(default="manual,ingest,wiki", max_length=80),
    ):
        selected = {
            value.strip().casefold()
            for value in spaces.split(",")
            if value.strip().casefold() in {"manual", "ingest", "wiki"}
        }
        if not selected:
            raise HTTPException(status_code=400, detail="Select at least one knowledge space")
        results = indexer.search(q, limit * 2, selected)
        if "ingest" in selected:
            results.extend(ingest_indexer.search(q, limit * 2))
        results.sort(key=lambda item: (item["score"], item["indexed_at"]), reverse=True)
        return {"query": q, "spaces": sorted(selected), "results": results[:limit]}

    @app.get("/api/notes")
    async def list_notes(
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        rows = database.fetch_all(
            """
            SELECT n.path, n.space, n.title, n.excerpt, n.word_count, n.size, n.mtime_ns,
                   n.indexed_at, n.headings_json, n.tags_json,
                   COUNT(DISTINCT incoming.source_path) AS backlinks
            FROM notes n
            LEFT JOIN links incoming ON incoming.normalized_target = n.path
            GROUP BY n.path ORDER BY n.mtime_ns DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        total = database.fetch_one("SELECT COUNT(*) AS count FROM notes") or {"count": 0}
        return {"notes": [database.decode_note(row) for row in rows], "total": total["count"]}

    @app.get("/api/files")
    async def list_files() -> dict[str, Any]:
        markdown = database.fetch_all(
            """
            SELECT n.path, n.space, n.title, n.size, n.mtime_ns, n.indexed_at, n.word_count,
                   CASE WHEN n.space='ingest' THEN
                       CASE WHEN EXISTS(
                           SELECT 1 FROM source_integrations s WHERE s.source_path=n.path
                       ) THEN 'integrated' ELSE 'unprocessed' END
                   ELSE NULL END AS integration_status
            FROM notes n ORDER BY n.path COLLATE NOCASE
            """
        )
        assets = database.fetch_all(
            """
            SELECT i.path, i.title, i.source_type, i.media_type, i.size, i.mtime_ns,
                   i.indexed_at, i.word_count, i.extraction_error,
                   CASE WHEN EXISTS(
                       SELECT 1 FROM source_integrations s WHERE s.source_path=i.path
                   ) THEN 'integrated' ELSE 'unprocessed' END AS integration_status
            FROM ingest_items i ORDER BY i.path COLLATE NOCASE
            """
        )
        files = [
            {
                **row,
                "kind": "spreadsheet"
                if Path(row["path"]).suffix.casefold() in SPREADSHEET_EXTENSIONS
                else "markdown",
                "editable": row["space"] != "ingest"
                and Path(row["path"]).suffix.casefold() == ".md",
                "extension": Path(row["path"]).suffix.casefold(),
            }
            for row in markdown
        ]
        files.extend(
            {
                **row,
                "space": "ingest",
                "kind": "asset",
                "editable": False,
                "extension": Path(row["path"]).suffix.casefold(),
            }
            for row in assets
        )
        files = ingest_indexer.decorate_files(files)
        directories = ingest_indexer.decorate_directories(
            knowledge_directories(app_settings.workspace)
        )
        return {
            "files": files,
            "directories": directories,
            "spaces": ["manual", "ingest", "wiki"],
        }

    @app.post("/api/files/directories", status_code=201)
    async def create_directory(body: DirectoryCreate) -> dict[str, str]:
        target, relative = resolve_knowledge_entry(app_settings.workspace, body.path)
        if target.exists():
            raise HTTPException(status_code=409, detail="A file or directory already exists there")
        if not target.parent.is_dir():
            raise HTTPException(status_code=400, detail="The parent directory does not exist")
        target.mkdir(parents=False)
        return {"path": relative, "space": space_for_path(relative)}

    @app.post("/api/files/move")
    async def move_entry(body: MoveEntry) -> dict[str, Any]:
        source, source_relative = resolve_knowledge_entry(
            app_settings.workspace, body.source_path
        )
        target, target_relative = resolve_knowledge_entry(
            app_settings.workspace, body.target_path
        )
        if source_relative in {
            "manual",
            "ingest",
            "wiki",
            "wiki/index.md",
            "wiki/log.md",
        }:
            raise HTTPException(status_code=403, detail="That path is maintained by Locus")
        if not source.exists() or source.is_dir() != body.is_directory:
            raise HTTPException(status_code=404, detail="File or directory not found")
        if target.exists():
            raise HTTPException(status_code=409, detail="The destination already exists")
        if space_for_path(source_relative) != space_for_path(target_relative):
            raise HTTPException(status_code=400, detail="Moves must stay in the same knowledge space")
        if not target.parent.is_dir():
            raise HTTPException(status_code=400, detail="The destination directory does not exist")
        if body.is_directory:
            try:
                target.relative_to(source)
            except ValueError:
                pass
            else:
                raise HTTPException(status_code=400, detail="A directory cannot be moved inside itself")
        elif source.suffix.casefold() != target.suffix.casefold():
            raise HTTPException(status_code=400, detail="Renaming cannot change the file type")
        source.rename(target)
        indexer.scan()
        ingest_indexer.scan()
        return {
            "path": target_relative,
            "space": space_for_path(target_relative),
            "is_directory": body.is_directory,
        }

    @app.delete("/api/files/{file_path:path}")
    async def delete_file(file_path: str) -> dict[str, bool]:
        target, relative = resolve_knowledge_entry(app_settings.workspace, file_path)
        if relative in {"wiki/index.md", "wiki/log.md"}:
            raise HTTPException(status_code=403, detail="Wiki index and log cannot be deleted")
        indexed = database.fetch_one("SELECT path FROM notes WHERE path=?", (relative,))
        ingested = database.fetch_one("SELECT path FROM ingest_items WHERE path=?", (relative,))
        if not target.is_file() or not (indexed or ingested):
            raise HTTPException(status_code=404, detail="Indexed file not found")
        target.unlink()
        with database.write() as connection:
            connection.execute(
                "DELETE FROM source_integrations WHERE source_path=?", (relative,)
            )
        indexer.scan()
        ingest_indexer.scan()
        return {"deleted": True}

    @app.delete("/api/directories/{directory_path:path}")
    async def delete_directory(directory_path: str) -> dict[str, bool]:
        target, relative = resolve_knowledge_entry(
            app_settings.workspace, directory_path
        )
        if relative in set(KNOWLEDGE_SPACES):
            raise HTTPException(status_code=403, detail="Knowledge-space roots cannot be deleted")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        if next(target.iterdir(), None) is not None:
            raise HTTPException(
                status_code=409,
                detail="The directory is not empty; move or delete its children first",
            )
        target.rmdir()
        return {"deleted": True}

    @app.post("/api/ingest/upload", status_code=201)
    async def upload_ingest(
        file: UploadFile = File(...), folder: str = Form(default="")
    ) -> dict[str, Any]:
        filename = Path(file.filename or "").name
        suffix = Path(filename).suffix.casefold()
        allowed = SUPPORTED_INGEST_EXTENSIONS | {".md"}
        if not filename or suffix not in allowed:
            raise HTTPException(
                status_code=400,
                detail="Supported ingest files are PDF, Markdown, text, HTML, CSV, and JSON",
            )
        content = await file.read(50_000_001)
        await file.close()
        if len(content) > 50_000_000:
            raise HTTPException(status_code=413, detail="Ingest files are limited to 50 MB")
        parent = resolve_ingest_directory(app_settings.workspace, folder)
        parent.mkdir(parents=True, exist_ok=True)
        group = ingest_indexer.create_group(Path(filename).stem, parent)
        target = app_settings.workspace / group["folder_path"] / filename
        atomic_write_bytes(target, content)
        indexer.scan()
        ingest_indexer.scan()
        relative = target.relative_to(app_settings.workspace).as_posix()
        ingest_indexer.register_group(group["folder_path"], relative)
        return file_payload(database, relative, ingest_indexer)

    @app.post("/api/manual/spreadsheet", status_code=201)
    async def upload_manual_spreadsheet(
        file: UploadFile = File(...), folder: str = Form(default="manual")
    ) -> dict[str, Any]:
        filename = Path(file.filename or "").name
        suffix = Path(filename).suffix.casefold()
        if not filename or suffix not in SPREADSHEET_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail="Supported spreadsheet notes are ODS, XLSX, and CSV",
            )
        target_dir = resolve_manual_directory(app_settings.workspace, folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = available_path(target_dir / filename)
        content = await file.read(50_000_001)
        await file.close()
        if len(content) > 50_000_000:
            raise HTTPException(status_code=413, detail="Spreadsheet files are limited to 50 MB")
        atomic_write_bytes(target, content)
        try:
            read_spreadsheet(target)
        except SpreadsheetError as error:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(error)) from error
        indexer.scan()
        relative = target.relative_to(app_settings.workspace).as_posix()
        return file_payload(database, relative, ingest_indexer)

    @app.post("/api/ingest/url", status_code=201)
    async def ingest_url(body: URLIngest) -> dict[str, Any]:
        try:
            return await store_web_source(body.url, body.title)
        except WebIngestError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.get("/api/ingest/items/{item_path:path}")
    async def get_ingest_item(item_path: str) -> dict[str, Any]:
        normalized = normalize_ingest_path(app_settings.workspace, item_path)
        item = database.fetch_one("SELECT * FROM ingest_items WHERE path = ?", (normalized,))
        if not item:
            raise HTTPException(status_code=404, detail="Ingest item not found")
        return {
            **item,
            "space": "ingest",
            "kind": "asset",
            **ingest_indexer.metadata_for_path(
                normalized, item.get("mtime_ns"), item.get("indexed_at")
            ),
            **source_integration_payload(database, normalized),
        }

    @app.get("/api/ingest/files/{item_path:path}")
    async def open_ingest_file(item_path: str):
        normalized = normalize_ingest_path(app_settings.workspace, item_path)
        path = (app_settings.workspace / normalized).resolve()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Ingest file not found")
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    @app.post("/api/notes", status_code=201)
    async def create_note(body: NoteCreate) -> dict[str, Any]:
        path = resolve_note_path(app_settings.workspace, body.path)
        relative = path.relative_to(app_settings.workspace).as_posix()
        if space_for_path(relative) == "ingest":
            raise HTTPException(status_code=400, detail="Use the Ingest controls to add source material")
        if path.exists():
            raise HTTPException(status_code=409, detail="A note already exists at that path")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, body.content)
        indexer.scan()
        return note_payload(
            database,
            path.relative_to(app_settings.workspace).as_posix(),
            ingest_indexer,
        )

    @app.get("/api/notes/{note_path:path}")
    async def get_note(note_path: str) -> dict[str, Any]:
        normalized = normalize_note_path(app_settings.workspace, note_path)
        return note_payload(database, normalized, ingest_indexer)

    @app.get("/api/spreadsheets/{item_path:path}")
    async def get_spreadsheet(item_path: str) -> dict[str, Any]:
        target, relative = resolve_knowledge_entry(app_settings.workspace, item_path)
        if (
            space_for_path(relative) != "manual"
            or target.suffix.casefold() not in SPREADSHEET_EXTENSIONS
            or not target.is_file()
        ):
            raise HTTPException(status_code=404, detail="Spreadsheet note not found")
        note = database.fetch_one("SELECT * FROM notes WHERE path = ?", (relative,))
        if not note:
            raise HTTPException(status_code=404, detail="Spreadsheet note is not indexed")
        try:
            sheets = read_spreadsheet(target)
        except SpreadsheetError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            **database.decode_note(note),
            "kind": "spreadsheet",
            "media_type": target.suffix.casefold().lstrip(".").upper(),
            "sheets": sheets,
        }

    @app.put("/api/notes/{note_path:path}")
    async def update_note(note_path: str, body: NoteUpdate) -> dict[str, Any]:
        path = resolve_note_path(app_settings.workspace, note_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Note not found")
        relative = path.relative_to(app_settings.workspace).as_posix()
        if space_for_path(relative) == "ingest":
            raise HTTPException(status_code=403, detail="Ingest sources are immutable")
        atomic_write(path, body.content)
        indexer.scan()
        return note_payload(
            database,
            path.relative_to(app_settings.workspace).as_posix(),
            ingest_indexer,
        )

    @app.get("/api/graph")
    async def graph() -> dict[str, Any]:
        notes = database.fetch_all(
            "SELECT path, title, word_count, tags_json FROM notes ORDER BY title COLLATE NOCASE"
        )
        links = database.fetch_all(
            """
            SELECT source_path AS source, normalized_target AS target, label, kind
            FROM links WHERE normalized_target IS NOT NULL
            """
        )
        degree: dict[str, int] = {note["path"]: 0 for note in notes}
        for link in links:
            degree[link["source"]] = degree.get(link["source"], 0) + 1
            degree[link["target"]] = degree.get(link["target"], 0) + 1
        return {
            "nodes": [
                {
                    "id": note["path"],
                    "title": note["title"],
                    "word_count": note["word_count"],
                    "tags": json.loads(note["tags_json"]),
                    "degree": degree[note["path"]],
                }
                for note in notes
            ],
            "edges": links,
        }

    @app.get("/api/auth/codex/status")
    async def auth_status() -> dict[str, Any]:
        return auth.status()

    @app.post("/api/auth/codex/device/start")
    async def auth_start() -> dict[str, Any]:
        return await auth.start_device_login()

    @app.post("/api/auth/codex/device/{flow_id}/poll")
    async def auth_poll(flow_id: str) -> dict[str, Any]:
        return await auth.poll_device_login(flow_id)

    @app.delete("/api/auth/codex")
    async def auth_logout() -> dict[str, bool]:
        auth.logout()
        return {"authenticated": False}

    @app.get("/api/auth/codex/usage")
    async def auth_usage() -> dict[str, Any]:
        return await auth.usage()

    @app.get("/api/settings")
    async def runtime_settings() -> dict[str, Any]:
        selected_model, reasoning_effort, fast_mode = agent.preferences()
        models = list(dict.fromkeys([selected_model, app_settings.model, *AVAILABLE_MODELS]))
        return {
            "model": selected_model,
            "reasoning_effort": reasoning_effort,
            "fast_mode": fast_mode,
            "models": models,
            "reasoning_efforts": REASONING_EFFORTS,
            "manual_integration": {
                "enabled": True,
                "interval_seconds": app_settings.manual_integration_interval_seconds,
                **manual_integrations.status(),
            },
        }

    @app.put("/api/settings")
    async def update_runtime_settings(body: RuntimeSettingsUpdate) -> dict[str, Any]:
        allowed_models = {app_settings.model, *AVAILABLE_MODELS}
        if body.model not in allowed_models:
            raise HTTPException(status_code=400, detail="Select one of the available models")
        if body.model == "gpt-5.5" and body.reasoning_effort == "max":
            raise HTTPException(status_code=400, detail="gpt-5.5 does not support max reasoning")
        now = datetime.now(UTC).isoformat()
        with database.write() as connection:
            connection.executemany(
                """
                INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                [
                    ("model", body.model, now),
                    ("reasoning_effort", body.reasoning_effort, now),
                    ("fast_mode", "true" if body.fast_mode else "false", now),
                ],
            )
        return await runtime_settings()

    @app.get("/api/wiki/lint")
    async def wiki_lint() -> dict[str, Any]:
        return operations.lint()

    @app.get("/api/wiki/schema")
    async def wiki_schema() -> dict[str, str]:
        path = contract_path
        return {"path": "AGENTS.md", "content": path.read_text(encoding="utf-8")}

    @app.get("/api/operations/{operation_id}")
    async def get_operation(operation_id: str) -> dict[str, Any]:
        try:
            return operations.get(operation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/operations/{operation_id}/diff")
    async def get_operation_diff(operation_id: str) -> dict[str, Any]:
        try:
            return operations.diff(operation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/operations/{operation_id}/undo")
    async def undo_operation(operation_id: str) -> dict[str, Any]:
        try:
            return operations.undo(operation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/chat/threads")
    async def chat_threads() -> dict[str, Any]:
        return {"threads": agent.threads()}

    @app.get("/api/chat/threads/{thread_id}")
    async def chat_messages(thread_id: str) -> dict[str, Any]:
        try:
            return {
                "thread_id": thread_id,
                "messages": agent.messages(thread_id),
                "operations": operations.for_thread(thread_id),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/api/chat/threads/{thread_id}")
    async def delete_chat_thread(thread_id: str) -> dict[str, bool]:
        agent.delete_thread(thread_id)
        return {"deleted": True}

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        async def events():
            try:
                context_paths = list(body.context_paths)
                for position, url in enumerate(extract_http_urls(body.question), start=1):
                    activity_id = f"download:{position}"
                    yield json.dumps(
                        {
                            "type": "activity",
                            "activity": {
                                "id": activity_id,
                                "label": "Downloading website into Ingest",
                                "detail": url,
                                "kind": "download_url",
                                "status": "running",
                            },
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    source = await store_web_source(url)
                    yield json.dumps(
                        {
                            "type": "activity",
                            "activity": {
                                "id": activity_id,
                                "label": f"Downloaded {source['path']}",
                                "detail": url,
                                "kind": "download_url",
                                "status": "completed",
                            },
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    if source["path"] not in context_paths:
                        context_paths.append(source["path"])
                write_mode = body.write_mode
                if write_mode == "auto":
                    for path in context_paths:
                        if not path.casefold().startswith("ingest/"):
                            continue
                        integrated = database.fetch_one(
                            "SELECT 1 AS found FROM source_integrations WHERE source_path = ?",
                            (path,),
                        )
                        if not integrated:
                            write_mode = "review"
                            break
                async for event in agent.stream_answer(
                    body.question,
                    body.thread_id,
                    body.current_note,
                    context_paths,
                    write_mode,
                ):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except (AuthError, KeyError, ValueError, WebIngestError) as error:
                yield json.dumps({"type": "error", "message": str(error)}) + "\n"
            except Exception:
                logger.exception("Codex chat request failed")
                yield json.dumps(
                    {
                        "type": "error",
                        "message": "The model request failed. Check the Locus server logs and try again.",
                    }
                ) + "\n"

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )

    assets = app_settings.frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        frontend_root = app_settings.frontend_dist.resolve()
        requested_file = (frontend_root / unquote(full_path)).resolve()
        if requested_file.is_relative_to(frontend_root) and requested_file.is_file():
            media_type = (
                "application/manifest+json"
                if requested_file.suffix == ".webmanifest"
                else None
            )
            return FileResponse(requested_file, media_type=media_type)

        index_file = frontend_root / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=503,
            content={"detail": "Frontend has not been built", "docs": "/api/docs"},
        )

    return app


def normalize_note_path(workspace: Path, note_path: str) -> str:
    path = resolve_note_path(workspace, note_path)
    return path.relative_to(workspace).as_posix()


def resolve_note_path(workspace: Path, note_path: str) -> Path:
    raw = unquote(note_path).replace("\\", "/").strip("/")
    if not raw.lower().endswith(".md"):
        raw += ".md"
    candidate = (workspace / raw).resolve()
    try:
        relative = candidate.relative_to(workspace)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Note path must stay inside the workspace") from error
    if space_for_path(relative.as_posix()) not in KNOWLEDGE_SPACES:
        raise HTTPException(
            status_code=400,
            detail="Notes must live below manual/, ingest/, or wiki/",
        )
    if candidate.name in EXCLUDED_FILES or any(
        part.startswith(".") or part in EXCLUDED_PARTS for part in relative.parts[:-1]
    ):
        raise HTTPException(status_code=400, detail="That path is reserved by the application")
    return candidate


def atomic_write(path: Path, content: str) -> None:
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


def atomic_write_bytes(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def resolve_ingest_directory(workspace: Path, folder: str) -> Path:
    root = (workspace / "ingest").resolve()
    raw = unquote(folder).replace("\\", "/").strip("/")
    candidate = (root / raw).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Ingest folder must stay inside ingest/") from error
    if any(part.startswith(".") for part in relative.parts):
        raise HTTPException(status_code=400, detail="Hidden ingest folders are not supported")
    return candidate


def resolve_manual_directory(workspace: Path, folder: str) -> Path:
    root = (workspace / "manual").resolve()
    raw = unquote(folder).replace("\\", "/").strip("/")
    if raw.casefold() == "manual":
        raw = ""
    elif raw.casefold().startswith("manual/"):
        raw = raw[len("manual/") :]
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Manual folder must stay inside manual/") from error
    if any(part.startswith(".") for part in candidate.relative_to(root).parts):
        raise HTTPException(status_code=400, detail="Hidden Manual folders are not supported")
    return candidate


def normalize_ingest_path(workspace: Path, item_path: str) -> str:
    raw = unquote(item_path).replace("\\", "/").strip("/")
    if not raw.casefold().startswith("ingest/"):
        raw = f"ingest/{raw}"
    candidate = (workspace / raw).resolve()
    root = (workspace / "ingest").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Ingest path must stay inside ingest/") from error
    return candidate.relative_to(workspace).as_posix()


def available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a unique ingest filename")


def file_payload(
    database: Database,
    path: str,
    ingest_indexer: IngestIndexer | None = None,
) -> dict[str, Any]:
    note = database.fetch_one(
        """
        SELECT path, space, title, size, word_count, mtime_ns, indexed_at
        FROM notes WHERE path = ?
        """,
        (path,),
    )
    if note:
        extension = Path(path).suffix.casefold()
        payload = {
            **note,
            "kind": "spreadsheet"
            if extension in SPREADSHEET_EXTENSIONS
            else "markdown",
            "editable": extension == ".md" and note["space"] != "ingest",
            "extension": extension,
        }
        if note["space"] == "ingest":
            if ingest_indexer:
                payload.update(
                    ingest_indexer.metadata_for_path(
                        path, note.get("mtime_ns"), note.get("indexed_at")
                    )
                )
            payload.update(source_integration_payload(database, path))
        return payload
    item = database.fetch_one(
        """
        SELECT path, title, source_type, media_type, size, word_count, mtime_ns, indexed_at,
               source_url, extraction_error FROM ingest_items WHERE path = ?
        """,
        (path,),
    )
    if item:
        payload = {
            **item,
            "space": "ingest",
            "kind": "asset",
            "editable": False,
            "extension": Path(path).suffix.casefold(),
            **source_integration_payload(database, path),
        }
        if ingest_indexer:
            payload.update(
                ingest_indexer.metadata_for_path(
                    path, item.get("mtime_ns"), item.get("indexed_at")
                )
            )
        return payload
    raise HTTPException(status_code=500, detail="The new ingest item could not be indexed")


def note_payload(
    database: Database,
    path: str,
    ingest_indexer: IngestIndexer | None = None,
) -> dict[str, Any]:
    note = database.fetch_one("SELECT * FROM notes WHERE path = ?", (path,))
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    decoded = database.decode_note(note)
    decoded["outgoing_links"] = database.fetch_all(
        """
        SELECT target, normalized_target, label, kind FROM links
        WHERE source_path = ? ORDER BY kind, label COLLATE NOCASE
        """,
        (path,),
    )
    decoded["backlinks"] = database.fetch_all(
        """
        SELECT l.source_path AS path, n.title, l.label
        FROM links l JOIN notes n ON n.path = l.source_path
        WHERE l.normalized_target = ? ORDER BY n.title COLLATE NOCASE
        """,
        (path,),
    )
    decoded["history"] = database.fetch_all(
        """
        SELECT o.id, o.kind, o.title, o.status, o.summary, o.created_at, c.action
        FROM wiki_operation_changes c JOIN wiki_operations o ON o.id = c.operation_id
        WHERE c.path = ? ORDER BY o.created_at DESC LIMIT 20
        """,
        (path,),
    )
    if decoded["space"] == "wiki":
        decoded["provenance"] = database.fetch_all(
            """
            SELECT DISTINCT s.source_path AS path
            FROM wiki_operation_changes c
            JOIN wiki_operation_sources s ON s.operation_id = c.operation_id
            WHERE c.path = ? ORDER BY s.source_path
            """,
            (path,),
        )
    elif decoded["space"] == "ingest":
        if ingest_indexer:
            decoded.update(
                ingest_indexer.metadata_for_path(
                    path, decoded.get("mtime_ns"), decoded.get("indexed_at")
                )
            )
        decoded.update(source_integration_payload(database, path))
    return decoded


def source_integration_payload(database: Database, path: str) -> dict[str, Any]:
    integration = database.fetch_one(
        """
        SELECT s.operation_id, s.integrated_at, o.title, o.status
        FROM source_integrations s JOIN wiki_operations o ON o.id = s.operation_id
        WHERE s.source_path = ?
        """,
        (path,),
    )
    pages = database.fetch_all(
        """
        SELECT DISTINCT c.path FROM source_integrations s
        JOIN wiki_operation_changes c ON c.operation_id = s.operation_id
        WHERE s.source_path = ? AND c.path NOT IN ('wiki/index.md', 'wiki/log.md')
        ORDER BY c.path
        """,
        (path,),
    )
    return {
        "integration_status": "integrated" if integration else "unprocessed",
        "integration": integration,
        "wiki_pages": pages,
    }


def resolve_knowledge_entry(workspace: Path, raw_path: str) -> tuple[Path, str]:
    raw = unquote(raw_path).replace("\\", "/").strip("/")
    if not raw:
        raise HTTPException(status_code=400, detail="A relative path is required")
    if space_for_path(raw) not in KNOWLEDGE_SPACES:
        raise HTTPException(
            status_code=400,
            detail="Paths must stay below manual/, ingest/, or wiki/",
        )
    candidate = (workspace / raw).resolve()
    try:
        relative_path = candidate.relative_to(workspace.resolve())
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Path must stay inside the workspace") from error
    if any(
        part.startswith(".") or part in EXCLUDED_PARTS
        for part in relative_path.parts
    ) or candidate.name in EXCLUDED_FILES:
        raise HTTPException(status_code=400, detail="That path is reserved by the application")
    return candidate, relative_path.as_posix()


def knowledge_directories(workspace: Path) -> list[dict[str, str]]:
    directories: list[dict[str, str]] = []
    for space in KNOWLEDGE_SPACES:
        root = workspace / space
        if not root.is_dir() or root.is_symlink():
            continue
        for current, names, _ in os.walk(root, followlinks=False):
            current_path = Path(current)
            names[:] = [
                name
                for name in names
                if not name.startswith(".")
                and name not in EXCLUDED_PARTS
                and not (current_path / name).is_symlink()
            ]
            for name in names:
                path = current_path / name
                relative = path.relative_to(workspace).as_posix()
                directories.append({"path": relative, "space": space})
    return sorted(directories, key=lambda item: item["path"].casefold())


app = create_app()
