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
RELEASE_DIR = PROJECT_ROOT / "artifacts" / "releases" / "retriever"

RELEASE_FILES = [
    "artifacts/retriever/split/split_train.npy",
    "artifacts/retriever/split/split_val.npy",
    "artifacts/retriever/split/split_test.npy",
    "artifacts/retriever/cooc/cooc_session.npz",
    "artifacts/retriever/cooc/cooc_playlist.npz",
    "artifacts/retriever/pref_nn/user_centroids.pkl",
    "artifacts/retriever/popularity/pop_scores.csv",
]

PROCESSED_DEPENDENCIES = [
    "artifacts/item2vec/session_tracks_i2v.parquet",
    "artifacts/item2vec/session_meta_i2v.parquet",
    "artifacts/item2vec/playlist_tracks_i2v.parquet",
    "artifacts/item2vec/love_filtered_i2v.parquet",
    "artifacts/item2vec/item2vec_128d.npy",
    "artifacts/item2vec/item2vec_track_to_row.json",
    "artifacts/item2vec/item2vec_catalog.csv",
]

log = logging.getLogger("data_release.retriever")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a versioned retriever feature release.")
    parser.add_argument("--feature-version", default=None)
    parser.add_argument("--processed-dataset-version", default=os.environ.get("DATASET_VERSION"))
    parser.add_argument(
        "--bucket",
        default=os.environ.get(
            "DATA_RELEASE_BUCKET",
            os.environ.get("ARTIFACT_STORAGE_BUCKET", "proj23-mlflow-artifacts"),
        ),
    )
    parser.add_argument(
        "--release-prefix",
        default=os.environ.get("RETRIEVER_RELEASE_PREFIX", "features/retriever"),
    )
    parser.add_argument(
        "--manifest-prefix",
        default=os.environ.get("RELEASE_MANIFEST_PREFIX", "manifests/releases"),
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-sha256", action="store_true")
    return parser.parse_args()


def run_build() -> dict[str, Any]:
    from src.retriever.cooc.build import run as run_cooc
    from src.retriever.popularity.build import run as run_popularity
    from src.retriever.pref_nn.build import run as run_pref
    from src.retriever.split.build import run as run_split

    return {
        "split": run_split(),
        "cooc": run_cooc(),
        "pref_nn": run_pref(),
        "popularity": run_popularity(),
    }


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
            "Missing retriever release files:\n" + "\n".join(f"- {path}" for path in missing)
        )
    return paths


def input_objects(processed_dataset_version: str | None, bucket: str) -> list[dict[str, str]]:
    if processed_dataset_version:
        prefix = f"processed/item2vec/{processed_dataset_version}"
        return [
            {
                "name": Path(relative_path).name,
                "s3_uri": build_s3_uri(bucket, join_s3_key(prefix, Path(relative_path).name)),
            }
            for relative_path in PROCESSED_DEPENDENCIES
        ]
    return [
        {
            "name": Path(relative_path).name,
            "local_path": str((PROJECT_ROOT / relative_path).as_posix()),
        }
        for relative_path in PROCESSED_DEPENDENCIES
    ]


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = parse_args()
    feature_version = resolve_version(
        explicit=args.feature_version,
        env_var="FEATURE_VERSION",
        label="retriever",
    )
    release = ReleaseLocation(
        bucket=args.bucket,
        prefix=args.release_prefix,
        version=feature_version,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Preparing retriever release %s -> %s", feature_version, release.uri)

    build_metrics: dict[str, Any] = {}
    if not args.skip_build:
        build_metrics = run_build()

    local_paths = ensure_release_files()
    store = S3ObjectStore(bucket=args.bucket)
    uploaded_outputs: list[dict[str, Any]] = []
    for path in local_paths:
        relative_name = path.relative_to(PROJECT_ROOT / "artifacts" / "retriever").as_posix()
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
        release_name="retriever_features",
        release_version=feature_version,
        release_uri=release.uri,
        source_version=args.processed_dataset_version or "local_item2vec_artifacts",
        raw_source_uri="derived-from-processed-item2vec-release",
        pipeline_name="src.data_release.publish_retriever_release",
        project_root=PROJECT_ROOT,
        input_objects=input_objects(args.processed_dataset_version, args.bucket),
        output_objects=uploaded_outputs,
        metrics=build_metrics,
        upstream_versions={"processed_item2vec": args.processed_dataset_version or "local"},
        notes=[
            "Initial implementation release for retriever-ready offline features.",
            "This release depends on the processed Item2Vec dataset artifacts.",
            "Outputs include split files, co-occurrence matrices, preference centroids, and popularity scores.",
        ],
    )

    manifest_path = write_manifest(manifest, RELEASE_DIR / feature_version / "manifest.json")
    manifest_key = join_s3_key(release.versioned_prefix, "manifest.json")
    manifest_uri = store.upload_file(manifest_path, manifest_key)
    central_manifest_key = join_s3_key(args.manifest_prefix, "retriever_features", f"{feature_version}.json")
    central_manifest_uri = store.upload_file(manifest_path, central_manifest_key)

    log.info("Release complete.")
    log.info("Release URI: %s", release.uri)
    log.info("Manifest URI: %s", manifest_uri)
    log.info("Central manifest URI: %s", central_manifest_uri)


if __name__ == "__main__":
    main()
