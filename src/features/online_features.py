from __future__ import annotations

from typing import Any


def _normalize_labels(session_labels: list[str]) -> list[str]:
    return [str(label).lower() for label in session_labels]


def build_online_feature_summary(
    *,
    user_id: int,
    session_track_ids: list[int],
    session_labels: list[str],
    seed_candidate_ids: list[int],
    candidate_pool_ids: list[int],
    candidate_source: str,
    retriever_enabled: bool,
    ranker_enabled: bool,
    ranker_used: bool,
) -> dict[str, Any]:
    labels = _normalize_labels(session_labels)
    positive_count = sum(label == "positive" for label in labels)
    neutral_count = sum(label == "neutral" for label in labels)
    skip_count = sum(label == "skip" for label in labels)

    return {
        "user_known": int(user_id) > 0,
        "prefix_len": len(session_track_ids),
        "recent_session_tracks": session_track_ids[-5:],
        "label_counts": {
            "positive": positive_count,
            "neutral": neutral_count,
            "skip": skip_count,
            "unknown": max(0, len(labels) - positive_count - neutral_count - skip_count),
        },
        "num_seed_candidates": len(seed_candidate_ids),
        "candidate_count": len(candidate_pool_ids),
        "candidate_source": candidate_source,
        "retriever_enabled": retriever_enabled,
        "ranker_enabled": ranker_enabled,
        "ranker_used": ranker_used,
    }
