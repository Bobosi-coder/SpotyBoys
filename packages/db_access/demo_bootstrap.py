from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .repositories import DemoRepository
from .runtime_state import InMemoryRuntimeState

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = PROJECT_ROOT / "fixtures" / "demo_catalog.json"

_repository: Optional[DemoRepository] = None
_runtime_state: Optional[InMemoryRuntimeState] = None


def get_demo_components() -> Tuple[DemoRepository, InMemoryRuntimeState]:
    global _repository, _runtime_state
    if _repository is None:
        _repository = DemoRepository.from_fixture(DEFAULT_FIXTURE)
    if _runtime_state is None:
        _runtime_state = InMemoryRuntimeState()
    return _repository, _runtime_state


def reset_demo_components() -> Tuple[DemoRepository, InMemoryRuntimeState]:
    global _repository, _runtime_state
    _repository = DemoRepository.from_fixture(DEFAULT_FIXTURE)
    _runtime_state = InMemoryRuntimeState()
    return _repository, _runtime_state
