from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from openai.types.responses import ResponseTextDeltaEvent
from openai.types.shared import Reasoning

from .auth import CodexAuth
from .database import Database
from .ingest import IngestIndexer
from .indexer import NoteIndexer
from .operations import OperationManager


CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

SYSTEM_INSTRUCTIONS = """
You are Locus, the compiler and maintainer of a private, persistent Markdown Wiki.

The Wiki is the compounding artifact; chat is only the working interface. Manual notes are
first-party sources written by the owner. Ingest files are immutable external sources. Wiki pages
are your maintained synthesis. Never rewrite Ingest. Do not change Manual during normal work.

For ordinary questions, read wiki/index.md and search Wiki first. Consult raw sources when the
Wiki is incomplete, the user asks about a source, or claims need verification. Cite exact paths in
double brackets. State gaps and contradictions rather than smoothing them over.

When integrating, update the durable concept/entity/comparison pages that should improve—not one
summary page per chat. Cross-link related Wiki pages and cite Manual/Ingest provenance. A single
source may legitimately update many pages. The application rebuilds index.md and appends log.md
after your operation, so do not write those special files yourself.

Decide autonomously which workflow the conversation needs; never ask the user to select a mode.
Ordinary questions and source reviews are read-only. Write only when the user explicitly asks to
integrate, file, update, repair, or otherwise persist a change. Use lint_wiki whenever structural
health matters. For Wiki writes, classify the operation as ingest when compiling knowledge from
sources and maintain when repairing existing Wiki structure. Manual edits are exceptional: use
update_manual_note only when the user explicitly names the Manual note and asks to change it.

Every write tool starts or joins one atomic, auditable operation. Before writing, inspect the
existing target pages and relevant sources. Preserve uncertainty and source disagreements.
""".strip()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class WikiAgent:
    def __init__(
        self,
        database: Database,
        indexer: NoteIndexer,
        ingest_indexer: IngestIndexer,
        operations: OperationManager,
        auth: CodexAuth,
        model_name: str,
    ):
        self.database = database
        self.indexer = indexer
        self.ingest_indexer = ingest_indexer
        self.operations = operations
        self.auth = auth
        self.model_name = model_name

    def preferences(self) -> tuple[str, str, bool]:
        rows = {
            row["key"]: row["value"]
            for row in self.database.fetch_all(
                "SELECT key, value FROM runtime_settings WHERE key IN ('model', 'reasoning_effort', 'fast_mode')"
            )
        }
        return (
            rows.get("model", self.model_name),
            rows.get("reasoning_effort", "medium"),
            rows.get("fast_mode", "false").casefold() == "true",
        )

    def create_thread(self, title: str = "New inquiry") -> dict[str, str]:
        thread_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO chat_threads(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (thread_id, title[:100] or "New inquiry", now, now),
            )
        return {"id": thread_id, "title": title[:100] or "New inquiry", "created_at": now}

    def threads(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            """
            SELECT t.id, t.title, t.created_at, t.updated_at,
                   COUNT(m.id) AS message_count
            FROM chat_threads t LEFT JOIN chat_messages m ON m.thread_id = t.id
            GROUP BY t.id ORDER BY t.updated_at DESC LIMIT 30
            """
        )

    def messages(self, thread_id: str) -> list[dict[str, Any]]:
        self._require_thread(thread_id)
        return self.database.fetch_all(
            "SELECT id, role, content, created_at FROM chat_messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )

    def delete_thread(self, thread_id: str) -> None:
        with self.database.write() as connection:
            connection.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))

    async def stream_answer(
        self,
        question: str,
        thread_id: str | None,
        current_note: str | None = None,
        context_paths: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        question = question.strip()
        if not question:
            raise ValueError("Question cannot be empty")
        if not thread_id:
            thread = self.create_thread(self._thread_title(question))
            thread_id = thread["id"]
            yield {"type": "thread", "thread": thread}
        else:
            self._require_thread(thread_id)

        operation_state: dict[str, str | None] = {"id": None}
        client: AsyncOpenAI | None = None
        selected_paths = list(dict.fromkeys((context_paths or []) + ([current_note] if current_note else [])))
        source_paths = set(selected_paths)
        try:
            credential = await self.auth.valid_credential()
            self._append_message(thread_id, "user", question)
            agent, client = self._build_agent(
                credential,
                thread_id=thread_id,
                operation_state=operation_state,
                source_paths=source_paths,
                operation_title=self._thread_title(question),
            )
            history = self.database.fetch_all(
                """
                SELECT role, content FROM chat_messages
                WHERE thread_id = ? ORDER BY id DESC LIMIT 18
                """,
                (thread_id,),
            )
            history.reverse()
            model_input: list[dict[str, Any]] = [
                {"role": item["role"], "content": item["content"]} for item in history
            ]
            wiki_index = self.database.fetch_one(
                "SELECT content FROM notes WHERE path='wiki/index.md'"
            ) or {"content": "# Wiki Index\n\n_No compiled pages yet._"}
            context_note = (
                "\n\nAttached paths: "
                + (", ".join(f"[[{path}]]" for path in selected_paths) or "none")
                + "\n\nCompiled Wiki entry point ([[wiki/index.md]]):\n"
                + wiki_index["content"][:20_000]
            )
            model_input[-1] = {"role": "user", "content": question + context_note}

            yield {"type": "status", "message": "Reading the Wiki and deciding what the conversation needs…"}
            result = Runner.run_streamed(
                agent,
                input=model_input,
                max_turns=12,
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    workflow_name="Locus Wiki conversation",
                    group_id=thread_id,
                ),
            )
            emitted = ""
            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    emitted += event.data.delta
                    yield {"type": "delta", "delta": event.data.delta}
                elif event.type == "run_item_stream_event":
                    item_type = getattr(event.item, "type", "")
                    if item_type == "tool_call_item":
                        yield {"type": "status", "message": "Following the Wiki trail…"}

            final_output = str(result.final_output or emitted).strip()
            if not final_output:
                final_output = "I couldn't produce an answer from the knowledge base."
            self._append_message(thread_id, "assistant", final_output)
            yield {"type": "done", "thread_id": thread_id, "content": final_output}
            if operation_state["id"]:
                receipt = self.operations.complete(operation_state["id"], final_output)
                yield {"type": "operation", "operation": receipt}
        except Exception as error:
            if operation_state["id"]:
                self.operations.fail(operation_state["id"], str(error))
            raise
        finally:
            if client:
                await client.close()

    def _build_agent(
        self,
        credential: dict[str, Any],
        *,
        thread_id: str | None = None,
        operation_state: dict[str, str | None] | None = None,
        source_paths: set[str] | None = None,
        operation_title: str = "Wiki update",
    ) -> tuple[Agent[Any], AsyncOpenAI]:
        state = operation_state if operation_state is not None else {"id": None}
        observed_sources = source_paths if source_paths is not None else set()

        def ensure_operation(kind: str) -> str:
            if kind not in {"ingest", "maintain", "manual-edit"}:
                raise ValueError("Operation kind must be ingest, maintain, or manual-edit")
            if not state["id"]:
                state["id"] = self.operations.start(kind, operation_title, thread_id)
                for source_path in sorted(observed_sources):
                    self.operations.record_source(state["id"], source_path)
            return state["id"]

        @function_tool
        def search_wiki(query: str, limit: int = 8) -> str:
            """Search the compiled Wiki. This is the first search tool for ordinary questions."""
            safe_limit = max(1, min(limit, 12))
            return json.dumps(
                self.indexer.search(query, safe_limit, {"wiki"}), ensure_ascii=False
            )

        @function_tool
        def search_sources(query: str, limit: int = 8) -> str:
            """Search Manual and Ingest raw sources when Wiki needs verification or is incomplete."""
            safe_limit = max(1, min(limit, 12))
            results = self.indexer.search(query, safe_limit * 2, {"manual", "ingest"})
            results.extend(self.ingest_indexer.search(query, safe_limit * 2))
            results.sort(key=lambda item: item["score"], reverse=True)
            return json.dumps(results[:safe_limit], ensure_ascii=False)

        @function_tool
        def read_path(path: str) -> str:
            """Read a Wiki page, Manual note, or extracted Ingest item by exact relative path."""
            note = self.database.fetch_one(
                "SELECT path, space, title, content, tags_json FROM notes WHERE path = ?", (path,)
            )
            if note:
                content = note["content"]
                payload = {
                    "path": note["path"],
                    "space": note["space"],
                    "title": note["title"],
                    "tags": json.loads(note["tags_json"]),
                    "content": content,
                }
                if note["space"] in {"manual", "ingest"}:
                    observed_sources.add(path)
                    self.operations.record_source(state["id"], path)
            else:
                item = self.database.fetch_one(
                    """
                    SELECT path, title, source_type, media_type, content, extraction_error
                    FROM ingest_items WHERE path = ?
                    """,
                    (path,),
                )
                if not item:
                    return json.dumps({"error": "Path not found", "path": path})
                content = item["content"]
                payload = {**item, "space": "ingest"}
                observed_sources.add(path)
                self.operations.record_source(state["id"], path)
            if len(content) > 40_000:
                content = content[:40_000] + "\n\n[Content truncated by the reader]"
            payload["content"] = content
            return json.dumps(payload, ensure_ascii=False)

        @function_tool
        def lint_wiki() -> str:
            """Inspect broken links, orphans, missing provenance/index entries, and raw backlog."""
            return json.dumps(self.operations.lint(), ensure_ascii=False)

        @function_tool
        def write_wiki_page(path: str, content: str, operation_kind: str) -> str:
            """Create or replace a Wiki page. operation_kind is ingest for source compilation or maintain for Wiki repair."""
            if operation_kind not in {"ingest", "maintain"}:
                raise ValueError("Wiki operation_kind must be ingest or maintain")
            operation_id = ensure_operation(operation_kind)
            return json.dumps(
                self.operations.write_wiki(operation_id, path, content), ensure_ascii=False
            )

        @function_tool
        def update_manual_note(path: str, content: str) -> str:
            """Replace a named Manual note only after the user explicitly requested that exact edit."""
            operation_id = ensure_operation("manual-edit")
            return json.dumps(
                self.operations.write_manual(operation_id, path, content), ensure_ascii=False
            )

        tools: list[Any] = [
            search_wiki,
            search_sources,
            read_path,
            lint_wiki,
            write_wiki_page,
            update_manual_note,
        ]

        schema_path = self.indexer.workspace / "AGENTS.md"
        schema = schema_path.read_text(encoding="utf-8", errors="replace") if schema_path.exists() else ""
        instructions = "\n\n".join(
            [SYSTEM_INSTRUCTIONS, "Workspace contract:\n" + schema[:20_000]]
        )
        client = AsyncOpenAI(
            api_key=credential["access_token"],
            base_url=CODEX_BASE_URL,
            default_headers={
                "chatgpt-account-id": credential["account_id"],
                "OpenAI-Beta": "responses=experimental",
                "originator": "locus-wiki",
                "User-Agent": "locus-wiki/1.0",
            },
        )
        selected_model, reasoning_effort, fast_mode = self.preferences()
        model = OpenAIResponsesModel(model=selected_model, openai_client=client)
        return (
            Agent(
                name="Locus",
                instructions=instructions,
                model=model,
                model_settings=ModelSettings(
                    store=False,
                    reasoning=Reasoning(effort=reasoning_effort),
                    extra_args={"service_tier": "fast"} if fast_mode else None,
                ),
                tools=tools,
            ),
            client,
        )

    def _require_thread(self, thread_id: str) -> None:
        if not self.database.fetch_one("SELECT id FROM chat_threads WHERE id = ?", (thread_id,)):
            raise KeyError("Chat thread not found")

    def _append_message(self, thread_id: str, role: str, content: str) -> None:
        now = utc_now()
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO chat_messages(thread_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, role, content, now),
            )
            connection.execute(
                "UPDATE chat_threads SET updated_at = ? WHERE id = ?", (now, thread_id)
            )

    @staticmethod
    def _thread_title(question: str) -> str:
        first_line = question.splitlines()[0].strip()
        return first_line if len(first_line) <= 70 else first_line[:69].rstrip() + "…"
