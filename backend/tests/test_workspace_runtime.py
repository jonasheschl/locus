from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.ingest import IngestIndexer
from app.workspace_runtime import create_workspace_runtime
from app.workspace_shell import WorkspaceShell


def test_workspace_runtime_executes_in_its_persistent_root(tmp_path: Path) -> None:
    workspace = tmp_path / "agent"
    with TestClient(create_workspace_runtime(workspace)) as client:
        response = client.post(
            "/execute",
            json={"commands": ["pwd; printf source > note.txt"], "timeout_ms": 5_000},
        )

    assert response.status_code == 200
    output = response.json()["outputs"][0]
    assert output["exit_code"] == 0
    assert output["stdout"].strip() == str(workspace)
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "source"


@pytest.mark.asyncio
async def test_shell_imports_completed_outbox_files_into_ingest(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    source_folder = knowledge / "ingest" / "situational-awareness"
    source_folder.mkdir(parents=True)
    primary = source_folder / "introduction.html"
    primary.write_text("<html><body>Introduction.</body></html>", encoding="utf-8")
    agent_workspace = tmp_path / "agent"
    outbox = agent_workspace / "outbox" / "ingest" / "series"
    outbox.mkdir(parents=True)
    (outbox / "chapter.html").write_text(
        "<html><head><title>Chapter</title></head><body>Full chapter text.</body></html>",
        encoding="utf-8",
    )
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    ingest_indexer = IngestIndexer(knowledge, database)
    ingest_indexer.scan()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/execute"
        return httpx.Response(
            200,
            request=request,
            json={
                "outputs": [
                    {
                        "command": "true",
                        "stdout": "done",
                        "stderr": "",
                        "outcome": "exit",
                        "exit_code": 0,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://workspace"
    ) as client:
        shell = WorkspaceShell(
            tmp_path / "unused.sock",
            agent_workspace,
            knowledge,
            ingest_indexer,
            client=client,
        )
        result = await shell.run(
            ["true"],
            5_000,
            20_000,
            {"ingest/situational-awareness/introduction.html"},
        )

    target = knowledge / "ingest" / "situational-awareness" / "chapter.html"
    assert target.is_file()
    assert not (outbox / "chapter.html").exists()
    assert "ingest/situational-awareness/chapter.html" in result["outputs"][0]["stdout"]
    assert database.fetch_one(
        "SELECT content FROM ingest_items WHERE path = ?",
        ("ingest/situational-awareness/chapter.html",),
    )["content"].endswith("Full chapter text.")
