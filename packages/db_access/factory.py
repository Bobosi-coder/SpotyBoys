from __future__ import annotations

from typing import Tuple

from packages.config import AppConfig
from packages.db_access.demo_bootstrap import get_demo_components
from packages.db_access.postgres import PostgresRepository
from packages.db_access.repositories import DemoRepository
from packages.db_access.runtime_state import InMemoryRuntimeState, RedisRuntimeState


def build_repository_and_runtime(config: AppConfig):
    if config.runtime_mode in {"postgres", "compose", "production"}:
        repository = PostgresRepository(config.database_url)
        repository.seed_from_fixture(config.fixture_path, config.user_id, config.session_id)
        runtime_state = RedisRuntimeState(config.redis_url)
        return repository, runtime_state
    return get_demo_components()

