"""
Stage 2 — 从事件日志计算曲目播放次数

Input:  data/raw/content/30music_parsed/events.csv  (~31M 行，分块读取)
Output: data/processed/event_playcount.csv  (track_id, event_count)
"""
import os
import time
from collections import Counter

import pandas as pd

RAW_DIR = "data/raw/content/30music_parsed"
OUT_DIR = "data/processed"
CHUNK   = 500_000


def run(logger=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    if logger is None:
        try:
            from .pipeline_logging import setup_logging, get_stage_logger
        except ImportError:
            from pipeline_logging import setup_logging, get_stage_logger
        setup_logging()
        logger = get_stage_logger("stage2", "logs/stage2_playcount.log")

    t0  = time.time()
    src = os.path.join(RAW_DIR, "events.csv")
    logger.info(f"加载 {src}")

    seen_event_ids: set    = set()
    track_counter: Counter = Counter()
    n_raw = n_dedup = 0

    for chunk in pd.read_csv(src, usecols=["event_id", "track_id"],
                              chunksize=CHUNK, low_memory=False):
        n_raw += len(chunk)
        # 块内去重
        chunk = chunk.drop_duplicates(subset="event_id", keep="first")
        # 跨块去重
        mask  = ~chunk["event_id"].isin(seen_event_ids)
        chunk = chunk[mask]
        seen_event_ids.update(chunk["event_id"].tolist())
        n_dedup += len(chunk)
        track_counter.update(chunk["track_id"].dropna().astype(int).tolist())

    n_dropped = n_raw - n_dedup
    logger.info(f"加载 events.csv：{n_raw:,} 行")
    logger.info(
        f"去重（event_id）：{n_raw:,} → {n_dedup:,} 行，丢弃 {n_dropped:,} 行"
    )

    df = (
        pd.DataFrame(track_counter.items(), columns=["track_id", "event_count"])
        .sort_values("track_id")
        .reset_index(drop=True)
    )
    logger.info(f"统计完成：覆盖 {len(df):,} 个唯一 track_id")

    out = os.path.join(OUT_DIR, "event_playcount.csv")
    df.to_csv(out, index=False)
    logger.info(f"输出 event_playcount.csv，耗时 {time.time()-t0:.0f}s")

    return df
