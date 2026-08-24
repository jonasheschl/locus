import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.web_ingest import DownloadedWebSource


def app_for(tmp_path: Path):
    manual = tmp_path / "manual"
    manual.mkdir()
    (manual / "First.md").write_text("# First note\n\nA useful starting point.", encoding="utf-8")
    frontend = tmp_path / ".frontend"
    frontend.mkdir()
    return create_app(
        Settings(
            workspace=tmp_path,
            database=tmp_path / "wiki.sqlite3",
            frontend_dist=frontend,
            model="gpt-test",
            scan_interval_seconds=60,
        )
    )


def test_frontend_serves_pwa_files_and_keeps_spa_fallback(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    frontend = tmp_path / ".frontend"
    (frontend / "index.html").write_text("<h1>Locus shell</h1>", encoding="utf-8")
    (frontend / "manifest.webmanifest").write_text(
        '{"name":"Locus"}', encoding="utf-8"
    )
    (frontend / "service-worker.js").write_text(
        "self.addEventListener('fetch', () => {});", encoding="utf-8"
    )

    with TestClient(app) as client:
        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.headers["content-type"].startswith("application/manifest+json")
        assert manifest.headers["cache-control"] == "no-cache"
        assert manifest.json()["name"] == "Locus"

        worker = client.get("/service-worker.js")
        assert worker.status_code == 200
        assert worker.headers["cache-control"] == "no-cache"
        assert worker.headers["service-worker-allowed"] == "/"

        fallback = client.get("/some/client/route")
        assert fallback.status_code == 200
        assert "Locus shell" in fallback.text


def test_health_notes_search_and_graph(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        assert client.get("/api/health").json()["notes"] == 3
        notes = client.get("/api/notes").json()
        assert any(note["title"] == "First note" for note in notes["notes"])
        assert client.get("/api/search", params={"q": "starting"}).json()["results"][0]["path"] == "manual/First.md"
        assert len(client.get("/api/graph").json()["nodes"]) == 3
        assert client.get("/api/wiki/schema").json()["path"] == "AGENTS.md"
        assert client.get("/api/wiki/lint").json()["pages"] == 0


def test_create_and_edit_note(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        created = client.post(
            "/api/notes", json={"path": "manual/Ideas/New idea.md", "content": "# New idea\n\nDraft"}
        )
        assert created.status_code == 201
        assert (tmp_path / "manual" / "Ideas" / "New idea.md").exists()

        updated = client.put(
            "/api/notes/manual%2FIdeas%2FNew%20idea.md", json={"content": "# Better idea\n\nRevised"}
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Better idea"


def test_rejects_workspace_traversal(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        response = client.post(
            "/api/notes", json={"path": "../outside.md", "content": "no"}
        )
        assert response.status_code == 400
        assert client.post(
            "/api/notes", json={"path": "loose.md", "content": "no"}
        ).status_code == 400


def test_manual_spreadsheet_upload_is_indexed_searchable_and_viewable(
    tmp_path: Path,
) -> None:
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/manual/spreadsheet",
            data={"folder": "manual/research"},
            files={
                "file": (
                    "experiments.csv",
                    b"Experiment,Result\nAgent trust,Promising\n",
                    "text/csv",
                )
            },
        )

        assert uploaded.status_code == 201
        assert uploaded.json()["path"] == "manual/research/experiments.csv"
        assert uploaded.json()["kind"] == "spreadsheet"
        viewed = client.get(
            "/api/spreadsheets/manual%2Fresearch%2Fexperiments.csv"
        )
        assert viewed.status_code == 200
        assert viewed.json()["sheets"][0]["rows"][1] == ["Agent trust", "Promising"]
        results = client.get("/api/search", params={"q": "promising"}).json()[
            "results"
        ]
        assert results[0]["path"] == "manual/research/experiments.csv"


def test_ingest_upload_url_and_space_file_view(tmp_path: Path, monkeypatch) -> None:
    async def fake_download(url: str) -> DownloadedWebSource:
        return DownloadedWebSource(
            original_url=url,
            final_url=url,
            content=b"<html><head><title>Example report</title></head><body>Archived web knowledge about agent trust.</body></html>",
            media_type="text/html",
            extension=".html",
            title="Example report",
        )

    monkeypatch.setattr("app.main.download_web_source", fake_download)
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/ingest/upload",
            files={"file": ("research.txt", b"Raw external research about agent trust.", "text/plain")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["space"] == "ingest"
        assert uploaded.json()["kind"] == "asset"
        assert uploaded.json()["path"] == "ingest/research/research.txt"
        assert uploaded.json()["ingest_group"] == "ingest/research"
        assert uploaded.json()["ingested_at"]

        downloaded = client.post(
            "/api/ingest/url",
            json={"url": "https://example.com/report", "title": "Example report"},
        )
        assert downloaded.status_code == 201
        assert downloaded.json()["path"] == "ingest/example-report/example-report.html"
        assert (tmp_path / "ingest" / "example-report" / "example-report.html").read_bytes().startswith(
            b"<html>"
        )
        item = client.get(
            "/api/ingest/items/ingest%2Fexample-report%2Fexample-report.html"
        ).json()
        assert item["source_url"] == "https://example.com/report"
        assert "Archived web knowledge" in item["content"]

        files = client.get("/api/files").json()["files"]
        assert {item["space"] for item in files} == {"manual", "ingest", "wiki"}
        assert next(item for item in files if item["path"] == "ingest/research/research.txt")[
            "integration_status"
        ] == "unprocessed"
        ingest_directories = [
            item
            for item in client.get("/api/files").json()["directories"]
            if item["space"] == "ingest"
        ]
        assert {item["path"] for item in ingest_directories} >= {
            "ingest/research",
            "ingest/example-report",
        }
        assert all(
            item["ingested_at"]
            for item in ingest_directories
            if item["path"] in {"ingest/research", "ingest/example-report"}
        )
        assert client.get("/api/search", params={"q": "external research"}).json()["results"][0]["space"] == "ingest"
        assert client.get("/api/search", params={"q": "archived web knowledge"}).json()["results"][0]["path"] == "ingest/example-report/example-report.html"

        chat = client.post(
            "/api/chat",
            json={"question": "Review https://example.com/automatic-source"},
        )
        assert chat.status_code == 200
        activity_events = [
            json.loads(line)
            for line in chat.text.splitlines()
            if line and json.loads(line).get("type") == "activity"
        ]
        assert activity_events[:2] == [
            {
                "type": "activity",
                "activity": {
                    "id": "download:1",
                    "label": "Downloading website into Ingest",
                    "detail": "https://example.com/automatic-source",
                    "kind": "download_url",
                    "status": "running",
                },
            },
            {
                "type": "activity",
                "activity": {
                    "id": "download:1",
                    "label": "Downloaded ingest/example-report-2/example-report.html",
                    "detail": "https://example.com/automatic-source",
                    "kind": "download_url",
                    "status": "completed",
                },
            },
        ]
        assert (
            tmp_path / "ingest" / "example-report-2" / "example-report.html"
        ).is_file()


def test_ingest_markdown_is_immutable_through_notes_api(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/ingest/upload",
            files={"file": ("source.md", b"# Untouched source\n", "text/markdown")},
        )
        assert uploaded.status_code == 201
        response = client.put(
            f"/api/notes/{uploaded.json()['path'].replace('/', '%2F')}",
            json={"content": "# Rewritten\n"},
        )
        assert response.status_code == 403
        assert (tmp_path / uploaded.json()["path"]).read_text() == "# Untouched source\n"


def test_unprocessed_ingest_context_requires_review_transition(
    tmp_path: Path, monkeypatch
) -> None:
    observed_modes = []

    async def fake_stream_answer(
        _self,
        question,
        thread_id,
        current_note=None,
        context_paths=None,
        write_mode="auto",
    ):
        observed_modes.append(write_mode)
        yield {"type": "done", "thread_id": thread_id or "test", "content": question}

    monkeypatch.setattr("app.main.WikiAgent.stream_answer", fake_stream_answer)
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/ingest/upload",
            files={"file": ("paper.txt", b"A claim to discuss.", "text/plain")},
        ).json()

        reviewed = client.post(
            "/api/chat",
            json={
                "question": "Process this source",
                "context_paths": [uploaded["path"]],
                "write_mode": "auto",
            },
        )
        integrated = client.post(
            "/api/chat",
            json={
                "question": "Integrate our agreed direction",
                "context_paths": [uploaded["path"]],
                "write_mode": "integrate",
            },
        )

    assert reviewed.status_code == 200
    assert integrated.status_code == 200
    assert observed_modes == ["review", "integrate"]


def test_file_tree_directory_rename_and_delete_actions(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        folder = client.post("/api/files/directories", json={"path": "manual/Projects"})
        assert folder.status_code == 201
        assert {item["path"] for item in client.get("/api/files").json()["directories"]} >= {
            "manual/Projects"
        }

        moved_folder = client.post(
            "/api/files/move",
            json={
                "source_path": "manual/Projects",
                "target_path": "manual/Work",
                "is_directory": True,
            },
        )
        assert moved_folder.status_code == 200
        assert moved_folder.json()["path"] == "manual/Work"

        note = client.post(
            "/api/notes", json={"path": "manual/Work/Draft.md", "content": "# Draft\n"}
        )
        assert note.status_code == 201
        assert client.delete("/api/directories/manual%2FWork").status_code == 409

        renamed = client.post(
            "/api/files/move",
            json={
                "source_path": "manual/Work/Draft.md",
                "target_path": "manual/Work/Final.md",
                "is_directory": False,
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["path"] == "manual/Work/Final.md"
        assert not (tmp_path / "manual" / "Work" / "Draft.md").exists()

        assert client.delete("/api/files/manual%2FWork%2FFinal.md").status_code == 200
        assert client.delete("/api/directories/manual%2FWork").status_code == 200
        assert not (tmp_path / "manual" / "Work").exists()


def test_file_actions_protect_spaces_and_special_wiki_files(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        crossing = client.post(
            "/api/files/move",
            json={
                "source_path": "manual/First.md",
                "target_path": "wiki/First.md",
                "is_directory": False,
            },
        )
        assert crossing.status_code == 400
        assert client.delete("/api/files/wiki%2Findex.md").status_code == 403


def test_agent_model_and_reasoning_settings(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        assert client.get("/api/auth/codex/usage").status_code == 401
        initial = client.get("/api/settings").json()
        assert initial["model"] == "gpt-test"
        assert initial["reasoning_effort"] == "medium"
        assert initial["fast_mode"] is False
        assert initial["manual_integration"] == {
            "enabled": True,
            "interval_seconds": 60.0,
            "pending": 1,
            "tracked": 0,
            "last_integrated_at": None,
        }

        updated = client.put(
            "/api/settings",
            json={
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "fast_mode": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["model"] == "gpt-5.6-terra"
        assert updated.json()["reasoning_effort"] == "high"
        assert updated.json()["fast_mode"] is True
