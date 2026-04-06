from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

from .manifest import build_manifest, summarize_artifact, write_manifest
from .object_store import S3ObjectStore
from .versioning import ReleaseLocation, build_s3_uri, join_s3_key, resolve_version

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = PROJECT_ROOT / "artifacts" / "releases" / "ranker"

RELEASE_FILES = [
    "artifacts/ranker/neg_sample_weights.npy",
    "artifacts/ranker/ranker_train.parquet",
    "artifacts/ranker/ranker_val.parquet",
    "artifacts/ranker/gru_ranker.pt",
    "artifacts/ranker/gru_ranker_config.json",
]

PROCESSED_DEPENDENCIES = [
    "item2vec_128d.npy",
    "item2vec_track_to_row.json",
    "item2vec_catalog.csv",
    "session_tracks_i2v.parquet",
]

RETRIEVER_DEPENDENCIES = [
    "split/split_train.npy",
    "split/split_val.npy",
    "cooc/cooc_session.npz",
    "popularity/pop_scores.csv",
    "pref_nn/user_centroids.pkl",
]

log = logging.getLogger("data_release.ranker")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a versioned ranker release.")
    parser.add_argument("--ranker-version", default=None)
    parser.add_argument("--processed-dataset-version", default=os.environ.get("DATASET_VERSION"))
    parser.add_argument("--retriever-feature-version", default=os.environ.get("FEATURE_VERSION"))
    parser.add_argument(
        "--bucket",
        default=os.environ.get(
            "DATA_RELEASE_BUCKET",
            os.environ.get("ARTIFACT_STORAGE_BUCKET", "proj23-mlflow-artifacts"),
        ),
    )
    parser.add_argument(
        "--release-prefix",
        default=os.environ.get("RANKER_RELEASE_PREFIX", "datasets/ranker"),
    )
    parser.add_argument(
        "--manifest-prefix",
        default=os.environ.get("RELEASE_MANIFEST_PREFIX", "manifests/releases"),
    )
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--skip-data-build", action="store_true")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--skip-sha256", action="store_true")

    parser.add_argument("--max-train-sessions", type=int, default=None)
    parser.add_argument("--max-val-sessions", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)

    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--max-norm", type=float, default=1.0)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--mlflow-experiment", default="gru-ranker-training")
    parser.add_argument("--run-name", default="gru-ranker")
    parser.add_argument("--data-build-experiment", default="ranker-data-build")
    parser.add_argument("--data-build-run-name", default="ranker-data-build")
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    from src.ranker.data.build import run as run_data_build
    from src.ranker.train import train as run_train

    metrics: dict[str, Any] = {}

    if not args.skip_data_build:
        metrics["data_build"] = run_data_build(
            mlflow_experiment=args.data_build_experiment,
            run_name=args.data_build_run_name,
            max_train_sessions=args.max_train_sessions,
            max_val_sessions=args.max_val_sessions,
            seed=args.seed,
        )

    if not args.skip_train:
        train_args = argparse.Namespace(
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            weight_decay=args.weight_decay,
            max_norm=args.max_norm,
            device=args.device,
            mlflow_experiment=args.mlflow_experiment,
            run_name=args.run_name,
        )
        run_train(train_args)
        metrics["training"] = {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "max_norm": args.max_norm,
            "device": args.device,
            "mlflow_experiment": args.mlflow_experiment,
            "run_name": args.run_name,
        }

    return metrics


def ensure_release_files() -> list[Path]:
    missing: list[str] = []
    paths: list[Path] = []
    for relative_path in RELEASE_FILES:
        full_path = PROJECT_ROOT / relative_path
        if full_path.exists():
            paths.append(full_path)
        else:
            missing.append(relative_path)
    if missing:
        raise FileNotFoundError(
            "Missing ranker release files:\n" + "\n".join(f"- {path}" for path in missing)
        )
    return paths


