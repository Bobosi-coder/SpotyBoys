#!/usr/bin/env python3
"""
scripts/merge_delta.py — Merge snapshot + delta parquets into artifacts/item2vec/

Called by retrain.sh in --with-delta mode.
Expects delta partitions already downloaded to /tmp/delta/ by download_data.sh.

Delta directory layout:
  /tmp/delta/{YYYYMMDD}/session_tracks_addition.parquet
  /tmp/delta/{YYYYMMDD}/session_meta_addition.parquet
  /tmp/delta/{YYYYMMDD}/love_addition.parquet
  /tmp/delta/{YYYYMMDD}/users_addition.parquet

Snapshot files (already in artifacts/item2vec/):
  session_tracks_i2v.parquet
  session_meta_i2v.parquet
  love_filtered_i2v.parquet   ← love_i2v on S3
  users_filtered_i2v.parquet  ← users_i2v on S3
"""

import sys
import glob
from pathlib import Path

import pandas as pd

ARTIFACTS = Path("artifacts/item2vec")
DELTA_ROOT = Path("/tmp/delta")

# (snapshot local name, delta addition filename)
MERGE_TARGETS = [
    ("session_tracks_i2v.parquet",  "session_tracks_addition.parquet"),
    ("session_meta_i2v.parquet",    "session_meta_addition.parquet"),
    ("love_filtered_i2v.parquet",   "love_addition.parquet"),
    ("users_filtered_i2v.parquet",  "users_addition.parquet"),
]


def main() -> None:
    # Collect all delta date partitions, sorted chronologically
    date_dirs = sorted([p for p in DELTA_ROOT.iterdir() if p.is_dir()])
    if not date_dirs:
        print("No delta partitions found in /tmp/delta/ — nothing to merge.")
        sys.exit(0)

    print(f"Found {len(date_dirs)} delta partition(s): {[d.name for d in date_dirs]}")

    for snapshot_file, addition_file in MERGE_TARGETS:
        snapshot_path = ARTIFACTS / snapshot_file
        if not snapshot_path.exists():
            print(f"WARNING: snapshot file not found, skipping: {snapshot_path}")
            continue

        addition_paths = [d / addition_file for d in date_dirs if (d / addition_file).exists()]
        if not addition_paths:
            print(f"No delta additions for {snapshot_file} — keeping snapshot as-is.")
            continue

        print(f"Merging {snapshot_file} + {len(addition_paths)} delta file(s) ...", end="", flush=True)

        dfs = [pd.read_parquet(snapshot_path)]
        for p in addition_paths:
            dfs.append(pd.read_parquet(p))

        merged = pd.concat(dfs, ignore_index=True)
        merged.to_parquet(snapshot_path, index=False)
        print(f" done  ({len(dfs[0]):,} + {sum(len(d) for d in dfs[1:]):,} → {len(merged):,} rows)")

    print("Merge complete.")


if __name__ == "__main__":
    main()
