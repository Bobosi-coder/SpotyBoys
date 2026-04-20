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
    media_mode: str
    music_root: Path
    navidrome_base_url: str
    navidrome_username: str
    navidrome_password: str
    navidrome_token: str
    navidrome_salt: str
    serving_bundle_path: Path
    object_storage_root: Path
    object_storage_endpoint: str
    mlflow_tracking_uri: str
    require_full_ml_pipeline: bool


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
        media_mode=os.environ.get("SPOTIBOYS_MEDIA_MODE", "navidrome_fixture").strip().lower(),
        music_root=Path(os.environ.get("SPOTIBOYS_MUSIC_ROOT", str(PROJECT_ROOT / ".local" / "fixture_music"))),
        navidrome_base_url=os.environ.get("NAVIDROME_BASE_URL", "http://navidrome:4533").rstrip("/"),
        navidrome_username=os.environ.get("NAVIDROME_USERNAME", "spotiboys"),
        navidrome_password=os.environ.get("NAVIDROME_PASSWORD", "spotiboys"),
        navidrome_token=os.environ.get("NAVIDROME_TOKEN", ""),
        navidrome_salt=os.environ.get("NAVIDROME_SALT", ""),
        serving_bundle_path=Path(
            os.environ.get(
                "SPOTIBOYS_SERVING_BUNDLE_PATH",
                str(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1"),
            )
        ),
        object_storage_root=Path(os.environ.get("SPOTIBOYS_OBJECT_STORAGE_ROOT", str(PROJECT_ROOT / ".local" / "object_storage"))),
        object_storage_endpoint=os.environ.get("OBJECT_STORAGE_ENDPOINT", "file://.local/object_storage"),
        mlflow_tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000"),
        require_full_ml_pipeline=_truthy(os.environ.get("SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE", "true")),
    )


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