def input_objects(
    processed_dataset_version: str | None,
    retriever_feature_version: str | None,
    bucket: str,
) -> list[dict[str, str]]:
    objects: list[dict[str, str]] = []

    if processed_dataset_version:
        processed_prefix = f"processed/item2vec/{processed_dataset_version}"
        objects.extend(
            {
                "name": name,
                "s3_uri": build_s3_uri(bucket, join_s3_key(processed_prefix, name)),
            }
            for name in PROCESSED_DEPENDENCIES
        )
    else:
        objects.extend(
            {
                "name": name,
                "local_path": str((PROJECT_ROOT / "artifacts" / "item2vec" / name).as_posix()),
            }
            for name in PROCESSED_DEPENDENCIES
        )

    if retriever_feature_version:
        retriever_prefix = f"features/retriever/{retriever_feature_version}"
        objects.extend(
            {
                "name": name,
                "s3_uri": build_s3_uri(bucket, join_s3_key(retriever_prefix, name)),
            }
            for name in RETRIEVER_DEPENDENCIES
        )
    else:
        objects.extend(
            {
                "name": name,
                "local_path": str((PROJECT_ROOT / "artifacts" / "retriever" / name).as_posix()),
            }
            for name in RETRIEVER_DEPENDENCIES
        )

    return objects


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = parse_args()
    ranker_version = resolve_version(
        explicit=args.ranker_version,
        env_var="RANKER_VERSION",
        label="ranker",
    )
    release = ReleaseLocation(
        bucket=args.bucket,
        prefix=args.release_prefix,
        version=ranker_version,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Preparing ranker release %s -> %s", ranker_version, release.uri)

    pipeline_metrics: dict[str, Any] = {}
    if args.skip_pipeline:
        args.skip_data_build = True
        args.skip_train = True
    else:
        pipeline_metrics = run_pipeline(args)

    local_paths = ensure_release_files()
    store = S3ObjectStore(bucket=args.bucket)
    uploaded_outputs: list[dict[str, Any]] = []
    for path in local_paths:
        relative_name = path.relative_to(PROJECT_ROOT / "artifacts" / "ranker").as_posix()
        key = join_s3_key(release.versioned_prefix, relative_name)
        s3_uri = store.upload_file(path, key)
        uploaded_outputs.append(
            summarize_artifact(
                path,
                logical_name=relative_name,
                s3_uri=s3_uri,
                include_sha256=not args.skip_sha256,
            )
        )
        log.info("Uploaded %s", s3_uri)

    manifest = build_manifest(
        release_name="ranker_release",
        release_version=ranker_version,
        release_uri=release.uri,
        source_version=args.processed_dataset_version or "local_ranker_artifacts",
        raw_source_uri="derived-from-processed-item2vec-and-retriever-features",
        pipeline_name="src.data_release.publish_ranker_release",
        project_root=PROJECT_ROOT,
        input_objects=input_objects(
            args.processed_dataset_version,
            args.retriever_feature_version,
            args.bucket,
        ),
        output_objects=uploaded_outputs,
        metrics=pipeline_metrics,
        upstream_versions={
            "processed_item2vec": args.processed_dataset_version or "local",
            "retriever_features": args.retriever_feature_version or "local",
        },
        notes=[
            "Initial implementation release for ranker-ready datasets and GRU checkpoint.",
            "Release contents include ranker train/val parquet files, negative-sample weights, and model artifacts.",
            "Published under datasets/ranker for course-facing versioned dataset organization.",
        ],
    )

    manifest_path = write_manifest(manifest, RELEASE_DIR / ranker_version / "manifest.json")
    manifest_key = join_s3_key(release.versioned_prefix, "manifest.json")
    manifest_uri = store.upload_file(manifest_path, manifest_key)
    central_manifest_key = join_s3_key(args.manifest_prefix, "ranker_release", f"{ranker_version}.json")
    central_manifest_uri = store.upload_file(manifest_path, central_manifest_key)

    log.info("Release complete.")
    log.info("Release URI: %s", release.uri)
    log.info("Manifest URI: %s", manifest_uri)
    log.info("Central manifest URI: %s", central_manifest_uri)


if __name__ == "__main__":
    main()
