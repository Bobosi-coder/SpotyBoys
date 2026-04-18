from __future__ import annotations

from typing import Iterable, Mapping, Set

REQUIRED_DELTA_FILES = {
    "session_tracks.parquet",
    "session_meta.parquet",
    "love.parquet",
    "manifest.json",
}

REQUIRED_SERVING_BUNDLE_FILES = {
    "gru_ranker.pt",
    "gru_ranker_config.json",
    "cooc_session.npz",
    "cooc_playlist.npz",
    "user_centroids.pkl",
    "pop_scores.csv",
    "manifest.json",
}

FORBIDDEN_VM1_RUNTIME_ARTIFACT_TOKENS = {"faiss", ".index"}


def _as_names(files: Iterable[str]) -> Set[str]:
    return {str(path).strip().split("/")[-1] for path in files}


def validate_delta_manifest(payload: Mapping[str, object]) -> None:
    files = _as_names(payload.get("output_files", []))  # type: ignore[arg-type]
    missing = REQUIRED_DELTA_FILES - files
    if missing:
        raise ValueError("delta manifest missing required files: " + ", ".join(sorted(missing)))


def validate_serving_bundle_manifest(payload: Mapping[str, object]) -> None:
    artifacts = [str(item) for item in payload.get("artifacts", [])]  # type: ignore[arg-type]
    files = _as_names(artifacts)
    missing = REQUIRED_SERVING_BUNDLE_FILES - files
    if missing:
        raise ValueError("serving bundle manifest missing required files: " + ", ".join(sorted(missing)))
    lower_artifacts = " ".join(artifacts).lower()
    if any(token in lower_artifacts for token in FORBIDDEN_VM1_RUNTIME_ARTIFACT_TOKENS):
        raise ValueError("serving bundle manifest must not include FAISS runtime artifacts")
