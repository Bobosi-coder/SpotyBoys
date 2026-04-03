"""
Stage 3 — 合并曲目目录与播放计数

Inputs:  data/processed/tracks_unique.csv
         data/processed/event_playcount.csv
Output:  data/processed/catalog.csv
         (track_id, artist_hint, title, final_playcount, pop_score, neg_sample_weight)
"""
import os
import time

import numpy as np
import pandas as pd

OUT_DIR = "data/processed"


def run(logger=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    if logger is None:
        try:
            from .pipeline_logging import setup_logging, get_stage_logger
        except ImportError:
            from pipeline_logging import setup_logging, get_stage_logger
        setup_logging()
        logger = get_stage_logger("stage3", "logs/stage3_merge.log")

    t0 = time.time()

    tracks = pd.read_csv(os.path.join(OUT_DIR, "tracks_unique.csv"), low_memory=False)
    counts = pd.read_csv(os.path.join(OUT_DIR, "event_playcount.csv"), low_memory=False)
    logger.info(f"加载 tracks_unique.csv：{len(tracks):,} 行")
    logger.info(f"加载 event_playcount.csv：{len(counts):,} 行")

    # 再次确认无重复（主键：track_id）
    for name, df in [("tracks_unique", tracks), ("event_playcount", counts)]:
        dups = df.duplicated(subset="track_id").sum()
        if dups:
            logger.error(
                f"[ERROR] {name} 中发现 {dups:,} 行重复 track_id——再次去重后继续"
            )
            df = df.drop_duplicates(subset="track_id", keep="first")
        if name == "tracks_unique":
            tracks = df
        else:
            counts = df

    # 左连接
    merged = tracks.merge(counts, on="track_id", how="left")
    logger.info(f"左连接完成：{len(merged):,} 行")

    # 填充 0
    merged["event_count"] = merged["event_count"].fillna(0).astype(np.int64)
    n_zero = int((merged["event_count"] == 0).sum())
    logger.info(f"final_playcount=0 的曲目：{n_zero:,} 条（{n_zero/len(merged):.1%}）")

    # 重命名
    merged = merged.rename(columns={"event_count": "final_playcount"})

    # 派生字段
    merged["pop_score"] = np.log1p(merged["final_playcount"].astype(float))

    raw_w = merged["final_playcount"].astype(float) ** 0.75
    total = raw_w.sum()
    if total > 0:
        merged["neg_sample_weight"] = raw_w / total
    else:
        logger.warning("所有 final_playcount=0，neg_sample_weight 设为均匀分布")
        merged["neg_sample_weight"] = 1.0 / len(merged)

    # 统计
    pc = merged["final_playcount"]
    logger.info(
        f"final_playcount 统计：min={int(pc.min()):,}, "
        f"max={int(pc.max()):,}, mean={pc.mean():.1f}"
    )

    out_cols = ["track_id", "artist_hint", "title",
                "final_playcount", "pop_score", "neg_sample_weight"]
    out = os.path.join(OUT_DIR, "catalog.csv")
    merged[out_cols].to_csv(out, index=False)
    logger.info(f"输出 catalog.csv：{len(merged):,} 行，耗时 {time.time()-t0:.0f}s")

    return merged[out_cols]
