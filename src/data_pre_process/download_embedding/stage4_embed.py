"""
Stage 4 — 下载-嵌入流水线（Download → Embed → Delete）

每批次：并发下载 MP3 → PANNs 提取 2048-d 嵌入 → 删除 MP3 → 持久化嵌入
断点续传：通过 download_manifest.csv 跟踪每首曲目状态

Outputs:
  /Volumes/T7/MLOps_music_embedding/raw_audio_2048d.npy
  /Volumes/T7/MLOps_music_embedding/track_id_to_row.json
  data/processed/download_manifest.csv
  data/processed/audio_catalog.csv
"""
import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

CATALOG_PATH  = "data/processed/catalog.csv"
MANIFEST_PATH = "data/processed/download_manifest.csv"
MP3_DIR       = "/Volumes/T7/MLOps_music_track"
EMBEDDING_DIR = "/Volumes/T7/MLOps_music_embedding"


# ── 下载单首曲目 ──────────────────────────────────────────────────────────────
def download_track(row, output_dir, logger):
    track_id    = row["track_id"]
    output_path = os.path.join(output_dir, f"{track_id}.mp3")

    if os.path.exists(output_path):          # 幂等：已存在则跳过
        return track_id, True

    query = f"{row['artist_hint']} {row['title']}"
    try:
        search_url = (
            "https://api.deezer.com/search?q=" + requests.utils.quote(query)
        )
        resp = requests.get(search_url, timeout=10).json()
        if not resp.get("data"):
            logger.warning(f"[DOWNLOAD FAIL] track_id={track_id}：Deezer 无结果")
            return track_id, False
        preview_url = resp["data"][0]["preview"]
        if not preview_url:
            logger.warning(f"[DOWNLOAD FAIL] track_id={track_id}：preview_url 为空")
            return track_id, False
        r = requests.get(preview_url, stream=True, timeout=10)
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return track_id, True
    except Exception as e:
        logger.error(f"[DOWNLOAD ERROR] track_id={track_id}：{e}")
        return track_id, False


# ── 嵌入提取 + 删除 MP3 ───────────────────────────────────────────────────────
def embed_and_cleanup(successful_ids, mp3_dir, panns_model, logger):
    import librosa

    embeddings = {}
    for track_id in successful_ids:
        mp3_path = os.path.join(mp3_dir, f"{track_id}.mp3")
        try:
            waveform, _ = librosa.load(mp3_path, sr=None, mono=True)
            waveform    = waveform[np.newaxis, :]
            _, emb      = panns_model.inference(waveform)   # (1, 2048)
            embeddings[track_id] = emb[0]
        except Exception as e:
            logger.error(f"[EMBED ERROR] track_id={track_id}：{e}")
        finally:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)          # 无论成功与否，删除 MP3
    return embeddings


# ── 持久化嵌入（追加模式）─────────────────────────────────────────────────────
def append_embeddings(embeddings, npy_path, row_map_path, logger):
    """将本批嵌入追加到 .npy 文件，增量更新 track_id_to_row.json。"""
    if not embeddings:
        return

    # 加载已有映射（若存在）
    if os.path.exists(row_map_path):
        with open(row_map_path) as f:
            row_map = json.load(f)
    else:
        row_map = {}

    # 加载已有嵌入矩阵（若存在）
    if os.path.exists(npy_path):
        existing = np.load(npy_path)
    else:
        existing = np.empty((0, 2048), dtype="float32")

    new_ids    = list(embeddings.keys())
    new_matrix = np.array([embeddings[tid] for tid in new_ids], dtype="float32")

    start_row = len(existing)
    for i, tid in enumerate(new_ids):
        row_map[str(tid)] = start_row + i

    merged = np.concatenate([existing, new_matrix], axis=0)
    np.save(npy_path, merged)
    with open(row_map_path, "w") as f:
        json.dump(row_map, f)

    logger.info(
        f"[EMBED SAVE] 追加 {len(new_ids):,} 条，矩阵总行数 {len(merged):,}，"
        f"文件大小 {merged.nbytes / 1e9:.2f} GB"
    )


