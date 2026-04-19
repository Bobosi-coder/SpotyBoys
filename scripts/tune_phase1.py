"""
scripts/tune_phase1.py — Phase 1 fixed-config training runs

Runs two sequential full training jobs with the best known hyperparameters,
differing only in epoch count (3 and 5). Both runs are logged to MLflow
experiment "training before online service".

Fixed hyperparameters:
  batch_size:   8192
  lr:           4.8e-3   (= 8192/4096 * 2.4e-3, linear scaling rule)
  dropout:      0.1
  weight_decay: 1e-4
  bpr_weight:   0.5
  n_layers:     2
  max_norm:     1.0

Usage:
  python3 scripts/tune_phase1.py
"""
import logging

from src.ranker.train import run as train_run

EXPERIMENT = "training before online service"

FIXED = dict(
    batch_size   = 8192,
    lr           = 4.8e-3,
    dropout      = 0.1,
    weight_decay = 1e-4,
    bpr_weight   = 0.5,
    n_layers     = 2,
    max_norm     = 1.0,
    device       = "auto",
    experiment   = EXPERIMENT,
)

EPOCH_CONFIGS = [3, 5]

log = logging.getLogger("tune_phase1")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for epochs in EPOCH_CONFIGS:
        run_name = f"fixed_e{epochs}_b8192_d0.1_wd1e-4_bpr0.5"
        log.info("=" * 60)
        log.info(f"Starting run: {run_name}  (epochs={epochs})")
        log.info("=" * 60)
        train_run(
            run_name=run_name,
            epochs=epochs,
            **FIXED,
        )
        log.info(f"Run {run_name} complete.")

    log.info("All Phase 1 runs finished.")
    log.info(f"MLflow experiment: '{EXPERIMENT}'")


if __name__ == "__main__":
    main()
