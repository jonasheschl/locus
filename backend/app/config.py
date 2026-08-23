from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace: Path
    database: Path
    frontend_dist: Path
    model: str
    scan_interval_seconds: float
    manual_integration_interval_seconds: float = 60.0
    contract: Path | None = None
    agent_workspace: Path | None = None
    workspace_runtime_socket: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        workspace = Path(os.getenv("WIKI_WORKSPACE", "/workspace")).resolve()
        database = Path(os.getenv("WIKI_DATABASE", str(workspace / "wiki.sqlite3"))).resolve()
        frontend_dist = Path(os.getenv("WIKI_FRONTEND_DIST", "/app/frontend/dist")).resolve()
        manual_interval = float(os.getenv("WIKI_MANUAL_INTEGRATION_INTERVAL", "60"))
        return cls(
            workspace=workspace,
            database=database,
            frontend_dist=frontend_dist,
            model=os.getenv("WIKI_MODEL", "gpt-5.5"),
            scan_interval_seconds=float(os.getenv("WIKI_SCAN_INTERVAL", "3")),
            manual_integration_interval_seconds=min(60.0, max(1.0, manual_interval)),
            contract=Path(os.getenv("WIKI_CONTRACT", "/app/AGENTS.md")).resolve(),
            agent_workspace=Path(
                os.getenv(
                    "WIKI_AGENT_WORKSPACE",
                    str(workspace / "locus" / "agent-workspace"),
                )
            ).resolve(),
            workspace_runtime_socket=Path(
                os.getenv(
                    "WIKI_WORKSPACE_RUNTIME_SOCKET",
                    "/run/locus-agent/runtime.sock",
                )
            ).resolve(),
        )


settings = Settings.from_env()