# ── 合理性检验 ────────────────────────────────────────────────────────────────
def _sanity_check(npy_path, row_map_path, audio_catalog, logger):
    """随机验证同艺术家曲目对的余弦相似度高于随机对。"""
    from sklearn.metrics.pairwise import cosine_similarity

    emb = np.load(npy_path)
    with open(row_map_path) as f:
        row_map = json.load(f)

    artist_groups = audio_catalog.groupby("artist_hint")["track_id"].apply(list)
    multi         = artist_groups[artist_groups.apply(len) >= 2]

    if len(multi) < 10:
        logger.warning("[SANITY] 艺术家分组不足，跳过检验")
        return

    same_sims, rand_sims = [], []
    for _ in range(20):
        artist = random.choice(multi.index.tolist())
        t1, t2 = random.sample(multi[artist], 2)
        if str(t1) in row_map and str(t2) in row_map:
            v1, v2 = emb[row_map[str(t1)]], emb[row_map[str(t2)]]
            same_sims.append(cosine_similarity([v1], [v2])[0][0])

    all_ids = list(row_map.keys())
    for _ in range(20):
        t1, t2 = random.sample(all_ids, 2)
        v1, v2 = emb[row_map[t1]], emb[row_map[t2]]
        rand_sims.append(cosine_similarity([v1], [v2])[0][0])

    mean_same = float(np.mean(same_sims)) if same_sims else float("nan")
    mean_rand = float(np.mean(rand_sims))
    passed    = mean_same > mean_rand
    level     = logging.INFO if passed else logging.WARNING
    logger.log(level,
        f"[SANITY] 同艺术家平均余弦相似度={mean_same:.4f}，"
        f"随机对={mean_rand:.4f}，{'✓ 通过' if passed else '✗ 未通过'}"
    )


