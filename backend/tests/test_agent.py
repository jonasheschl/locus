import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_service import CODEX_BASE_URL, WikiAgent, tool_activity, tool_arguments, tool_output_failed
from app.auth import CodexAuth
from app.config import Settings
from app.database import Database
from app.ingest import IngestIndexer
from app.indexer import NoteIndexer
from app.operations import OperationManager, ensure_wiki_contract


class RawToolCall:
    def __init__(self, arguments: str):
        self.raw_item = {"arguments": arguments}


def test_parallel_wiki_writes_share_one_atomic_operation(tmp_path: Path) -> None:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    ensure_wiki_contract(tmp_path)
    note_indexer = NoteIndexer(tmp_path, database)
    note_indexer.scan()
    service = WikiAgent(
        database,
        note_indexer,
        IngestIndexer(tmp_path, database),
        OperationManager(tmp_path, database, note_indexer),
        CodexAuth(database),
        "gpt-test",
    )
    state = {"id": None}
    built, client = service._build_agent(
        {"access_token": "test-token", "account_id": "test-account"},
        operation_state=state,
        source_paths={"manual/Ideas.md"},
        operation_title="Parallel integration",
    )
    write_tool = next(tool for tool in built.tools if tool.name == "write_wiki_page")

    def write_page(number: int):
        context = SimpleNamespace(tool_name=write_tool.name, run_config=None, context=None)
        return asyncio.run(
            write_tool.on_invoke_tool(
                context,
                json.dumps(
                    {
                        "path": f"parallel/page-{number}.md",
                        "content": f"# Page {number}\n",
                        "operation_kind": "ingest",
                    }
                ),
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write_page, range(8)))
    finally:
        asyncio.run(client.close())

    assert state["id"] is not None
    assert database.fetch_one("SELECT COUNT(*) AS count FROM wiki_operations")["count"] == 1
    assert database.fetch_one(
        "SELECT COUNT(*) AS count FROM wiki_operation_changes WHERE operation_id = ?",
        (state["id"],),
    )["count"] == 8


def test_tool_activity_describes_actions_without_payloads() -> None:
    item = RawToolCall('{"path":"wiki/Agents.md","content":"private page body"}')
    arguments = tool_arguments(item)

    assert arguments["path"] == "wiki/Agents.md"
    assert tool_activity("write_wiki_page", arguments) == {
        "label": "Updating wiki/Agents.md",
        "detail": "",
        "kind": "write_wiki_page",
    }
    assert "private page body" not in str(tool_activity("write_wiki_page", arguments))
    assert tool_activity("search_sources", {"query": "agent trust"})["label"] == "Searching source notes"
    assert tool_activity("work_in_workspace", {"commands": ["rg -n awareness /knowledge"]}) == {
        "label": "Working in the source workspace",
        "detail": "rg -n awareness /knowledge",
        "kind": "work_in_workspace",
    }
    assert tool_output_failed('{"error":"Path not found"}') is True
    assert tool_output_failed('{"path":"wiki/Agents.md"}') is False


