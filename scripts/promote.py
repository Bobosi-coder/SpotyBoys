"""
scripts/promote.py — Promote best ranker model to Real_service/{VERSION}/

Phase 1 (manual):
  python3 scripts/promote.py --mode manual --retrieve-version 20260417_051148

Phase 2 (automated, called from retrain.sh):
  python3 scripts/promote.py --mode auto --retrieve-version 20260501_120000 --version 20260501_120000

What it does:
  1. Query MLflow for best run in "training before online service" (manual)
     OR use env var BEST_RUN_ID (auto)
  2. Download gru_ranker.pt + gru_ranker_config.json from MLflow artifact store
  3. Copy retriever artifacts from Retrieve/{retrieve_version}/ in S3
  4. Upload all to s3://proj23-mlflow-artifacts/Real_service/{version}/
  5. Write manifest.json with metadata
  6. (manual) Save baseline.json to Real_service/baseline.json for Phase 2 comparison
  7. (auto)   Compare against baseline.json; exit 1 if below threshold
"""
import argparse
import io
import json
import logging
import os
import sys
import time

import boto3
import mlflow
import requests
from botocore import UNSIGNED
from botocore.client import Config

BUCKET   = os.environ.get("ARTIFACT_BUCKET", "proj23-mlflow-artifacts")
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "https://chi.tacc.chameleoncloud.org:7480")
SERVICE_BASE_URL = os.environ.get("SPOTIBOYS_SERVICE_BASE_URL", "").rstrip("/")
SERVICE_ADMIN_TOKEN = os.environ.get("SPOTIBOYS_SERVICE_ADMIN_TOKEN", "")
EXPERIMENT_PHASE1 = "training before online service"
EXPERIMENT_PHASE2 = "retraining after online service"

COMPOSITE_WEIGHTS = {"NDCG5": 0.5, "HR5": 0.3, "MRR5": 0.2}

log = logging.getLogger("promote")


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        verify=False,
    )


def s3_upload_bytes(s3, data: bytes, key: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=data)
    log.info(f"  uploaded → s3://{BUCKET}/{key}")


def s3_upload_file(s3, local_path: str, key: str) -> None:
    s3.upload_file(local_path, BUCKET, key)
    log.info(f"  uploaded → s3://{BUCKET}/{key}")


def s3_copy(s3, src_key: str, dst_key: str) -> None:
    s3.copy_object(
        Bucket=BUCKET, Key=dst_key,
        CopySource={"Bucket": BUCKET, "Key": src_key},
    )
    log.info(f"  copied  s3://{BUCKET}/{src_key} → {dst_key}")


def s3_download_bytes(s3, key: str) -> bytes:
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return obj["Body"].read()


