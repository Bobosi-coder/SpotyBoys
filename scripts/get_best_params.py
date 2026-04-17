"""
scripts/get_best_params.py — Fetch best Phase 1 hyperparams from MLflow

Queries the "training before online service" experiment for the run with
the highest composite score (0.5*NDCG5 + 0.3*HR5 + 0.2*MRR5) and prints
shell-sourceable export statements.

Usage (from retrain.sh):
  eval $(python3 scripts/get_best_params.py)
  # → sets BEST_BATCH_SIZE, BEST_LR, BEST_DROPOUT, BEST_EPOCHS

Standalone:
  python3 scripts/get_best_params.py
"""
import os
import sys

import mlflow

EXPERIMENT = "training before online service"


def composite(run) -> float:
    m = run.data.metrics
    return 0.5 * m.get("NDCG5", 0) + 0.3 * m.get("HR5", 0) + 0.2 * m.get("MRR5", 0)


def main() -> None:
    client = mlflow.MlflowClient()

    exp = client.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        print(f"ERROR: MLflow experiment '{EXPERIMENT}' not found", file=sys.stderr)
        sys.exit(1)

    runs = client.search_runs(
        experiment_ids=[exp.experiment_id],
        filter_string="attributes.status = 'FINISHED'",
        order_by=["metrics.NDCG5 DESC"],
        max_results=50,
    )

    if not runs:
        print(f"ERROR: No finished runs in experiment '{EXPERIMENT}'", file=sys.stderr)
        sys.exit(1)

    best_run = max(runs, key=composite)
    p = best_run.data.params
    m = best_run.data.metrics

    batch_size = int(p.get("batch_size", 4096))
    lr         = float(p.get("lr", batch_size / 4096 * 2.4e-3))
    dropout    = float(p.get("dropout", 0.1))
    epochs     = int(p.get("epochs", 3))
    run_id     = best_run.info.run_id

    score = composite(best_run)
    print(f"# Best run: {run_id}  composite={score:.4f}  NDCG5={m.get('NDCG5', 0):.4f}",
          file=sys.stderr)

    # Shell-sourceable output
    print(f"export BEST_BATCH_SIZE={batch_size}")
    print(f"export BEST_LR={lr}")
    print(f"export BEST_DROPOUT={dropout}")
    print(f"export BEST_EPOCHS={epochs}")
    print(f"export BEST_RUN_ID={run_id}")


if __name__ == "__main__":
    main()
