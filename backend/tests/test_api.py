from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def app_for(tmp_path: Path):
    (tmp_path / "First.md").write_text("# First note\n\nA useful starting point.", encoding="utf-8")
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


def test_health_notes_search_and_graph(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        assert client.get("/api/health").json()["notes"] == 3
        notes = client.get("/api/notes").json()
        assert any(note["title"] == "First note" for note in notes["notes"])
        assert client.get("/api/search", params={"q": "starting"}).json()["results"][0]["path"] == "First.md"
        assert len(client.get("/api/graph").json()["nodes"]) == 3
        assert client.get("/api/wiki/schema").json()["path"] == "AGENTS.md"
        assert client.get("/api/wiki/lint").json()["pages"] == 0


def test_create_and_edit_note(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        created = client.post(
            "/api/notes", json={"path": "Ideas/New idea.md", "content": "# New idea\n\nDraft"}
        )
        assert created.status_code == 201
        assert (tmp_path / "Ideas" / "New idea.md").exists()

        updated = client.put(
            "/api/notes/Ideas%2FNew%20idea.md", json={"content": "# Better idea\n\nRevised"}
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Better idea"


def test_rejects_workspace_traversal(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        response = client.post(
            "/api/notes", json={"path": "../outside.md", "content": "no"}
        )
        assert response.status_code == 400


def test_ingest_upload_url_and_space_file_view(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/ingest/upload",
            files={"file": ("research.txt", b"Raw external research about agent trust.", "text/plain")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["space"] == "ingest"
        assert uploaded.json()["kind"] == "asset"

        bookmarked = client.post(
            "/api/ingest/url",
            json={"url": "https://example.com/report", "title": "Example report"},
        )
        assert bookmarked.status_code == 201
        assert bookmarked.json()["path"].startswith("ingest/web/example-report")

        files = client.get("/api/files").json()["files"]
        assert {item["space"] for item in files} == {"manual", "ingest", "wiki"}
        assert next(item for item in files if item["path"] == "ingest/research.txt")[
            "integration_status"
        ] == "unprocessed"
        assert client.get("/api/search", params={"q": "external research"}).json()["results"][0]["space"] == "ingest"


def test_ingest_markdown_is_immutable_through_notes_api(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        uploaded = client.post(
            "/api/ingest/upload",
            files={"file": ("source.md", b"# Untouched source\n", "text/markdown")},
        )
        assert uploaded.status_code == 201
        response = client.put(
            "/api/notes/ingest%2Fsource.md", json={"content": "# Rewritten\n"}
        )
        assert response.status_code == 403
        assert (tmp_path / "ingest" / "source.md").read_text() == "# Untouched source\n"


def test_file_tree_directory_rename_and_delete_actions(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        folder = client.post("/api/files/directories", json={"path": "Projects"})
        assert folder.status_code == 201
        assert {item["path"] for item in client.get("/api/files").json()["directories"]} >= {
            "Projects"
        }

        moved_folder = client.post(
            "/api/files/move",
            json={
                "source_path": "Projects",
                "target_path": "Work",
                "is_directory": True,
            },
        )
        assert moved_folder.status_code == 200
        assert moved_folder.json()["path"] == "Work"

        note = client.post(
            "/api/notes", json={"path": "Work/Draft.md", "content": "# Draft\n"}
        )
        assert note.status_code == 201
        assert client.delete("/api/directories/Work").status_code == 409

        renamed = client.post(
            "/api/files/move",
            json={
                "source_path": "Work/Draft.md",
                "target_path": "Work/Final.md",
                "is_directory": False,
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["path"] == "Work/Final.md"
        assert not (tmp_path / "Work" / "Draft.md").exists()

        assert client.delete("/api/files/Work%2FFinal.md").status_code == 200
        assert client.delete("/api/directories/Work").status_code == 200
        assert not (tmp_path / "Work").exists()


def test_file_actions_protect_spaces_and_special_wiki_files(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        crossing = client.post(
            "/api/files/move",
            json={
                "source_path": "First.md",
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