def refresh_service_bundle() -> dict:
    if not SERVICE_BASE_URL:
        raise RuntimeError(
            "SPOTIBOYS_SERVICE_BASE_URL is required for auto promotion to refresh the service VM"
        )
    url = f"{SERVICE_BASE_URL}/admin/refresh-serving-bundle"
    headers = {}
    if SERVICE_ADMIN_TOKEN:
        headers["X-SpotyBoys-Admin-Token"] = SERVICE_ADMIN_TOKEN
    resp = requests.post(
        url,
        headers=headers,
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


# ── Composite score ────────────────────────────────────────────────────────────

def composite_score(metrics: dict) -> float:
    return sum(w * metrics.get(k, 0.0) for k, w in COMPOSITE_WEIGHTS.items())


# ── MLflow helpers ────────────────────────────────────────────────────────────

def _get_best_run(experiment: str) -> object:
    client = mlflow.MlflowClient()
    exp = client.get_experiment_by_name(experiment)
    if exp is None:
        raise RuntimeError(f"MLflow experiment '{experiment}' not found")
    runs = client.search_runs(
        experiment_ids=[exp.experiment_id],
        filter_string="attributes.status = 'FINISHED'",
        order_by=["metrics.NDCG5 DESC"],
        max_results=50,
    )
    if not runs:
        raise RuntimeError(f"No finished runs in '{experiment}'")
    return max(runs, key=lambda r: composite_score(r.data.metrics))


def _download_ranker_artifacts(run_id: str, tmp_dir: str) -> tuple[str, str]:
    """Download gru_ranker.pt and gru_ranker_config.json from S3 artifact store."""
    s3 = _s3_client()
    # Look up experiment_id from the run to build the correct S3 path
    client = mlflow.MlflowClient()
    exp_id = client.get_run(run_id).info.experiment_id
    pt_key  = f"mlflow/{exp_id}/{run_id}/artifacts/gru_ranker.pt"
    cfg_key = f"mlflow/{exp_id}/{run_id}/artifacts/gru_ranker_config.json"

    pt_local  = os.path.join(tmp_dir, "gru_ranker.pt")
    cfg_local = os.path.join(tmp_dir, "gru_ranker_config.json")

    s3.download_file(BUCKET, pt_key,  pt_local)
    s3.download_file(BUCKET, cfg_key, cfg_local)
    log.info(f"  downloaded {pt_key}")
    log.info(f"  downloaded {cfg_key}")
    return pt_local, cfg_local


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Promote best ranker to Real_service/")
    parser.add_argument("--mode",             choices=["manual", "auto"], required=True)
    parser.add_argument("--retrieve-version", required=True,
                        help="Retrieve artifact version (e.g. 20260417_051148)")
    parser.add_argument("--version",          default=None,
                        help="Real_service VERSION (auto-generated if omitted)")
    args = parser.parse_args()

    version = args.version or time.strftime("%Y%m%d_%H%M%S")
    s3 = _s3_client()

    # ── Identify run to promote ───────────────────────────────────────────────
    # manual: best composite run in Phase 1 experiment
    # auto:   best composite run in Phase 2 experiment (the newly retrained model)
    if args.mode == "manual":
        run = _get_best_run(EXPERIMENT_PHASE1)
    else:
        run = _get_best_run(EXPERIMENT_PHASE2)

    run_id  = run.info.run_id
    metrics = run.data.metrics
    params  = run.data.params
    score   = composite_score(metrics)

    log.info(f"Best run: {run_id}")
    log.info(f"  composite={score:.4f}  NDCG5={metrics.get('NDCG5',0):.4f}"
             f"  HR5={metrics.get('HR5',0):.4f}  MRR5={metrics.get('MRR5',0):.4f}"
             f"  val_loss={metrics.get('val_loss',0):.4f}")

    # ── Phase 2 auto promotion gate ───────────────────────────────────────────
    if args.mode == "auto":
        try:
            baseline_bytes = s3_download_bytes(s3, "Real_service/baseline.json")
            baseline = json.loads(baseline_bytes)
        except Exception as e:
            log.error(f"Could not load baseline.json: {e}")
            sys.exit(1)

        threshold_composite = baseline["composite"] * 0.99
        threshold_val_loss  = baseline["val_loss"]  * 1.05
        passes = (
            score >= threshold_composite
            and metrics.get("val_loss", 999) <= threshold_val_loss
        )
        if not passes:
            log.error(
                f"PROMOTION FAILED: composite={score:.4f} < {threshold_composite:.4f}"
                f"  or val_loss={metrics.get('val_loss',999):.4f} > {threshold_val_loss:.4f}"
            )
            sys.exit(1)
        log.info("Promotion gate PASSED.")

    # ── Download ranker artifacts from MLflow ─────────────────────────────────
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pt_local, cfg_local = _download_ranker_artifacts(run_id, tmp)

        dst_prefix = f"Real_service/{version}"

        # Upload ranker model
        s3_upload_file(s3, pt_local,  f"{dst_prefix}/gru_ranker.pt")
        s3_upload_file(s3, cfg_local, f"{dst_prefix}/gru_ranker_config.json")

    # ── Copy retriever artifacts ───────────────────────────────────────────────
    retriever_files = [
        "cooc_session.npz",
        "cooc_playlist.npz",
        "user_centroids.pkl",
        "pop_scores.csv",
        "split_train.npy",
        "split_val.npy",
        "split_test.npy",
    ]
    for fname in retriever_files:
        src = f"Retrieve/{args.retrieve_version}/{fname}"
        dst = f"{dst_prefix}/{fname}"
        try:
            s3_copy(s3, src, dst)
        except Exception as e:
            log.warning(f"  skipped {fname}: {e}")

    # ── Write manifest.json ───────────────────────────────────────────────────
    manifest = {
        "version":           version,
        "retrieve_version":  args.retrieve_version,
        "mlflow_run_id":     run_id,
        "composite_score":   round(score, 6),
        "metrics":           {k: round(v, 6) for k, v in metrics.items()},
        "params":            params,
        "promoted_at":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode":              args.mode,
    }
    s3_upload_bytes(s3, json.dumps(manifest, indent=2).encode(),
                    f"{dst_prefix}/manifest.json")

    # ── Save / update baseline.json (manual mode) ─────────────────────────────
    if args.mode == "manual":
        baseline = {
            "composite":  round(score, 6),
            "val_loss":   round(metrics.get("val_loss", 0), 6),
            "run_id":     run_id,
            "version":    version,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        s3_upload_bytes(s3, json.dumps(baseline, indent=2).encode(),
                        "Real_service/baseline.json")
        log.info("baseline.json saved → Real_service/baseline.json")

    if args.mode == "auto":
        refresh_result = refresh_service_bundle()
        log.info(
            "Service VM refreshed: model_version=%s serving_bundle_version=%s",
            refresh_result.get("model_version"),
            refresh_result.get("serving_bundle_version"),
        )

    log.info(f"Promotion complete → s3://{BUCKET}/Real_service/{version}/")


if __name__ == "__main__":
    main()
