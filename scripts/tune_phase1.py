"""
scripts/tune_phase1.py — Ray Tune Phase 1 hyperparameter sweep

Runs 6 trials with ASHA early stopping on a single GPU.
Batch sizes: 8192 / 16384 / 32768 with linear LR scaling.
Results logged to MLflow experiment "training before online service".

Usage:
  python3 scripts/tune_phase1.py
  python3 scripts/tune_phase1.py --num-samples 2 --max-epochs 2   # smoke test
"""
import argparse
import logging
import os

import ray
import ray.train
from ray import tune
from ray.tune.schedulers import ASHAScheduler

from src.ranker.train import run as train_run

EXPERIMENT = "training before online service"

log = logging.getLogger("tune_phase1")


def train_trial(config: dict) -> None:
    lr = config["batch_size"] / 4096 * 2.4e-3

    def _report(metrics: dict) -> None:
        ray.train.report(metrics)

    train_run(
        experiment=EXPERIMENT,
        run_name=f"tune_b{config['batch_size']}_d{config['dropout']}",
        epochs=config["max_epochs"],
        batch_size=config["batch_size"],
        lr=lr,
        weight_decay=1e-4,
        max_norm=1.0,
        device="auto",
        n_layers=2,
        dropout=config["dropout"],
        report_callback=_report,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Ray Tune Phase 1 sweep")
    parser.add_argument("--num-samples", type=int, default=6,
                        help="Number of Ray Tune trials")
    parser.add_argument("--max-epochs",  type=int, default=5,
                        help="Max epochs per trial (ASHA may stop earlier)")
    args = parser.parse_args()

    ray.init(num_gpus=1)

    search_space = {
        "batch_size": tune.choice([8192, 16384, 32768]),
        "dropout":    tune.choice([0.1, 0.2]),
        "max_epochs": args.max_epochs,
    }

    scheduler = ASHAScheduler(
        max_t=args.max_epochs,
        grace_period=1,
        reduction_factor=2,
        metric="val_ndcg5",
        mode="max",
    )

    tuner = tune.Tuner(
        tune.with_resources(train_trial, {"gpu": 1}),
        param_space=search_space,
        tune_config=tune.TuneConfig(
            scheduler=scheduler,
            num_samples=args.num_samples,
            metric="val_ndcg5",
            mode="max",
        ),
    )

    results = tuner.fit()
    best = results.get_best_result()
    log.info(f"Best trial config: {best.config}")
    log.info(f"Best val_ndcg5:   {best.metrics.get('val_ndcg5')}")

    ray.shutdown()


if __name__ == "__main__":
    main()
