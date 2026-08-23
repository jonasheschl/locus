from pathlib import Path

import pytest

from app.agent_service import CODEX_BASE_URL, WikiAgent
from app.auth import CodexAuth
from app.config import Settings
from app.database import Database
from app.ingest import IngestIndexer
from app.indexer import NoteIndexer
from app.operations import OperationManager, ensure_wiki_contract


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
    finally:
        await client.close()

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
        assert configured.model_settings.extra_args == {"service_tier": "fast"}
    finally:
        await configured_client.close()