# ── 主流水线 ──────────────────────────────────────────────────────────────────
def run_pipeline(
    catalog_path  = CATALOG_PATH,
    mp3_dir       = MP3_DIR,
    embedding_dir = EMBEDDING_DIR,
    manifest_path = MANIFEST_PATH,
    batch_size    = 500,
    max_workers   = 5,
    limit         = None,
    checkpoint_path = None,   # None → panns_inference 自动下载
):
    try:
        from .pipeline_logging import setup_logging, get_stage_logger
    except ImportError:
        from pipeline_logging import setup_logging, get_stage_logger
    setup_logging()
    main_logger = logging.getLogger("stage4")

    os.makedirs(mp3_dir, exist_ok=True)
    os.makedirs(embedding_dir, exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)

    npy_path     = os.path.join(embedding_dir, "raw_audio_2048d.npy")
    row_map_path = os.path.join(embedding_dir, "track_id_to_row.json")

    # 加载 catalog，去重（主键：track_id）
    df     = pd.read_csv(catalog_path)
    before = len(df)
    df     = df.drop_duplicates(subset="track_id", keep="first")
    df["track_id"] = df["track_id"].astype(int)
    main_logger.info(
        f"catalog 去重：{before:,} → {len(df):,} 行，丢弃 {before-len(df):,} 行"
    )

    # 加载/初始化 manifest（index = track_id int）
    if os.path.exists(manifest_path):
        manifest = pd.read_csv(manifest_path)
        manifest["track_id"] = manifest["track_id"].astype(int)
        manifest = manifest.set_index("track_id")
        main_logger.info(f"断点续传：manifest 已存在，{len(manifest):,} 条记录")
    else:
        manifest = pd.DataFrame({
            "download_status": ["pending"] * len(df),
            "embed_status":    ["pending"] * len(df),
        }, index=pd.Index(df["track_id"].values, name="track_id"))

    # 仅处理 pending 的曲目
    status  = manifest.reindex(df["track_id"])["download_status"].fillna("pending")
    pending = df[status.values == "pending"].reset_index(drop=True)

    if limit:
        pending = pending.head(limit)
        main_logger.info(f"[TEST MODE] 限制处理前 {limit:,} 条曲目")

    main_logger.info(f"待处理曲目：{len(pending):,} 条，批次大小：{batch_size:,}")

    if len(pending) == 0:
        main_logger.info("所有曲目已处理完毕，重新生成 audio_catalog.csv")
        _write_audio_catalog(df, manifest, main_logger)
        return

    # 加载 PANNs 模型（一次性，批次间复用）
    from panns_inference import AudioTagging
    panns_model = AudioTagging(checkpoint_path=checkpoint_path, device="cpu")
    main_logger.info("PANNs CNN14 加载完成")

    batches      = [pending.iloc[i:i+batch_size] for i in range(0, len(pending), batch_size)]
    total_dl_ok  = 0
    total_emb_ok = 0

    for batch_idx, batch_df in enumerate(batches):
        try:
            from .pipeline_logging import get_stage_logger
        except ImportError:
            from pipeline_logging import get_stage_logger
        batch_logger = get_stage_logger(
            f"stage4.batch{batch_idx:04d}",
            f"logs/stage4_embed/batch_{batch_idx:04d}.log",
        )
        batch_start = time.time()
        batch_logger.info(f"=== 批次 {batch_idx:04d} 开始，共 {len(batch_df):,} 首 ===")

        # 步骤 1：并发下载
        dl_ok: list   = []
        dl_fail: list = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_track, row, mp3_dir, batch_logger): row["track_id"]
                for _, row in batch_df.iterrows()
            }
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc=f"Batch {batch_idx:04d} Download", leave=False):
                tid, ok = future.result()
                (dl_ok if ok else dl_fail).append(tid)
                manifest.loc[tid, "download_status"] = "ok" if ok else "failed"

        batch_logger.info(
            f"[DOWNLOAD] 成功 {len(dl_ok):,}/{len(batch_df):,}，"
            f"失败 {len(dl_fail):,} 条：{dl_fail[:10]}{'...' if len(dl_fail)>10 else ''}"
        )

        # 步骤 2：嵌入提取 + 步骤 4：删除 MP3（embed_and_cleanup 内部完成删除）
        embeddings = embed_and_cleanup(dl_ok, mp3_dir, panns_model, batch_logger)
        emb_fail   = [tid for tid in dl_ok if tid not in embeddings]

        for tid in dl_ok:
            manifest.loc[tid, "embed_status"] = "ok" if tid in embeddings else "failed"
        for tid in emb_fail:
            batch_logger.error(f"[EMBED FAIL] track_id={tid}")

        batch_logger.info(
            f"[EMBED] 成功 {len(embeddings):,}/{len(dl_ok):,}，"
            f"失败 {len(emb_fail):,} 条"
        )

        # 步骤 3：持久化本批嵌入
        append_embeddings(embeddings, npy_path, row_map_path, batch_logger)

        # 清理本批残余 MP3（仅删除属于本批的文件，以防 embed 异常导致未删除）
        batch_ids = set(batch_df["track_id"].tolist())
        leftover = [
            f"{tid}.mp3" for tid in batch_ids
            if os.path.exists(os.path.join(mp3_dir, f"{tid}.mp3"))
        ]
        for fname in leftover:
            fp = os.path.join(mp3_dir, fname)
            os.remove(fp)
        if leftover:
            batch_logger.warning(f"[CLEANUP] 清理本批残余 MP3：{len(leftover):,} 个")

        # 保存 manifest（每批次持久化，防止中断丢失）
        manifest.reset_index().to_csv(manifest_path, index=False)

        elapsed          = time.time() - batch_start
        remaining        = len(batches) - batch_idx - 1
        total_dl_ok     += len(dl_ok)
        total_emb_ok    += len(embeddings)
        batch_logger.info(
            f"=== 批次 {batch_idx:04d} 完成，耗时 {elapsed:.1f}s，"
            f"预计剩余时间 {elapsed*remaining/3600:.1f}h ==="
        )

    # 流水线结束汇总
    main_logger.info("=" * 60)
    main_logger.info(f"[STAGE 4 COMPLETE] 总处理曲目：{len(pending):,}")
    main_logger.info(f"  下载成功：{total_dl_ok:,}（{total_dl_ok/len(pending):.1%}）")
    main_logger.info(f"  嵌入成功：{total_emb_ok:,}（{total_emb_ok/len(pending):.1%}）")
    main_logger.info(f"  嵌入矩阵路径：{npy_path}")
    main_logger.info("=" * 60)

    _write_audio_catalog(df, manifest, main_logger)

    # 合理性检验
    if os.path.exists(npy_path) and os.path.exists(row_map_path):
        audio_catalog = pd.read_csv("data/processed/audio_catalog.csv")
        _sanity_check(npy_path, row_map_path, audio_catalog, main_logger)


def _write_audio_catalog(df, manifest, logger):
    ok_ids        = manifest[manifest["embed_status"] == "ok"].index
    audio_catalog = df[df["track_id"].isin(ok_ids)].copy()
    audio_catalog.to_csv("data/processed/audio_catalog.csv", index=False)
    logger.info(f"audio_catalog.csv 写入完成：{len(audio_catalog):,} 条")


# pipeline.py 调用的统一入口
def run(batch_size=500, max_workers=5, limit=None, logger=None,
        checkpoint_path=None):
    if logger:
        logging.getLogger("stage4").addHandler(
            logging.handlers.MemoryHandler(capacity=0) if False else
            type("_NullH", (logging.Handler,), {"emit": lambda s, r: None})()
        )
    run_pipeline(
        batch_size      = batch_size,
        max_workers     = max_workers,
        limit           = limit,
        checkpoint_path = checkpoint_path,
    )


# ── 直接运行入口 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_pipeline(
        catalog_path  = CATALOG_PATH,
        mp3_dir       = MP3_DIR,
        embedding_dir = EMBEDDING_DIR,
        manifest_path = MANIFEST_PATH,
        batch_size    = 500,
        max_workers   = 5,
        limit         = limit,
    )
