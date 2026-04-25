from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from packages.shared_contracts.schemas import QueueItem, QueueState, utc_now


class InMemoryRuntimeState:
    """Redis-compatible runtime state for demo and tests."""

    def __init__(self) -> None:
        self._queues: Dict[str, QueueState] = {}
        self._dedupe: Set[str] = set()
        self._recent_tracks: Dict[str, List[str]] = {}
        self._strings: Dict[str, str] = {}

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
        return list(self._recent_tracks.get(session_id, []))

    def append_recent_track(self, session_id: str, track_id: str, limit: int = 50) -> None:
        recent = [track_id, *[item for item in self._recent_tracks.get(session_id, []) if item != track_id]]
        self._recent_tracks[session_id] = recent[:limit]

    def get_string(self, key: str) -> Optional[str]:
        return self._strings.get(key)

    def set_string(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        self._strings[key] = value


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
        pipe.execute()
        return state

    def remember_once(self, namespace: str, identifier: str) -> bool:
        return bool(self.client.set(f"{namespace}:{identifier}", "1", nx=True, ex=60 * 60 * 24))

    def recent_track_ids(self, session_id: str) -> List[str]:
        return [str(item) for item in self.client.lrange(f"sess:{session_id}:recent_tracks", 0, 49)]

    def append_recent_track(self, session_id: str, track_id: str, limit: int = 50) -> None:
        key = f"sess:{session_id}:recent_tracks"
        pipe = self.client.pipeline()
        pipe.lrem(key, 0, track_id)
        pipe.lpush(key, track_id)
        pipe.ltrim(key, 0, limit - 1)
        pipe.execute()

    def get_string(self, key: str) -> Optional[str]:
        return self.client.get(key)

    def set_string(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        if ttl_seconds:
            self.client.set(key, value, ex=ttl_seconds)
        else:
            self.client.set(key, value)

    @staticmethod
    def _queue_key(session_id: str) -> str:
        return f"sess:{session_id}:queue"
