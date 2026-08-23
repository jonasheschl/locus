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

    @classmethod
    def from_env(cls) -> "Settings":
        workspace = Path(os.getenv("WIKI_WORKSPACE", "/workspace")).resolve()
        database = Path(os.getenv("WIKI_DATABASE", str(workspace / "wiki.sqlite3"))).resolve()
        frontend_dist = Path(os.getenv("WIKI_FRONTEND_DIST", "/app/frontend/dist")).resolve()
        return cls(
            workspace=workspace,
            database=database,
            frontend_dist=frontend_dist,
            model=os.getenv("WIKI_MODEL", "gpt-5.5"),
            scan_interval_seconds=float(os.getenv("WIKI_SCAN_INTERVAL", "3")),
        )


settings = Settings.from_env()
