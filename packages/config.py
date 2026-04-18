from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppConfig:
    runtime_mode: str
    database_url: str
    redis_url: str
    fixture_path: Path
    session_id: str
    user_id: str


def load_config() -> AppConfig:
    return AppConfig(
        runtime_mode=os.environ.get("SPOTIBOYS_RUNTIME_MODE", "fixture").strip().lower(),
        database_url=os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/spotiboys",
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        fixture_path=Path(os.environ.get("SPOTIBOYS_FIXTURE_PATH", str(PROJECT_ROOT / "fixtures" / "demo_catalog.json"))),
        session_id=os.environ.get("SPOTIBOYS_SESSION_ID", "sess_demo"),
        user_id=os.environ.get("SPOTIBOYS_USER_ID", "user_demo"),
    )
