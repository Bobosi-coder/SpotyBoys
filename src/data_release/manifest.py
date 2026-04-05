from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def git_commit(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_schema(path: Path) -> dict[str, Any]:
    sample = pd.read_csv(path, nrows=50, low_memory=False)
    return {
        "format": "csv",
        "columns": {column: str(dtype) for column, dtype in sample.dtypes.items()},
    }


def _parquet_schema(path: Path) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    return {
        "format": "parquet",
        "num_rows": parquet.metadata.num_rows,
        "columns": {field.name: str(field.type) for field in schema},
    }


def _npy_schema(path: Path) -> dict[str, Any]:
    arr = np.load(path, mmap_mode="r")
    return {
        "format": "npy",
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
    }


def _npz_schema(path: Path) -> dict[str, Any]:
    archive = np.load(path)
    return {
        "format": "npz",
        "members": list(archive.files),
    }


def _json_schema(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema: dict[str, Any] = {"format": "json", "top_level_type": type(payload).__name__}
    if isinstance(payload, dict):
        schema["keys"] = sorted(payload.keys())
    return schema


def summarize_artifact(
    path: str | Path,
    *,
    logical_name: str | None = None,
    s3_uri: str | None = None,
    include_sha256: bool = True,
) -> dict[str, Any]:
    artifact_path = Path(path)
    summary: dict[str, Any] = {
        "name": logical_name or artifact_path.name,
        "local_path": str(artifact_path.as_posix()),
        "size_bytes": artifact_path.stat().st_size,
    }
    if s3_uri:
        summary["s3_uri"] = s3_uri
    if include_sha256:
        summary["sha256"] = sha256_file(artifact_path)

    suffix = artifact_path.suffix.lower()
    try:
        if suffix == ".csv":
            summary["schema"] = _csv_schema(artifact_path)
        elif suffix == ".parquet":
            summary["schema"] = _parquet_schema(artifact_path)
        elif suffix == ".npy":
            summary["schema"] = _npy_schema(artifact_path)
        elif suffix == ".npz":
            summary["schema"] = _npz_schema(artifact_path)
        elif suffix == ".json":
            summary["schema"] = _json_schema(artifact_path)
    except Exception as exc:
        summary["schema_error"] = str(exc)
    return summary


def build_manifest(
    *,
    release_name: str,
    release_version: str,
    release_uri: str,
    source_version: str,
    raw_source_uri: str,
    pipeline_name: str,
    project_root: Path,
    input_objects: list[dict[str, Any]],
    output_objects: list[dict[str, Any]],
    metrics: Mapping[str, Any] | None = None,
    notes: list[str] | None = None,
    upstream_versions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "release_name": release_name,
        "release_version": release_version,
        "release_uri": release_uri,
        "created_at_utc": utc_now_iso(),
        "git_commit": git_commit(project_root),
        "pipeline_name": pipeline_name,
        "source_version": source_version,
        "raw_source_uri": raw_source_uri,
        "input_objects": input_objects,
        "output_objects": output_objects,
        "metrics": dict(metrics or {}),
        "upstream_versions": dict(upstream_versions or {}),
        "notes": list(notes or []),
    }


def write_manifest(manifest: Mapping[str, Any], path: str | Path) -> Path:
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path

