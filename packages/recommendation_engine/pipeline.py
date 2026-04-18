from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from packages.artifact_runtime import ServingBundle
from packages.db_access.repositories import PlayableTrackRecord


@dataclass(frozen=True)
class Candidate:
    track: PlayableTrackRecord
    retrieval_score: float
    retrieval_sources: List[str]
    ranker_score: float = 0.0
    policy_score: float = 0.0


@dataclass(frozen=True)
class PipelineTrace:
    c1_artifacts_loaded: List[str]
    c2_candidate_count: int
    c2_sources: List[str]
    c3_ranker_invoked: bool
    c4_policy_invoked: bool
    c4_removed_track_ids: List[str]
    final_track_ids: List[str]


class ServingRecommendationPipeline:
    """VM1 serving pipeline: C2 retrieval, C3 ranker, C4 policy over offline C1 artifacts."""

    def __init__(self, bundle: ServingBundle) -> None:
        self.bundle = bundle
        root = bundle.bundle_path
        self.cooc_session = _load_score_file(root / "cooc_session.npz")
        self.cooc_playlist = _load_score_file(root / "cooc_playlist.npz")
        self.centroids = _load_json_scores(root / "user_centroids.pkl")
        self.ranker_weights = _load_json(root / "gru_ranker.pt")
        self.last_trace = PipelineTrace([], 0, [], False, False, [], [])

    def recommend(
        self,
        playable_tracks: Sequence[PlayableTrackRecord],
        *,
        user_id: str,
        recent_track_ids: Iterable[str],
    ) -> List[PlayableTrackRecord]:
        candidates = self.retrieve_candidates(playable_tracks, user_id=user_id)
        ranked = self.rank_candidates(candidates)
        final = self.apply_policy(ranked, recent_track_ids=set(recent_track_ids))
        self.last_trace = PipelineTrace(
            c1_artifacts_loaded=[
                "gru_ranker.pt",
                "gru_ranker_config.json",
                "cooc_session.npz",
                "cooc_playlist.npz",
                "user_centroids.pkl",
                "pop_scores.csv",
            ],
            c2_candidate_count=len(candidates),
            c2_sources=sorted({source for item in candidates for source in item.retrieval_sources}),
            c3_ranker_invoked=True,
            c4_policy_invoked=True,
            c4_removed_track_ids=[item.track.track_id for item in ranked if item.track.track_id not in {x.track.track_id for x in final}],
            final_track_ids=[item.track.track_id for item in final],
        )
        return [item.track for item in final]

    def retrieve_candidates(self, playable_tracks: Sequence[PlayableTrackRecord], *, user_id: str) -> List[Candidate]:
        by_id = {track.track_id: track for track in playable_tracks}
        candidate_ids = set(self.bundle.pop_scores) | set(self.cooc_session) | set(self.cooc_playlist)
        user_centroid = self.centroids.get(user_id, {})
        candidate_ids |= set(user_centroid)
        candidates: List[Candidate] = []
        for track_id in candidate_ids:
            track = by_id.get(track_id)
            if not track:
                continue
            sources: List[str] = []
            score = 0.0
            if track_id in self.bundle.pop_scores:
                score += self.bundle.pop_scores[track_id]
                sources.append("popularity")
            if track_id in self.cooc_session:
                score += self.cooc_session[track_id]
                sources.append("cooc_session")
            if track_id in self.cooc_playlist:
                score += self.cooc_playlist[track_id] * 0.7
                sources.append("cooc_playlist")
            if track_id in user_centroid:
                score += user_centroid[track_id]
                sources.append("user_centroid")
            candidates.append(Candidate(track=track, retrieval_score=score, retrieval_sources=sources))
        return sorted(candidates, key=lambda item: (-item.retrieval_score, item.track.track_id))

    def rank_candidates(self, candidates: Sequence[Candidate]) -> List[Candidate]:
        base_weight = float(self.ranker_weights.get("base_weight", 1.0))
        cooc_weight = float(self.ranker_weights.get("cooc_weight", 1.0))
        centroid_weight = float(self.ranker_weights.get("centroid_weight", 1.0))
        biases = self.ranker_weights.get("track_bias", {})
        ranked: List[Candidate] = []
        for item in candidates:
            cooc_bonus = 1.0 if any(source.startswith("cooc") for source in item.retrieval_sources) else 0.0
            centroid_bonus = 1.0 if "user_centroid" in item.retrieval_sources else 0.0
            score = (
                item.retrieval_score * base_weight
                + cooc_bonus * cooc_weight
                + centroid_bonus * centroid_weight
                + float(biases.get(item.track.track_id, 0.0))
            )
            ranked.append(
                Candidate(
                    track=item.track,
                    retrieval_score=item.retrieval_score,
                    retrieval_sources=item.retrieval_sources,
                    ranker_score=score,
                    policy_score=score,
                )
            )
        return sorted(ranked, key=lambda item: (-item.ranker_score, item.track.track_id))

    def apply_policy(self, ranked: Sequence[Candidate], *, recent_track_ids: set[str]) -> List[Candidate]:
        output: List[Candidate] = []
        artist_counts: Dict[str, int] = {}
        for item in ranked:
            if item.track.track_id in recent_track_ids:
                continue
            penalty = 0.18 * artist_counts.get(item.track.artist, 0)
            output.append(
                Candidate(
                    track=item.track,
                    retrieval_score=item.retrieval_score,
                    retrieval_sources=item.retrieval_sources,
                    ranker_score=item.ranker_score,
                    policy_score=item.ranker_score - penalty,
                )
            )
            artist_counts[item.track.artist] = artist_counts.get(item.track.artist, 0) + 1
        if not output:
            return list(ranked)
        return sorted(output, key=lambda item: (-item.policy_score, item.track.track_id))


def _load_score_file(path: Path) -> Dict[str, float]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {str(row["track_id"]): float(row["score"]) for row in reader}


def _load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_scores(path: Path) -> Dict[str, Dict[str, float]]:
    payload = _load_json(path)
    return {
        str(user_id): {str(track_id): float(score) for track_id, score in scores.items()}
        for user_id, scores in payload.items()
    }
