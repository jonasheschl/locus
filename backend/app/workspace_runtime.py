from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


MAX_COMMANDS = 8
MAX_COMMAND_CHARACTERS = 20_000
MAX_OUTPUT_CHARACTERS = 100_000
MAX_TIMEOUT_MS = 300_000


class ExecuteRequest(BaseModel):
    commands: list[str] = Field(min_length=1, max_length=MAX_COMMANDS)
    timeout_ms: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_MS)
    max_output_length: int | None = Field(
        default=None, ge=1_000, le=MAX_OUTPUT_CHARACTERS
    )


def _bounded_output(stdout: bytes, stderr: bytes, limit: int) -> tuple[str, str]:
    decoded_stdout = stdout.decode("utf-8", errors="replace")
    decoded_stderr = stderr.decode("utf-8", errors="replace")
    if len(decoded_stdout) + len(decoded_stderr) <= limit:
        return decoded_stdout, decoded_stderr
    stdout_limit = min(len(decoded_stdout), max(limit // 2, limit - len(decoded_stderr)))
    stderr_limit = max(0, limit - stdout_limit)
    suffix = "\n[output truncated by Locus]"
    return (
        decoded_stdout[:stdout_limit] + (suffix if stdout_limit < len(decoded_stdout) else ""),
        decoded_stderr[:stderr_limit] + (suffix if stderr_limit < len(decoded_stderr) else ""),
    )


def create_workspace_runtime(root: Path | None = None) -> FastAPI:
    workspace = (root or Path(os.getenv("LOCUS_AGENT_WORKSPACE", "/workspace"))).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "outbox" / "ingest").mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    app = FastAPI(title="Locus agent workspace runtime", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/execute")
    async def execute(body: ExecuteRequest) -> dict[str, Any]:
        timeout = (body.timeout_ms or 120_000) / 1_000
        output_limit = body.max_output_length or MAX_OUTPUT_CHARACTERS
        outputs: list[dict[str, Any]] = []
        async with lock:
            for command in body.commands:
                if not command.strip() or len(command) > MAX_COMMAND_CHARACTERS or "\0" in command:
                    outputs.append(
                        {
                            "command": command[:200],
                            "stdout": "",
                            "stderr": "The workspace command is empty or too large.",
                            "outcome": "exit",
                            "exit_code": 2,
                        }
                    )
                    continue
                process = await asyncio.create_subprocess_exec(
                    "/bin/sh",
                    "-lc",
                    command,
                    cwd=workspace,
                    env={
                        "HOME": str(workspace),
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "LANG": "C.UTF-8",
                        "LOCUS_KNOWLEDGE": "/knowledge",
                        "LOCUS_OUTBOX": str(workspace / "outbox" / "ingest"),
                    },
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=timeout
                    )
                    outcome = "exit"
                    exit_code = process.returncode
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = await process.communicate()
                    outcome = "timeout"
                    exit_code = None
                bounded_stdout, bounded_stderr = _bounded_output(
                    stdout, stderr, output_limit
                )
                outputs.append(
                    {
                        "command": command,
                        "stdout": bounded_stdout,
                        "stderr": bounded_stderr,
                        "outcome": outcome,
                        "exit_code": exit_code,
                    }
                )
        return {"outputs": outputs, "max_output_length": output_limit}

    return app
app = create_workspace_runtime()
