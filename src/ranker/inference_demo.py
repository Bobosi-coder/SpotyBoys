"""
GRU Ranker Inference Demo

Picks one real context from ranker_val.parquet, runs the ranker,
and writes two JSON files:
  artifacts/ranker/demo_input.json   — the input (user, session, candidates)
  artifacts/ranker/demo_output.json  — the ranked output (track_id, score, rank)

CLI:
  uv run python -m src.ranker.inference_demo
  uv run python -m src.ranker.inference_demo --context-index 42
"""
import argparse
import json
import logging
import os

import pandas as pd

from src.ranker.ranker import GRURankerInference

OUT_DIR = "artifacts/ranker"
VAL_PARQUET = os.path.join(OUT_DIR, "ranker_val.parquet")

LABEL_DEC = {0: "positive", 1: "neutral", 2: "skip", 3: "pad"}

log = logging.getLogger("ranker.demo")


def main(context_index: int) -> None:
    # ── Load val parquet ──────────────────────────────────────────────────────
    log.info(f"Loading {VAL_PARQUET} ...")
    df = pd.read_parquet(VAL_PARQUET)

    # Each context has 6 rows; find the context_id for the requested index
    context_ids = df["context_id"].unique()
    if context_index >= len(context_ids):
        raise ValueError(
            f"context_index {context_index} out of range "
            f"(val has {len(context_ids)} contexts)"
        )
    ctx_id = int(context_ids[context_index])
    rows = df[df["context_id"] == ctx_id].reset_index(drop=True)

    # ── Extract input fields from the positive row ────────────────────────────
    pos_row = rows[rows["is_positive"] == True].iloc[0]

    user_id    = int(pos_row["user_id"])
    prefix_ids = [int(x) for x in pos_row["prefix_ids"]]    # left-padded len=20
    prefix_labs = [int(x) for x in pos_row["prefix_labels"]]
    prefix_len = int(pos_row["prefix_len"])

    # Strip left padding (storage is left-padded, inference_demo shows real tracks)
    real_prefix_ids  = prefix_ids[-prefix_len:]
    real_prefix_labs = [LABEL_DEC.get(l, "pad") for l in prefix_labs[-prefix_len:]]

    # All 6 candidates (1 positive + 5 negatives)
    candidates = [int(r["candidate_id"]) for _, r in rows.iterrows()]
    ground_truth_id = int(pos_row["candidate_id"])

    # ── Build input JSON ──────────────────────────────────────────────────────
    input_data = {
        "context_id":        ctx_id,
        "user_id":           user_id,
        "session_prefix": {
            "track_ids":  real_prefix_ids,
            "labels":     real_prefix_labs,
            "length":     prefix_len,
        },
        "candidates":        candidates,
        "ground_truth_id":   ground_truth_id,
    }

    # ── Run ranker ────────────────────────────────────────────────────────────
    log.info("Loading ranker model ...")
    ranker = GRURankerInference()

    log.info(f"Scoring {len(candidates)} candidates for user={user_id} ...")
    ranked = ranker.score(user_id, real_prefix_ids, real_prefix_labs, candidates)

    # ── Build output JSON ─────────────────────────────────────────────────────
    output_data = {
        "context_id":      ctx_id,
        "user_id":         user_id,
        "ground_truth_id": ground_truth_id,
        "ranked_candidates": [
            {
                "rank":       rank + 1,
                "track_id":   int(tid),
                "score":      round(float(score), 6),
                "is_ground_truth": int(tid) == ground_truth_id,
            }
            for rank, (tid, score) in enumerate(ranked)
        ],
    }

    # Ground truth rank
    gt_rank = next(
        item["rank"] for item in output_data["ranked_candidates"]
        if item["is_ground_truth"]
    )
    output_data["ground_truth_rank"] = gt_rank
    output_data["ground_truth_in_top5"] = gt_rank <= 5

    # ── Write files ───────────────────────────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    input_path  = os.path.join(OUT_DIR, "demo_input.json")
    output_path = os.path.join(OUT_DIR, "demo_output.json")

    with open(input_path, "w") as f:
        json.dump(input_data, f, indent=2)
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    log.info(f"Input  → {input_path}")
    log.info(f"Output → {output_path}")
    log.info(
        f"Ground truth track {ground_truth_id} "
        f"ranked #{gt_rank} out of {len(candidates)}"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="GRU Ranker inference demo")
    parser.add_argument(
        "--context-index", type=int, default=0,
        help="Which context to pick from ranker_val.parquet (0-indexed, default: 0)"
    )
    args = parser.parse_args()
    main(args.context_index)
