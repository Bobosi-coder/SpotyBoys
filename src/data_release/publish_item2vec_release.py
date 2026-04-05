from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

from .manifest import build_manifest, summarize_artifact, write_manifest
from .object_store import S3ObjectStore
from .versioning import ReleaseLocation, join_s3_key, resolve_version

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = PROJECT_ROOT / "artifacts" / "releases" / "item2vec"

RAW_INPUT_FILES = [
    "tracks.csv",
    "session_tracks.csv",
    "session_meta.csv",
    "playlist_tracks.csv",
    "playlist_meta.csv",
    "love.csv",
    "users.csv",
]

RELEASE_FILES = [
    "artifacts/item2vec/item2vec_corpus.parquet",
    "artifacts/item2vec/item2vec_model.bin",
    "artifacts/item2vec/item2vec_128d.npy",
    "artifacts/item2vec/item2vec_track_to_row.json",
    "artifacts/item2vec/item2vec_catalog.csv",
    "artifacts/item2vec/session_tracks_i2v.parquet",
    "artifacts/item2vec/session_meta_i2v.parquet",
    "artifacts/item2vec/playlist_tracks_i2v.parquet",
    "artifacts/item2vec/playlist_meta_i2v.parquet",
    "artifacts/item2vec/love_filtered_i2v.parquet",
    "artifacts/item2vec/users_filtered_i2v.parquet",
]

log = logging.getLogger("data_release.item2vec")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a versioned Item2Vec data release.")
    parser.add_argument("--dataset-version", default=None)
    parser.add_argument("--source-version", default=os.environ.get("RAW_SOURCE_VERSION", "source_v1"))
    parser.add_argument(
        "--raw-source-uri",
        default=os.environ.get(
            "RAW_SOURCE_URI",
            "s3://proj23-mlflow-artifacts/data/raw/content/30music_parsed/",
        ),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get(
            "DATA_RELEASE_BUCKET",
            os.environ.get("ARTIFACT_STORAGE_BUCKET", "proj23-mlflow-artifacts"),
        ),
    )
    parser.add_argument(
        "--release-prefix",
        default=os.environ.get("ITEM2VEC_RELEASE_PREFIX", "processed/item2vec"),
    )
    parser.add_argument(
        "--manifest-prefix",
        default=os.environ.get("RELEASE_MANIFEST_PREFIX", "manifests/releases"),
    )
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--stages", default="a,b,c,d")
    parser.add_argument("--vector-size", type=int, default=128)
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--negative", type=int, default=15)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("ITEM2VEC_WORKERS", "8")))
    parser.add_argument("--mlflow-experiment", default="item2vec-training")
    parser.add_argument("--run-name", default="item2vec-release")
    parser.add_argument("--skip-sha256", action="store_true")
    return parser.parse_args()


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    from src.item2vec.stage_a_corpus import run as run_a
    from src.item2vec.stage_b_train import run as run_b
    from src.item2vec.stage_c_validate import run as run_c
    from src.item2vec.stage_d_filter import run as run_d

    stages = [stage.strip().lower() for stage in args.stages.split(",") if stage.strip()]
    stats: dict[str, Any] = {}
    run_id: str | None = None

    if "a" in stages:
        stats["stage_a"] = run_a()
    if "b" in stages:
        stats["stage_b"] = run_b(
            vector_size=args.vector_size,
            window=args.window,
            min_count=args.min_count,
            negative=args.negative,
            epochs=args.epochs,
            workers=args.workers,
            mlflow_experiment=args.mlflow_experiment,
            run_name=args.run_name,
        )
        run_id = stats["stage_b"]["run_id"]
    if "c" in stages:
        stats["stage_c"] = run_c(run_id=run_id, mlflow_experiment=args.mlflow_experiment)
    if "d" in stages:
        stats["stage_d"] = run_d(run_id=run_id, mlflow_experiment=args.mlflow_experiment)

    return stats


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
            "Missing Item2Vec release files:\n" + "\n".join(f"- {path}" for path in missing)
        )
    return paths


def raw_input_objects(raw_source_uri: str) -> list[dict[str, str]]:
    base = raw_source_uri.rstrip("/")
    return [{"s3_uri": f"{base}/{name}", "name": name} for name in RAW_INPUT_FILES]


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = parse_args()
    dataset_version = resolve_version(
        explicit=args.dataset_version,
        env_var="DATASET_VERSION",
        label="item2vec",
    )
    release = ReleaseLocation(
        bucket=args.bucket,
        prefix=args.release_prefix,
        version=dataset_version,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Preparing Item2Vec release %s -> %s", dataset_version, release.uri)

    pipeline_metrics: dict[str, Any] = {}
    if not args.skip_pipeline:
        pipeline_metrics = run_pipeline(args)

    local_paths = ensure_release_files()
    store = S3ObjectStore(bucket=args.bucket)
    uploaded_outputs: list[dict[str, Any]] = []
    for path in local_paths:
        relative_name = path.relative_to(PROJECT_ROOT / "artifacts" / "item2vec").as_posix()
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
        release_name="processed_item2vec",
        release_version=dataset_version,
        release_uri=release.uri,
        source_version=args.source_version,
        raw_source_uri=args.raw_source_uri,
        pipeline_name="src.data_release.publish_item2vec_release",
        project_root=PROJECT_ROOT,
        input_objects=raw_input_objects(args.raw_source_uri),
        output_objects=uploaded_outputs,
        metrics=pipeline_metrics,
        notes=[
            "Initial implementation release for processed Item2Vec-ready data.",
            "Raw source is already parsed CSV stored in the project object-storage bucket.",
            "Stage A-D outputs are published under a versioned processed/item2vec prefix.",
        ],
    )

    manifest_path = write_manifest(manifest, RELEASE_DIR / dataset_version / "manifest.json")
    manifest_key = join_s3_key(release.versioned_prefix, "manifest.json")
    manifest_uri = store.upload_file(manifest_path, manifest_key)
    central_manifest_key = join_s3_key(args.manifest_prefix, "processed_item2vec", f"{dataset_version}.json")
    central_manifest_uri = store.upload_file(manifest_path, central_manifest_key)

    log.info("Release complete.")
    log.info("Release URI: %s", release.uri)
    log.info("Manifest URI: %s", manifest_uri)
    log.info("Central manifest URI: %s", central_manifest_uri)


if __name__ == "__main__":
    main()
