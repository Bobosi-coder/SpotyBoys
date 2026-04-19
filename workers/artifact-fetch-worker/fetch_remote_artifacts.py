from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
from typing import Iterable, List

from packages.artifact_runtime import validate_serving_bundle_directory


BUCKET = os.environ.get("ARTIFACT_BUCKET", "proj23-mlflow-artifacts")
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT")
DEST_ROOT = Path(os.environ.get("SPOTIBOYS_OBJECT_STORAGE_ROOT", "/object-storage")) / BUCKET
ACTIVE_ROOT = Path(os.environ.get("SPOTIBOYS_ACTIVE_ARTIFACT_ROOT", "/serving-bundle"))
REQUIRED_SERVING_FILES = [
    "cooc_playlist.npz",
    "cooc_session.npz",
    "gru_ranker.pt",
    "gru_ranker_config.json",
    "manifest.json",
    "pop_scores.csv",
    "user_centroids.pkl",
]
REQUIRED_ITEM2VEC_FILES = [
    "item2vec_128d.npy",
    "item2vec_track_to_row.json",
    "item2vec_catalog.csv",
]


def fetch_remote_artifacts() -> Path:
    print(f"Connecting to object storage bucket={BUCKET}", flush=True)
    client = _client()
    real_service_prefix = _latest_real_service_prefix(client)
    print(f"Selected serving bundle prefix {real_service_prefix}", flush=True)
    real_service_dest = _download_prefix(client, real_service_prefix, DEST_ROOT / real_service_prefix.rstrip("/"))
    print(f"Downloaded serving bundle to {real_service_dest}", flush=True)
    _download_prefix(client, "Item2vec/", DEST_ROOT / "Item2vec")
    print(f"Downloaded Item2vec artifacts to {DEST_ROOT / 'Item2vec'}", flush=True)
    staged = _stage_runtime(real_service_dest, DEST_ROOT / "Item2vec")
    validate_serving_bundle_directory(staged / "Real_service" / "active")
    print(f"Staged active serving bundle at {staged / 'Real_service' / 'active'}", flush=True)
    return staged / "Real_service" / "active"


def _client():
    import boto3
    from botocore.config import Config

    verify = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() not in {"1", "true", "yes"}
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        verify=verify,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def _latest_real_service_prefix(client) -> str:
    paginator = client.get_paginator("list_objects_v2")
    versions: List[tuple[str, object]] = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix="Real_service/"):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.endswith("/manifest.json"):
                version_prefix = key.rsplit("/", 1)[0] + "/"
                versions.append((version_prefix, item.get("LastModified")))
    if not versions:
        raise RuntimeError("No Real_service/<version>/manifest.json found in object storage")
    versions.sort(key=lambda item: (item[1], item[0]))
    return versions[-1][0]


def _download_prefix(client, prefix: str, destination: Path) -> Path:
    if _prefix_complete(prefix, destination):
        print(f"Reusing existing downloaded prefix at {destination}", flush=True)
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.endswith("/"):
                continue
            relative = key[len(prefix) :].lstrip("/")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading s3://{BUCKET}/{key} -> {target}", flush=True)
            client.download_file(BUCKET, key, str(target))
    return destination


def _prefix_complete(prefix: str, destination: Path) -> bool:
    if not destination.exists():
        return False
    if prefix.startswith("Real_service/"):
        return all((destination / name).exists() for name in REQUIRED_SERVING_FILES)
    if prefix == "Item2vec/":
        return all((destination / name).exists() for name in REQUIRED_ITEM2VEC_FILES)
    return False


def _stage_runtime(real_service_dir: Path, item2vec_dir: Path) -> Path:
    ACTIVE_ROOT.mkdir(parents=True, exist_ok=True)
    for child in ACTIVE_ROOT.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    active_bundle = ACTIVE_ROOT / "Real_service" / "active"
    runtime_retriever = ACTIVE_ROOT / "runtime" / "retriever"
    runtime_item2vec = ACTIVE_ROOT / "runtime" / "item2vec"
    shutil.copytree(real_service_dir, active_bundle)
    _normalize_serving_manifest(active_bundle)
    shutil.copytree(item2vec_dir, runtime_item2vec)
    _copy_files(
        real_service_dir,
        runtime_retriever / "cooc",
        ["cooc_session.npz", "cooc_playlist.npz"],
    )
    _copy_files(real_service_dir, runtime_retriever / "popularity", ["pop_scores.csv"])
    _copy_files(real_service_dir, runtime_retriever / "pref_nn", ["user_centroids.pkl"])
    return ACTIVE_ROOT


def _normalize_serving_manifest(active_bundle: Path) -> None:
    manifest_path = active_bundle / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(payload.get("version") or active_bundle.name)
    payload.setdefault("model_version", version)
    payload["artifacts"] = [
        "gru_ranker.pt",
        "gru_ranker_config.json",
        "cooc_session.npz",
        "cooc_playlist.npz",
        "user_centroids.pkl",
        "pop_scores.csv",
        "manifest.json",
    ]
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_files(source: Path, destination: Path, names: Iterable[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(source / name, destination / name)


if __name__ == "__main__":
    print(fetch_remote_artifacts())