@pytest.mark.asyncio
async def test_agent_constructs_codex_responses_model(tmp_path: Path) -> None:
    settings = Settings(
        workspace=tmp_path,
        database=tmp_path / "wiki.sqlite3",
        frontend_dist=tmp_path / ".frontend",
        model="gpt-test",
        scan_interval_seconds=60,
    )
    database = Database(settings.database)
    database.initialize()
    ensure_wiki_contract(settings.workspace)
    note_indexer = NoteIndexer(settings.workspace, database)
    note_indexer.scan()
    operations = OperationManager(settings.workspace, database, note_indexer)
    service = WikiAgent(
        database,
        note_indexer,
        IngestIndexer(settings.workspace, database),
        operations,
        CodexAuth(database),
        settings.model,
    )

    agent, client = service._build_agent(
        {"access_token": "test-token", "account_id": "test-account"}
    )
    try:
        assert agent.model.model == "gpt-test"
        assert agent.model_settings.reasoning.effort == "medium"
        assert len(agent.tools) == 6
        assert {tool.name for tool in agent.tools} == {
            "search_wiki",
            "search_sources",
            "read_path",
            "lint_wiki",
            "write_wiki_page",
            "update_manual_note",
        }
        assert str(client.base_url) == f"{CODEX_BASE_URL}/"
        assert client.default_headers["chatgpt-account-id"] == "test-account"
        assert "x-codex-routing-hint" not in client.default_headers
    finally:
        await client.close()

    review_agent, review_client = service._build_agent(
        {"access_token": "test-token", "account_id": "test-account"},
        write_mode="review",
    )
    try:
        assert {tool.name for tool in review_agent.tools} == {
            "search_wiki",
            "search_sources",
            "read_path",
            "lint_wiki",
        }
        assert "source-discussion turn" in review_agent.instructions
    finally:
        await review_client.close()

    with database.write() as connection:
        connection.executemany(
            "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, 'now')",
            [
                ("model", "gpt-5.6-terra"),
                ("reasoning_effort", "high"),
                ("fast_mode", "true"),
            ],
        )
    configured, configured_client = service._build_agent(
        {"access_token": "test-token", "account_id": "test-account"}
    )
    try:
        assert configured.model.model == "gpt-5.6-terra"
        assert configured.model_settings.reasoning.effort == "high"
        assert configured.model_settings.extra_args == {"service_tier": "priority"}
        assert configured_client.default_headers["x-codex-routing-hint"] == (
            "model=gpt-5.6-terra;tier=priority"
        )
    finally:
        await configured_client.close()


def test_chat_messages_preserve_attached_source_context(tmp_path: Path) -> None:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    ensure_wiki_contract(tmp_path)
    note_indexer = NoteIndexer(tmp_path, database)
    note_indexer.scan()
    service = WikiAgent(
        database,
        note_indexer,
        IngestIndexer(tmp_path, database),
        OperationManager(tmp_path, database, note_indexer),
        CodexAuth(database),
        "gpt-test",
    )
    thread = service.create_thread("Discuss a source")

    service._append_message(
        thread["id"],
        "user",
        "What should we keep?",
        ["ingest/paper.pdf"],
    )
    service._append_message(thread["id"], "assistant", "Let's examine the claims.")

    messages = service.messages(thread["id"])
    assert messages[0]["context_paths"] == ["ingest/paper.pdf"]
    assert messages[1]["context_paths"] == []


@pytest.mark.asyncio
async def test_automatic_manual_integration_consumes_streamed_codex_response(
    tmp_path: Path, monkeypatch
) -> None:
    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    ensure_wiki_contract(tmp_path)
    (tmp_path / "manual" / "Ideas.md").write_text(
        "# Ideas\n\nAutomatic source content.\n", encoding="utf-8"
    )
    note_indexer = NoteIndexer(tmp_path, database)
    note_indexer.scan()
    service = WikiAgent(
        database,
        note_indexer,
        IngestIndexer(tmp_path, database),
        OperationManager(tmp_path, database, note_indexer),
        CodexAuth(database),
        "gpt-test",
    )
    captured = {}

    async def valid_credential():
        return {"access_token": "test-token", "account_id": "test-account"}

    class FakeClient:
        closed = False

        async def close(self):
            self.closed = True

    class FakeResult:
        final_output = "Integrated the changed Manual note."

        async def stream_events(self):
            yield object()

    fake_client = FakeClient()

    def build_agent(credential, **kwargs):
        captured["source_paths"] = kwargs["source_paths"]
        return object(), fake_client

    def run_streamed(agent, *, input, **kwargs):
        captured["prompt"] = input
        return FakeResult()

    monkeypatch.setattr(service.auth, "valid_credential", valid_credential)
    monkeypatch.setattr(service, "_build_agent", build_agent)
    monkeypatch.setattr("app.agent_service.Runner.run_streamed", run_streamed)

    result = await service.integrate_manual_changes(
        [
            {
                "path": "manual/Ideas.md",
                "title": "Ideas",
                "content_hash": database.fetch_one(
                    "SELECT content_hash FROM notes WHERE path='manual/Ideas.md'"
                )["content_hash"],
                "change": "modified",
            }
        ]
    )

    assert result == {
        "operation_id": None,
        "summary": "Integrated the changed Manual note.",
    }
    assert captured["source_paths"] == {"manual/Ideas.md"}
    assert "[[manual/Ideas.md]]" in captured["prompt"]
    assert "Automatic source content" in captured["prompt"]
    assert fake_client.closed is True
