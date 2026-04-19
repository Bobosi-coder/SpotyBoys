from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from packages.artifact_runtime.manifest_validator import validate_serving_bundle_directory


@dataclass(frozen=True)
class ServingBundle:
    bundle_path: Path
    version: str
    model_version: str
    pop_scores: Dict[str, float]

    @classmethod
    def load(cls, bundle_path: str | Path) -> "ServingBundle":
        bundle = Path(bundle_path)
        manifest = validate_serving_bundle_directory(bundle)
        return cls(
            bundle_path=bundle,
            version=str(manifest["version"]),
            model_version=str(manifest["model_version"]),
            pop_scores=_load_pop_scores(bundle / "pop_scores.csv"),
        )

    def ranked_track_ids(self) -> List[str]:
        return [
            track_id
            for track_id, _score in sorted(
                self.pop_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]


def _load_pop_scores(path: Path) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        score_column = "score" if "score" in fieldnames else "pop_score" if "pop_score" in fieldnames else ""
        if "track_id" not in fieldnames or not score_column:
            raise ValueError("pop_scores.csv must contain track_id and score/pop_score columns")
        for row in reader:
            scores[str(row["track_id"])] = float(row[score_column])
    if not scores:
        raise ValueError("pop_scores.csv must contain at least one scored track")
    return scores
