from __future__ import annotations

from typing import Dict, Iterable, List, Set

from packages.shared_contracts.schemas import QueueItem, QueueState, utc_now


class InMemoryRuntimeState:
    """Redis-compatible runtime state for demo and tests."""

    def __init__(self) -> None:
        self._queues: Dict[str, QueueState] = {}
        self._dedupe: Set[str] = set()

    def get_queue(self, session_id: str) -> QueueState:
        return self._queues.get(
            session_id,
            QueueState(items=[], generated_at=utc_now(), drawer_default_open=False, revision=1),
        )

    def set_queue(self, session_id: str, items: Iterable[QueueItem], fallback_level: str = "none") -> QueueState:
        current = self.get_queue(session_id)
        state = QueueState(
            items=list(items),
            fallback_level=fallback_level,
            generated_at=utc_now(),
            drawer_default_open=False,
            revision=current.revision + 1,
        )
        self._queues[session_id] = state
        return state

    def remember_once(self, namespace: str, identifier: str) -> bool:
        key = f"{namespace}:{identifier}"
        if key in self._dedupe:
            return False
        self._dedupe.add(key)
        return True

    def recent_track_ids(self, session_id: str) -> List[str]:
        return [item.track_id for item in self.get_queue(session_id).items]


class RedisRuntimeState:
    """Redis-backed runtime queue and event dedupe state."""

    def __init__(self, redis_url: str) -> None:
        import redis

        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def get_queue(self, session_id: str) -> QueueState:
        raw = self.client.get(self._queue_key(session_id))
        if not raw:
            return QueueState(items=[], generated_at=utc_now(), drawer_default_open=False, revision=1)
        return QueueState.parse_raw(raw)

    def set_queue(self, session_id: str, items: Iterable[QueueItem], fallback_level: str = "none") -> QueueState:
        current = self.get_queue(session_id)
        state = QueueState(
            items=list(items),
            fallback_level=fallback_level,
            generated_at=utc_now(),
            drawer_default_open=False,
            revision=current.revision + 1,
        )
        pipe = self.client.pipeline()
        pipe.set(self._queue_key(session_id), state.json())
        track_ids = [item.track_id for item in state.items]
        if track_ids:
            pipe.sadd(f"sess:{session_id}:recent_tracks", *track_ids)
        pipe.execute()
        return state

    def remember_once(self, namespace: str, identifier: str) -> bool:
        return bool(self.client.set(f"{namespace}:{identifier}", "1", nx=True, ex=60 * 60 * 24))

    def recent_track_ids(self, session_id: str) -> List[str]:
        return [str(item) for item in self.client.smembers(f"sess:{session_id}:recent_tracks")]

    @staticmethod
    def _queue_key(session_id: str) -> str:
        return f"sess:{session_id}:queue"
