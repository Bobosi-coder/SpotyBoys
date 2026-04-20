from __future__ import annotations

import csv
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

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
    c4_disliked_track_ids: List[str]
    final_track_ids: List[str]


class ServingRecommendationPipeline:
    """VM1 serving pipeline: C2 retrieval, C3 ranker, C4 policy over offline C1 artifacts."""

    def __init__(self, bundle: ServingBundle, *, require_full_runtime: bool = False) -> None:
        self.bundle = bundle
        self.require_full_runtime = require_full_runtime
        root = bundle.bundle_path
        self.real_retriever = None
        self.real_ranker = None
        self.real_runtime_error: str | None = None
        self.cooc_session = _load_score_file(root / "cooc_session.npz")
        self.cooc_playlist = _load_score_file(root / "cooc_playlist.npz")
        self.centroids = _load_json_scores(root / "user_centroids.pkl")
        self.ranker_weights = _load_json(root / "gru_ranker.pt")
        self._try_load_real_runtime(root)
        if self.require_full_runtime and (not self.real_retriever or not self.real_ranker):
            raise RuntimeError(
                "Full C1-C4 ML pipeline is required, but the production C2/C3 runtime did not load: "
                f"{self.real_runtime_error or 'unknown runtime load error'}"
            )
        self.last_trace = PipelineTrace([], 0, [], False, False, [], [], [])

    def recommend(
        self,
        playable_tracks: Sequence[PlayableTrackRecord],
        *,
        user_id: str,
        recent_track_ids: Iterable[str],
        disliked_track_ids: Iterable[str] = (),
    ) -> List[PlayableTrackRecord]:
        real_output = self._recommend_with_real_runtime(
            playable_tracks,
            user_id=user_id,
            recent_track_ids=list(recent_track_ids),
            disliked_track_ids=set(disliked_track_ids),
        )
        if real_output is not None:
            if self.require_full_runtime and not real_output:
                raise RuntimeError(
                    "Full C1-C4 ML pipeline returned zero playable recommendations. "
                    "Check that catalog sync uses the same canonical track IDs as the trained artifacts."
                )
            return real_output
        if self.require_full_runtime:
            raise RuntimeError(
                "Full C1-C4 ML pipeline is required, but real C2/C3 produced no playable recommendations. "
                "Check that playable canonical track IDs overlap the trained artifact track ID namespace."
            )
        candidates = self.retrieve_candidates(playable_tracks, user_id=user_id)
        ranked = self.rank_candidates(candidates)
        disliked = set(disliked_track_ids)
        final = self.apply_policy(ranked, recent_track_ids=set(recent_track_ids), disliked_track_ids=disliked)
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
            c4_disliked_track_ids=sorted(disliked),
            final_track_ids=[item.track.track_id for item in final],
        )
        return [item.track for item in final]

    def _try_load_real_runtime(self, root: Path) -> None:
        runtime_root = root.parents[1] / "runtime" if len(root.parents) > 1 else root / "runtime"
        item2vec_dir = runtime_root / "item2vec"
        retriever_dir = runtime_root / "retriever"
        if not item2vec_dir.exists() or not retriever_dir.exists():
            return
        try:
            from src.ranker.ranker import GRURankerInference
            from src.retriever.retriever import MultiRecallRetriever

            self.real_retriever = MultiRecallRetriever(
                artifacts_dir=str(retriever_dir),
                processed_dir=str(item2vec_dir),
            )
            self.real_ranker = GRURankerInference(
                artifacts_dir=str(root),
                i2v_dir=str(item2vec_dir),
                retriever_dir=str(retriever_dir),
                item_embeddings=self.real_retriever.emb,
                track_to_row=self.real_retriever.t2r,
                user_centroids=self.real_retriever.user_centroids,
            )
            self.real_runtime_error = None
        except Exception as exc:
            self.real_retriever = None
            self.real_ranker = None
            self.real_runtime_error = f"{type(exc).__name__}: {exc}"

    def _recommend_with_real_runtime(
        self,
        playable_tracks: Sequence[PlayableTrackRecord],
        *,
        user_id: str,
        recent_track_ids: Sequence[str],
        disliked_track_ids: set[str],
    ) -> Optional[List[PlayableTrackRecord]]:
        if not self.real_retriever or not self.real_ranker:
            return None
        playable_by_id = {str(track.track_id): track for track in playable_tracks}
        user_int = _safe_int(user_id)
        recent_ints = [_safe_int(track_id) for track_id in recent_track_ids]
        recent_ints = [track_id for track_id in recent_ints if track_id is not None]
        try:
            retrieved = self.real_retriever.retrieve(
                user_int or 0,
                recent_ints,
                ["neutral"] * len(recent_ints),
            )
            candidate_ids = [track_id for track_id, _score in retrieved]
            ranked = self.real_ranker.score(
                user_int or 0,
                recent_ints,
                ["neutral"] * len(recent_ints),
                candidate_ids,
            )
        except Exception as exc:
            if self.require_full_runtime:
                raise RuntimeError(f"Full C1-C4 ML pipeline recommendation failed: {type(exc).__name__}: {exc}") from exc
            return None
        output: List[PlayableTrackRecord] = []
        seen_artists: Dict[str, int] = {}
        for track_id, _score in ranked:
            key = str(track_id)
            if key in disliked_track_ids or key in recent_track_ids:
                continue
            track = playable_by_id.get(key)
            if not track:
                continue
            artist_count = seen_artists.get(track.artist, 0)
            if artist_count > 1:
                continue
            output.append(track)
            seen_artists[track.artist] = artist_count + 1
            if len(output) >= 18:
                break
        if not output:
            if self.require_full_runtime:
                return []
            return None
        self.last_trace = PipelineTrace(
            c1_artifacts_loaded=[
                "Item2vec/item2vec_128d.npy",
                "Item2vec/item2vec_track_to_row.json",
                "gru_ranker.pt",
                "gru_ranker_config.json",
                "cooc_session.npz",
                "cooc_playlist.npz",
                "user_centroids.pkl",
                "pop_scores.csv",
            ],
            c2_candidate_count=len(candidate_ids),
            c2_sources=["cooc_session", "cooc_playlist", "popularity", "user_centroid"],
            c3_ranker_invoked=True,
            c4_policy_invoked=True,
            c4_removed_track_ids=[],
            c4_disliked_track_ids=sorted(disliked_track_ids),
            final_track_ids=[track.track_id for track in output],
        )
        return output

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

    def apply_policy(
        self,
        ranked: Sequence[Candidate],
        *,
        recent_track_ids: set[str],
        disliked_track_ids: set[str] | None = None,
    ) -> List[Candidate]:
        disliked_track_ids = disliked_track_ids or set()
        output: List[Candidate] = []
        artist_counts: Dict[str, int] = {}
        for item in ranked:
            if item.track.track_id in recent_track_ids or item.track.track_id in disliked_track_ids:
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
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return {str(row["track_id"]): float(row["score"]) for row in reader}
    except UnicodeDecodeError:
        return {}


def _load_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return {}


def _load_json_scores(path: Path) -> Dict[str, Dict[str, float]]:
    payload = _load_json(path)
    if not payload:
        try:
            with path.open("rb") as handle:
                raw = pickle.load(handle)
            return {str(user_id): {} for user_id in raw}
        except Exception:
            return {}
    return {
        str(user_id): {str(track_id): float(score) for track_id, score in scores.items()}
        for user_id, scores in payload.items()
    }


def _safe_int(value: str) -> Optional[int]:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None
