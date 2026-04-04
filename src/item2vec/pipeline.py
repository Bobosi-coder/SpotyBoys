"""
Item2Vec Pipeline Entry Point

Usage:
  # Full pipeline
  uv run python -m src.data_pre_process.item2vec.pipeline --stages a,b,c,d

  # Individual stages
  uv run python -m src.data_pre_process.item2vec.pipeline --stages a
  uv run python -m src.data_pre_process.item2vec.pipeline --stages b,c

  # VM profile (2 workers)
  MLFLOW_TRACKING_URI=http://<vm-ip>:5000 \\
  uv run python -m src.data_pre_process.item2vec.pipeline --stages a,b,c,d --workers 2
"""
import argparse
import logging
import os
import sys
import time

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/item2vec_pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("item2vec.pipeline")


def parse_args():
    p = argparse.ArgumentParser(description="Item2Vec Embedding Pipeline")
    p.add_argument("--stages", default="a,b,c,d",
                   help="Stages to run, comma-separated: a,b,c,d (default: a,b,c,d)")
    p.add_argument("--vector-size",  type=int,   default=128)
    p.add_argument("--window",       type=int,   default=10)
    p.add_argument("--min-count",    type=int,   default=5)
    p.add_argument("--negative",     type=int,   default=15)
    p.add_argument("--epochs",       type=int,   default=10)
    p.add_argument("--workers",      type=int,   default=8)
    p.add_argument("--mlflow-experiment", default="item2vec-training")
    p.add_argument("--run-name",     default="item2vec-run")
    return p.parse_args()


def main():
    args   = parse_args()
    stages = [s.strip().lower() for s in args.stages.split(",")]

    log.info("=" * 60)
    log.info(f"Item2Vec Pipeline  stages={stages}")
    log.info(f"  vector_size={args.vector_size}  window={args.window}  "
             f"min_count={args.min_count}  negative={args.negative}")
    log.info(f"  epochs={args.epochs}  workers={args.workers}")
    mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "./mlruns")
    log.info(f"  MLFLOW_TRACKING_URI={mlflow_uri}")
    log.info("=" * 60)

    t_start = time.time()
    run_id  = None   # Stage B creates the run; C and D continue it

    try:
        if "a" in stages:
            from item2vec.stage_a_corpus import run as run_a
            log.info("▶ Stage A — Build training corpus")
            stats_a = run_a()
            log.info(f"✓ Stage A done: {stats_a['n_sessions']:,} sessions, "
                     f"{stats_a['n_tokens']:,} tokens")

        if "b" in stages:
            from item2vec.stage_b_train import run as run_b
            log.info("▶ Stage B — Train Item2Vec")
            stats_b = run_b(
                vector_size        = args.vector_size,
                window             = args.window,
                min_count          = args.min_count,
                negative           = args.negative,
                epochs             = args.epochs,
                workers            = args.workers,
                mlflow_experiment  = args.mlflow_experiment,
                run_name           = args.run_name,
            )
            run_id = stats_b["run_id"]
            log.info(f"✓ Stage B done: vocab={stats_b['vocab_size']:,}, "
                     f"coverage={stats_b['coverage_pct']:.1f}%, "
                     f"run_id={run_id}")

        if "c" in stages:
            from item2vec.stage_c_validate import run as run_c
            log.info("▶ Stage C — Validate embeddings")
            stats_c = run_c(
                run_id             = run_id,
                mlflow_experiment  = args.mlflow_experiment,
            )
            log.info(f"✓ Stage C done: sanity_passed={stats_c['sanity_passed']}, "
                     f"same_artist_cosine={stats_c['mean_same_cosine']:.4f}, "
                     f"random_cosine={stats_c['mean_rand_cosine']:.4f}")

        if "d" in stages:
            from item2vec.stage_d_filter import run as run_d
            log.info("▶ Stage D — Filter interaction tables")
            stats_d = run_d(
                run_id             = run_id,
                mlflow_experiment  = args.mlflow_experiment,
            )
            log.info(f"✓ Stage D done: session_tracks={stats_d['st_final']:,}, "
                     f"playlist_tracks={stats_d['pt_final']:,}")

    except Exception as exc:
        log.error(f"Pipeline failed: {exc}", exc_info=True)
        sys.exit(1)

    mins, secs = divmod(int(time.time() - t_start), 60)
    log.info("=" * 60)
    log.info(f"Pipeline complete  {mins}m {secs:02d}s")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
