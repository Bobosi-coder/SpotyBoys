"""
Stage 5 — 将所有交互表过滤至音频目录

必须在 Stage 4 全部批次完成后执行（audio_catalog.csv 已最终确定）。

5a  session_tracks   → data/processed/session_tracks.parquet
5b  session_meta     → data/processed/session_meta.parquet
5c  playlist_tracks  → data/processed/playlist_tracks.parquet
5d  playlist_meta    → data/processed/playlist_meta.parquet
5e  love             → data/processed/love_filtered.parquet
5f  users            → data/processed/users_filtered.parquet
"""
import os
import time

import pandas as pd

RAW_DIR       = "data/raw/content/30music_parsed"
OUT_DIR       = "data/processed"
PLAYRATIO_CAP = 5.0
MIN_SEQ       = 2


def run(logger=None):
    os.makedirs(OUT_DIR, exist_ok=True)

    if logger is None:
        try:
            from .pipeline_logging import setup_logging, get_stage_logger
        except ImportError:
            from pipeline_logging import setup_logging, get_stage_logger
        setup_logging()
        logger = get_stage_logger("stage5", "logs/stage5_filter.log")

    stage_start = time.time()

    # 加载音频目录 ID 集合
    audio_ids = set(
        pd.read_csv(os.path.join(OUT_DIR, "audio_catalog.csv"),
                    usecols=["track_id"])["track_id"].astype(int)
    )
    logger.info(f"音频目录加载完成：{len(audio_ids):,} 个有效 track_id")

    # ── 5a  session_tracks ────────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5a session_tracks ──")

    keep = ["session_id", "user_id", "position", "track_id", "playratio", "label"]
    st   = pd.read_csv(os.path.join(RAW_DIR, "session_tracks.csv"),
                       usecols=keep, low_memory=False)
    st_n_load = len(st)
    logger.info(f"session_tracks 加载：{st_n_load:,} 行")

    # 去重（主键：session_id, position）
    st = st.drop_duplicates(subset=["session_id", "position"], keep="first")
    st_n_dedup = len(st)
    logger.info(
        f"去重（session_id, position）：{st_n_load:,} → {st_n_dedup:,} 行，"
        f"丢弃 {st_n_load - st_n_dedup:,} 行"
    )

    # 过滤 track_id ∉ audio_catalog
    st["track_id"] = pd.to_numeric(st["track_id"], errors="coerce")
    st = st[st["track_id"].isin(audio_ids)]
    st_n_audio = len(st)
    logger.info(
        f"过滤 track_id：→ {st_n_audio:,} 行，"
        f"丢弃 {st_n_dedup - st_n_audio:,} 行"
    )

    # 过滤 label == "unknown"
    st = st[st["label"] != "unknown"]
    st_n_label = len(st)
    logger.info(
        f"过滤 unknown label：→ {st_n_label:,} 行，"
        f"丢弃 {st_n_audio - st_n_label:,} 行"
    )

    # playratio 截断至 5.0
    st["playratio"] = pd.to_numeric(st["playratio"], errors="coerce")
    n_clipped = int((st["playratio"] > PLAYRATIO_CAP).sum())
    st["playratio"] = st["playratio"].clip(upper=PLAYRATIO_CAP)
    logger.info(f"playratio 截断至 {PLAYRATIO_CAP}：{n_clipped:,} 行被截断")

    # 丢弃长度 < MIN_SEQ 的会话
    valid_sessions  = st.groupby("session_id").size()
    valid_sessions  = valid_sessions[valid_sessions >= MIN_SEQ].index
    st = st[st["session_id"].isin(valid_sessions)]
    st_n_final = len(st)
    logger.info(
        f"丢弃长度<{MIN_SEQ} 的会话：→ {st_n_final:,} 行，"
        f"丢弃 {st_n_label - st_n_final:,} 行"
    )

    st = st.sort_values(["session_id", "position"]).reset_index(drop=True)
    out_st = os.path.join(OUT_DIR, "session_tracks.parquet")
    st.to_parquet(out_st, index=False)
    logger.info(f"输出 session_tracks.parquet：{st_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 5b  session_meta ──────────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5b session_meta ──")

    sm = pd.read_csv(os.path.join(RAW_DIR, "session_meta.csv"),
                     usecols=["session_id", "user_id"], low_memory=False)
    sm_n_load = len(sm)

    sm = sm.drop_duplicates(subset="session_id", keep="first")
    sm_n_dedup = len(sm)
    logger.info(
        f"去重（session_id）：{sm_n_load:,} → {sm_n_dedup:,} 行，"
        f"丢弃 {sm_n_load - sm_n_dedup:,} 行"
    )

    surviving_sessions = set(st["session_id"].unique())
    sm = sm[sm["session_id"].isin(surviving_sessions)]
    sm_n_final = len(sm)
    logger.info(
        f"过滤至有效 session_id：{sm_n_dedup:,} → {sm_n_final:,} 行，"
        f"丢弃 {sm_n_dedup - sm_n_final:,} 行"
    )

    out_sm = os.path.join(OUT_DIR, "session_meta.parquet")
    sm.to_parquet(out_sm, index=False)
    logger.info(f"输出 session_meta.parquet：{sm_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 5c  playlist_tracks ───────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5c playlist_tracks ──")

    pt = pd.read_csv(os.path.join(RAW_DIR, "playlist_tracks.csv"),
                     usecols=["playlist_id", "user_id", "position", "track_id"],
                     low_memory=False)
    pt_n_load = len(pt)

    pt = pt.drop_duplicates(subset=["playlist_id", "track_id"], keep="first")
    pt_n_dedup = len(pt)
    logger.info(
        f"去重（playlist_id, track_id）：{pt_n_load:,} → {pt_n_dedup:,} 行，"
        f"丢弃 {pt_n_load - pt_n_dedup:,} 行"
    )

    pt["track_id"] = pd.to_numeric(pt["track_id"], errors="coerce")
    pt = pt[pt["track_id"].isin(audio_ids)]
    pt_n_audio = len(pt)
    logger.info(
        f"过滤 track_id：→ {pt_n_audio:,} 行，"
        f"丢弃 {pt_n_dedup - pt_n_audio:,} 行"
    )

    valid_pls = pt.groupby("playlist_id").size()
    valid_pls = valid_pls[valid_pls >= MIN_SEQ].index
    pt = pt[pt["playlist_id"].isin(valid_pls)]
    pt_n_final = len(pt)
    logger.info(
        f"丢弃长度<{MIN_SEQ} 的播放列表：→ {pt_n_final:,} 行，"
        f"丢弃 {pt_n_audio - pt_n_final:,} 行"
    )

    out_pt = os.path.join(OUT_DIR, "playlist_tracks.parquet")
    pt.to_parquet(out_pt, index=False)
    logger.info(f"输出 playlist_tracks.parquet：{pt_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 5d  playlist_meta ─────────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5d playlist_meta ──")

    pm = pd.read_csv(os.path.join(RAW_DIR, "playlist_meta.csv"),
                     usecols=["playlist_id", "user_id"], low_memory=False)
    pm_n_load = len(pm)

    pm = pm.drop_duplicates(subset="playlist_id", keep="first")
    pm_n_dedup = len(pm)
    logger.info(
        f"去重（playlist_id）：{pm_n_load:,} → {pm_n_dedup:,} 行，"
        f"丢弃 {pm_n_load - pm_n_dedup:,} 行"
    )

    surviving_pls = set(pt["playlist_id"].unique())
    pm = pm[pm["playlist_id"].isin(surviving_pls)]
    pm_n_final = len(pm)
    logger.info(
        f"过滤至有效 playlist_id：{pm_n_dedup:,} → {pm_n_final:,} 行，"
        f"丢弃 {pm_n_dedup - pm_n_final:,} 行"
    )

    out_pm = os.path.join(OUT_DIR, "playlist_meta.parquet")
    pm.to_parquet(out_pm, index=False)
    logger.info(f"输出 playlist_meta.parquet：{pm_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 5e  love ──────────────────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5e love ──")

    lv = pd.read_csv(os.path.join(RAW_DIR, "love.csv"),
                     usecols=["user_id", "track_id"], low_memory=False)
    lv_n_load = len(lv)

    lv = lv.drop_duplicates(subset=["user_id", "track_id"], keep="first")
    lv_n_dedup = len(lv)
    logger.info(
        f"去重（user_id, track_id）：{lv_n_load:,} → {lv_n_dedup:,} 行，"
        f"丢弃 {lv_n_load - lv_n_dedup:,} 行"
    )

    lv["track_id"] = pd.to_numeric(lv["track_id"], errors="coerce")
    lv = lv[lv["track_id"].isin(audio_ids)]
    lv_n_final = len(lv)
    logger.info(
        f"过滤 track_id：→ {lv_n_final:,} 行，"
        f"丢弃 {lv_n_dedup - lv_n_final:,} 行"
    )

    out_lv = os.path.join(OUT_DIR, "love_filtered.parquet")
    lv.to_parquet(out_lv, index=False)
    logger.info(f"输出 love_filtered.parquet：{lv_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 5f  users ─────────────────────────────────────────────────────────────
    t0 = time.time()
    logger.info("── 5f users ──")

    us = pd.read_csv(os.path.join(RAW_DIR, "users.csv"),
                     usecols=["user_id"], low_memory=False)
    us_n_load = len(us)

    us = us.drop_duplicates(subset="user_id", keep="first")
    us_n_final = len(us)
    logger.info(
        f"去重（user_id）：{us_n_load:,} → {us_n_final:,} 行，"
        f"丢弃 {us_n_load - us_n_final:,} 行"
    )

    out_us = os.path.join(OUT_DIR, "users_filtered.parquet")
    us.to_parquet(out_us, index=False)
    logger.info(f"输出 users_filtered.parquet：{us_n_final:,} 行，耗时 {time.time()-t0:.0f}s")

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    total_elapsed  = time.time() - stage_start
    mins, secs     = divmod(int(total_elapsed), 60)
    logger.info("════ 第五阶段完成 ════")
    logger.info(f"session_tracks：  {st_n_final:,} 行")
    logger.info(f"session_meta：    {sm_n_final:,} 行")
    logger.info(f"playlist_tracks： {pt_n_final:,} 行")
    logger.info(f"playlist_meta：   {pm_n_final:,} 行")
    logger.info(f"love_filtered：   {lv_n_final:,} 行")
    logger.info(f"users_filtered：  {us_n_final:,} 行")
    logger.info(f"总耗时：{mins}m {secs:02d}s")

    return {
        "session_tracks":  st_n_final,
        "session_meta":    sm_n_final,
        "playlist_tracks": pt_n_final,
        "playlist_meta":   pm_n_final,
        "love_filtered":   lv_n_final,
        "users_filtered":  us_n_final,
    }
