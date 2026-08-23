from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from agents.tool import (
    ShellCallOutcome,
    ShellCommandOutput,
    ShellCommandRequest,
    ShellResult,
)

from .ingest import IngestIndexer, SUPPORTED_INGEST_EXTENSIONS


MAX_IMPORTED_FILE_BYTES = 50_000_000


class WorkspaceShell:
    """Bridge the SDK shell capability to Locus's isolated, notes-only runtime."""

    def __init__(
        self,
        socket_path: Path,
        agent_workspace: Path,
        knowledge_workspace: Path,
        ingest_indexer: IngestIndexer,
        client: httpx.AsyncClient | None = None,
    ):
        self.socket_path = socket_path
        self.agent_workspace = agent_workspace
        self.knowledge_workspace = knowledge_workspace
        self.ingest_indexer = ingest_indexer
        self.client = client or httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url="http://locus-workspace",
            timeout=httpx.Timeout(310.0, connect=10.0),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def execute(self, request: ShellCommandRequest) -> ShellResult:
        action = request.data.action
        commands = list(action.get("commands") or [])
        timeout_ms = min(int(action.get("timeout_ms") or 120_000), 300_000)
        max_output_length = min(
            int(action.get("max_output_length") or 100_000), 100_000
        )
        payload = await self.run(commands, timeout_ms, max_output_length)
        outputs: list[ShellCommandOutput] = []
        for item in payload.get("outputs", []):
            outcome_type = "timeout" if item.get("outcome") == "timeout" else "exit"
            outputs.append(
                ShellCommandOutput(
                    command=str(item.get("command") or ""),
                    stdout=str(item.get("stdout") or ""),
                    stderr=str(item.get("stderr") or ""),
                    outcome=ShellCallOutcome(
                        type=outcome_type,
                        exit_code=item.get("exit_code") if outcome_type == "exit" else None,
                    ),
                )
            )
        return ShellResult(output=outputs, max_output_length=max_output_length)

    async def run(
        self,
        commands: list[str],
        timeout_ms: int = 120_000,
        max_output_length: int = 100_000,
    ) -> dict[str, Any]:
        timeout_ms = min(max(int(timeout_ms), 1_000), 300_000)
        max_output_length = min(max(int(max_output_length), 1_000), 100_000)
        try:
            response = await self.client.post(
                "/execute",
                json={
                    "commands": commands,
                    "timeout_ms": timeout_ms,
                    "max_output_length": max_output_length,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return {
                "outputs": [
                    {
                        "command": "",
                        "stdout": "",
                        "stderr": f"The isolated Locus workspace is unavailable: {error}",
                        "outcome": "exit",
                        "exit_code": 1,
                    }
                ],
                "max_output_length": max_output_length,
            }

        imported, import_errors = await asyncio.to_thread(self._import_outbox)
        outputs = payload.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = [
                {
                    "command": "",
                    "stdout": "",
                    "stderr": "The isolated workspace returned no command results.",
                    "outcome": "exit",
                    "exit_code": 1,
                }
            ]
            payload["outputs"] = outputs
        notices: list[str] = []
        if imported:
            notices.append(
                "Imported completed outbox files into immutable Ingest:\n"
                + "\n".join(
                    f"- {source} -> {target}" for source, target in imported
                )
            )
        if import_errors:
            notices.append(
                "Outbox files not imported:\n"
                + "\n".join(f"- {message}" for message in import_errors)
            )
        if notices:
            last = outputs[-1]
            last["stdout"] = (
                str(last.get("stdout") or "").rstrip()
                + "\n\n"
                + "\n".join(notices)
            ).strip()
        payload["max_output_length"] = max_output_length
        return payload

    def _import_outbox(self) -> tuple[list[tuple[str, str]], list[str]]:
        outbox = (self.agent_workspace / "outbox" / "ingest").resolve()
        outbox.mkdir(parents=True, exist_ok=True)
        ingest_root = (self.knowledge_workspace / "ingest" / "agent").resolve()
        ingest_root.mkdir(parents=True, exist_ok=True)
        imported: list[tuple[str, str]] = []
        errors: list[str] = []
        for source in sorted(outbox.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(outbox)
            shell_path = f"/workspace/outbox/ingest/{relative.as_posix()}"
            if any(part.startswith(".") for part in relative.parts):
                continue
            if source.suffix.casefold() not in SUPPORTED_INGEST_EXTENSIONS:
                errors.append(f"{shell_path}: unsupported file type")
                continue
            if source.stat().st_size > MAX_IMPORTED_FILE_BYTES:
                errors.append(f"{shell_path}: files are limited to 50 MB")
                continue
            target = self._available_path(ingest_root / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            imported.append(
                (
                    shell_path,
                    target.relative_to(self.knowledge_workspace).as_posix(),
                )
            )
        if imported:
            self.ingest_indexer.scan()
        return imported, errors

    @staticmethod
    def _available_path(path: Path) -> Path:
        if not path.exists():
            return path
        for number in range(2, 10_000):
            candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("Could not allocate a unique Ingest path")
