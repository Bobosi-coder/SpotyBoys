from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectStorageLayout:
    bucket: str = "proj23-mlflow-artifacts"

    def retrieve_prefix(self, version: str) -> str:
        return f"Retrieve/{version}/"

    def serving_bundle_prefix(self, version: str) -> str:
        return f"Real_service/{version}/"

    def item2vec_prefix(self) -> str:
        return "Item2vec/"

    def session_snapshot_prefix(self) -> str:
        return "session_event/snapshot/"

    def session_delta_prefix(self, version: str) -> str:
        return f"session_event/delta/{version}/"

