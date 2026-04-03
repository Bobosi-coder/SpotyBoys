"""
Stage 1 — 构建去重曲目目录

Input:  data/raw/content/30music_parsed/tracks.csv
Output: data/processed/tracks_unique.csv  (track_id, artist_hint, title)
"""
import os
import time

import pandas as pd

RAW_DIR = "data/raw/content/30music_parsed"
OUT_DIR = "data/processed"
DEDUP_WARN_THRESHOLD = 0.05


def run(logger=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    if logger is None:
        try:
            from .pipeline_logging import setup_logging, get_stage_logger
        except ImportError:
            from pipeline_logging import setup_logging, get_stage_logger
        setup_logging()
        logger = get_stage_logger("stage1", "logs/stage1_catalog.log")

    t0  = time.time()
    src = os.path.join(RAW_DIR, "tracks.csv")

    # 加载，只取三列
    df    = pd.read_csv(src, usecols=["track_id", "artist_hint", "title"], low_memory=False)
    n_raw = len(df)
    logger.info(f"加载 tracks.csv：{n_raw:,} 行")

    # 去重（主键：track_id）
    df        = df.drop_duplicates(subset="track_id", keep="first")
    n_dedup   = len(df)
    n_dropped = n_raw - n_dedup
    drop_rate = n_dropped / n_raw
    logger.info(
        f"去重（track_id）：{n_raw:,} → {n_dedup:,} 行，"
        f"丢弃 {n_dropped:,} 行（{drop_rate:.1%}）"
    )
    if drop_rate > DEDUP_WARN_THRESHOLD:
        logger.warning(
            f"去重丢弃率 {drop_rate:.1%} 超过预期阈值 {DEDUP_WARN_THRESHOLD:.0%}，请核查原始数据"
        )

    out = os.path.join(OUT_DIR, "tracks_unique.csv")
    df.to_csv(out, index=False)
    logger.info(f"输出 tracks_unique.csv：{n_dedup:,} 行，耗时 {time.time()-t0:.0f}s")

    return df
