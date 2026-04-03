## Vibe Coding Prompt：Spoty Boys — 数据预处理流水线

### 项目背景

你正在为 **Spoty Boys** 构建数据预处理流水线。Spoty Boys 是一个实时自适应音乐推荐系统，整体架构为四阶段流水线：轨道编码器 → 多路召回检索器 → Transformer 排序器 → 策略重排器。本流水线的目标是将原始 30Music 数据集处理为所有下游训练组件所需的标准化产物。

原始数据位于 `data/raw/content/30music_parsed/`，结构化输出写入 `data/processed/`，音频嵌入写入外接硬盘。本流水线共包含 **5 个有效阶段**（降维部分另行处理，不在本 prompt 范围内）。

---

### 日志规范（全流水线统一）

**所有阶段共享同一套日志系统**，在流水线启动时初始化，贯穿全程。

```
logs/
├── pipeline.log          # 主日志：所有阶段的 INFO / WARNING / ERROR 统一写入
├── stage1_catalog.log    # 第一阶段专属详细日志
├── stage2_playcount.log  # 第二阶段专属详细日志
├── stage3_merge.log      # 第三阶段专属详细日志
├── stage4_embed/         # 第四阶段按批次拆分
│   ├── batch_0000.log
│   ├── batch_0001.log
│   └── ...
└── stage5_filter.log     # 第五阶段专属详细日志
```

日志初始化代码（在 `main()` 入口处调用一次）：

```python
import logging
import os
from datetime import datetime

def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(os.path.join(log_dir, "stage4_embed"), exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # 根 logger：写入 pipeline.log + 控制台
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(
        os.path.join(log_dir, "pipeline.log"), encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)
    root.addHandler(ch)

def get_stage_logger(name, log_path):
    """为每个阶段创建独立 logger，同时写入阶段专属文件和 pipeline.log。"""
    logger = logging.getLogger(name)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        "%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    return logger
```

每个阶段必须记录以下内容：
- **阶段开始**：时间戳、输入文件路径、输入行数。
- **去重报告**：原始行数、去重后行数、丢弃行数。
- **过滤报告**：每一步过滤前后的行数变化及原因。
- **阶段结束**：输出文件路径、输出行数、耗时。
- **WARNING**：任何异常数据（如超出预期的空值率、意外的去重量）。
- **ERROR**：任何导致行数据被丢弃的异常，附 `track_id` 或 `session_id`。

---

### 输入文件一览

| 文件 | 行数 | 主键 | 说明 |
|---|---|---|---|
| `tracks.csv` | ~567 万 | `track_id` | 可能含重复行 |
| `events.csv` | 较大 | `event_id` | 原始播放日志 |
| `users.csv` | ~4.5 万 | `user_id` | 用户参考表 |
| `love.csv` | ~410 万 | `(user_id, track_id)` | 显式喜爱信号 |
| `playlist_meta.csv` | ~5.7 万 | `playlist_id` | 播放列表归属 |
| `playlist_tracks.csv` | ~160 万 | `(playlist_id, track_id)` | 播放列表-曲目关联 |
| `session_meta.csv` | ~276 万 | `session_id` | 会话归属 |
| `session_tracks.csv` | ~3140 万 | `(session_id, position)` | 核心交互数据 |

各文件字段结构：`tracks.csv`：`track_id, mbid, duration, playcount, raw_name, artist_hint, title, artist_ids, album_ids, tag_ids`。`events.csv`：`event_id, timestamp, user_id, track_id, playtime`。`love.csv`：`pref_id, timestamp, user_id, track_id, value`。`playlist_tracks.csv`：`playlist_id, user_id, position, track_id`。`session_tracks.csv`：`session_id, user_id, position, track_id, playstart, playtime, playratio, action, label`。

---

### 第一阶段 — 构建去重曲目目录

**目标：** 从原始 `tracks.csv` 中生成每个 `track_id` 唯一对应一行的干净目录。

**处理步骤：**

1. 加载 `tracks.csv`，仅保留 `track_id`、`artist_hint`、`title` 三列。丢弃原因：`mbid` 有 78.9% 为空；`duration` 有 48% 为 `-1`；`playcount` 有 22.5% 为空且存在异常负值；其余字段本阶段不需要。记录加载行数。

2. **去重（主键：`track_id`）：** 对 `track_id` 去重，保留第一次出现的行。记录原始行数、去重后行数、丢弃行数。若丢弃行数超过总行数的 5%，输出 WARNING。

3. 保存为 `processed/tracks_unique.csv`。记录输出行数和耗时。

**日志示例：**
```
2026-01-01 10:00:00 | INFO     | stage1 | 加载 tracks.csv：5,675,143 行
2026-01-01 10:00:45 | INFO     | stage1 | 去重（track_id）：5,675,143 → 4,982,011 行，丢弃 693,132 行（12.2%）
2026-01-01 10:00:45 | WARNING  | stage1 | 去重丢弃率 12.2% 超过预期阈值 5%，请核查原始数据
2026-01-01 10:00:46 | INFO     | stage1 | 输出 tracks_unique.csv：4,982,011 行，耗时 46s
```

**输出：** `processed/tracks_unique.csv`，字段 `track_id, artist_hint, title`。

---

### 第二阶段 — 从事件日志计算曲目播放次数

**目标：** 通过统计原始事件日志独立推导每首曲目的干净播放计数。

**处理步骤：**

1. 仅加载 `events.csv` 的 `event_id` 和 `track_id` 列。记录加载行数。

2. **去重（主键：`event_id`）：** 对 `event_id` 去重，保留第一次出现的行。记录丢弃行数。

3. 按 `track_id` 分组统计出现次数，得到 `event_count`。

4. 保存为 `processed/event_playcount.csv`，字段 `track_id, event_count`。记录唯一 `track_id` 数量和耗时。

**日志示例：**
```
2026-01-01 10:01:00 | INFO     | stage2 | 加载 events.csv：31,200,000 行
2026-01-01 10:01:30 | INFO     | stage2 | 去重（event_id）：31,200,000 → 31,198,754 行，丢弃 1,246 行
2026-01-01 10:02:10 | INFO     | stage2 | 统计完成：覆盖 475,238 个唯一 track_id
2026-01-01 10:02:11 | INFO     | stage2 | 输出 event_playcount.csv，耗时 71s
```

**输出：** `processed/event_playcount.csv`，字段 `track_id, event_count`。

---

### 第三阶段 — 合并曲目目录与播放计数

**目标：** 生成包含干净播放计数和下游所需派生字段的权威曲目目录。

**处理步骤：**

1. 加载 `tracks_unique.csv` 和 `event_playcount.csv`。

2. **再次确认无重复（主键：`track_id`）：** 对两张表分别检查 `track_id` 唯一性。若发现重复（理论上不应出现，因第一、二阶段已去重），记录 ERROR 并再次去重后继续。

3. 在 `track_id` 上做左连接。未出现在 `events.csv` 中的曲目，`event_count` 填充为 `0`。记录填充为 0 的曲目数量。

4. 将合并列重命名为 `final_playcount`，删除原始 `event_count` 列。

5. 添加两个派生字段：
   - `pop_score = log(1 + final_playcount)`：用于流行度分支排序及 C4 兜底。
   - `neg_sample_weight = final_playcount^0.75`，归一化使全列之和为 1：用于 C3 训练时的随机负样本采样。

6. 保存为 `processed/catalog.csv`。记录输出行数、`final_playcount` 的基本统计（min / max / mean / 零值比例）及耗时。

**日志示例：**
```
2026-01-01 10:02:30 | INFO     | stage3 | 左连接完成：4,982,011 行
2026-01-01 10:02:30 | INFO     | stage3 | final_playcount=0 的曲目：312,488 条（6.3%）
2026-01-01 10:02:30 | INFO     | stage3 | final_playcount 统计：min=0, max=284,312, mean=63.2
2026-01-01 10:02:31 | INFO     | stage3 | 输出 catalog.csv：4,982,011 行，耗时 31s
```

**输出：** `processed/catalog.csv`，字段 `track_id, artist_hint, title, final_playcount, pop_score, neg_sample_weight`。

---

### 第四阶段 — 下载-嵌入流水线（Download → Embed → Delete）

**目标：** 为目录中每首曲目提取 2048 维 PANNs 音频嵌入。由于一次性下载全部 MP3 会占用不可接受的存储空间，本阶段采用**批次滚动流水线**：每批次下载一组 MP3 → 提取嵌入 → 立即删除该批 MP3，仅保留嵌入向量。

**存储路径约定：**
- MP3 临时存储：`/Volumes/T7/MLOps_music_track/`（批次结束后清空）
- 嵌入持久存储：`/Volumes/T7/MLOps_music_embedding/`
- 批次日志：`logs/stage4_embed/batch_{NNNN}.log`

**模型说明：**
- 使用 `panns_inference.AudioTagging`，checkpoint `Cnn14_mAP=0.431.pth`，`eval` 模式，权重冻结。
- 提取**倒数第二层的 2048 维嵌入**（ReLU 激活，输出非负且稀疏）。
- PANNs 内部处理重采样（32 kHz）和梅尔频谱图，无需手动预处理。

**整体流程：**

```
catalog.csv
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  初始化：加载/创建 manifest，跳过已完成曲目（断点续传）              │
│                                                                  │
│  外层循环：按 BATCH_SIZE 分批遍历所有 pending 曲目                  │
│                                                                  │
│  ┌─── 步骤 1：并发下载本批 MP3 ─────────────────────────────┐    │
│  │  ThreadPoolExecutor(max_workers=5)                       │    │
│  │  Deezer API 搜索 → 下载 preview                          │    │
│  │  写入 /Volumes/T7/MLOps_music_track/{track_id}.mp3      │    │
│  │  记录每首：download_status ∈ {ok, failed}                │    │
│  │  日志：本批下载成功率、失败 track_id 列表                  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─── 步骤 2：PANNs 嵌入提取 ───────────────────────────────┐    │
│  │  仅处理 download_status=ok 的曲目                         │    │
│  │  librosa.load → PANNs.inference → 2048-d 向量            │    │
│  │  记录每首：embed_status ∈ {ok, failed}                   │    │
│  │  追加写入嵌入缓冲区                                        │    │
│  │  日志：本批嵌入成功率、失败 track_id 及异常信息             │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─── 步骤 3：持久化本批嵌入 ────────────────────────────────┐    │
│  │  将本批嵌入追加写入                                         │    │
│  │  /Volumes/T7/MLOps_music_embedding/raw_audio_2048d.npy   │    │
│  │  增量更新 track_id_to_row.json                            │    │
│  │  日志：追加后矩阵总行数、当前 .npy 文件大小                  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─── 步骤 4：删除本批 MP3 ──────────────────────────────────┐    │
│  │  os.remove 清除本批所有 MP3（无论嵌入是否成功）              │    │
│  │  日志：删除文件数、释放空间（MB）                            │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                                  │
│  更新全局 manifest，打印批次摘要，进入下一批                        │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
raw_audio_2048d.npy        /Volumes/T7/MLOps_music_embedding/
track_id_to_row.json       /Volumes/T7/MLOps_music_embedding/
download_manifest.csv      data/processed/
audio_catalog.csv          data/processed/
```

**详细实现说明：**

```python
import os, json, time, logging
import numpy as np
import pandas as pd
import requests
import librosa
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from panns_inference import AudioTagging

# ── 下载单首曲目 ────────────────────────────────────────────────────
def download_track(row, output_dir, logger):
    track_id = row['track_id']
    output_path = os.path.join(output_dir, f"{track_id}.mp3")

    if os.path.exists(output_path):           # 幂等：已存在则跳过
        return track_id, True

    query = f"{row['artist_hint']} {row['title']}"
    try:
        search_url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
        resp = requests.get(search_url, timeout=10).json()
        if not resp.get('data'):
            logger.warning(f"[DOWNLOAD FAIL] track_id={track_id}：Deezer 无结果")
            return track_id, False
        preview_url = resp['data'][0]['preview']
        if not preview_url:
            logger.warning(f"[DOWNLOAD FAIL] track_id={track_id}：preview_url 为空")
            return track_id, False
        r = requests.get(preview_url, stream=True, timeout=10)
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return track_id, True
    except Exception as e:
        logger.error(f"[DOWNLOAD ERROR] track_id={track_id}：{e}")
        return track_id, False

# ── 嵌入提取 + 删除 MP3 ─────────────────────────────────────────────
def embed_and_cleanup(successful_ids, mp3_dir, panns_model, logger):
    embeddings = {}
    for track_id in successful_ids:
        mp3_path = os.path.join(mp3_dir, f"{track_id}.mp3")
        try:
            waveform, _ = librosa.load(mp3_path, sr=None, mono=True)
            waveform = waveform[np.newaxis, :]
            _, emb = panns_model.inference(waveform)   # (1, 2048)
            embeddings[track_id] = emb[0]
        except Exception as e:
            logger.error(f"[EMBED ERROR] track_id={track_id}：{e}")
        finally:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)                    # 无论成功与否，删除 MP3
    return embeddings

# ── 持久化嵌入（追加模式）───────────────────────────────────────────
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

    new_ids = list(embeddings.keys())
    new_matrix = np.array([embeddings[tid] for tid in new_ids], dtype="float32")

    start_row = len(existing)
    for i, tid in enumerate(new_ids):
        row_map[str(tid)] = start_row + i

    merged = np.concatenate([existing, new_matrix], axis=0)
    np.save(npy_path, merged)
    with open(row_map_path, "w") as f:
        json.dump(row_map, f)

    logger.info(
        f"[EMBED SAVE] 追加 {len(new_ids)} 条，矩阵总行数 {len(merged)}，"
        f"文件大小 {merged.nbytes / 1e9:.2f} GB"
    )
```

**批次主循环：**

```python
def run_pipeline(catalog_path, mp3_dir, embedding_dir, manifest_path,
                 batch_size=500, max_workers=5, limit=None):

    setup_logging()
    main_logger = logging.getLogger("stage4")

    os.makedirs(mp3_dir, exist_ok=True)
    os.makedirs(embedding_dir, exist_ok=True)

    npy_path      = os.path.join(embedding_dir, "raw_audio_2048d.npy")
    row_map_path  = os.path.join(embedding_dir, "track_id_to_row.json")

    # 加载 catalog，去重（主键：track_id）
    df = pd.read_csv(catalog_path)
    before = len(df)
    df = df.drop_duplicates(subset="track_id", keep="first")
    main_logger.info(f"catalog 去重：{before} → {len(df)} 行，丢弃 {before - len(df)} 行")

    # 加载/初始化 manifest
    if os.path.exists(manifest_path):
        manifest = pd.read_csv(manifest_path, index_col="track_id")
        main_logger.info(f"断点续传：manifest 已存在，{len(manifest)} 条记录")
    else:
        manifest = pd.DataFrame(
            index=df["track_id"],
            columns=["download_status", "embed_status"],
            data="pending"
        )

    # 仅处理 pending 的曲目
    pending = df[manifest.loc[df["track_id"]]["download_status"].values == "pending"]
    if limit:
        pending = pending.head(limit)
        main_logger.info(f"[TEST MODE] 限制处理前 {limit} 条曲目")

    main_logger.info(f"待处理曲目：{len(pending)} 条，批次大小：{batch_size}")

    # 加载 PANNs 模型（一次性，批次间复用）
    panns_model = AudioTagging(checkpoint_path="Cnn14_mAP=0.431.pth", device="cpu")
    main_logger.info("PANNs CNN14 加载完成")

    batches = [pending.iloc[i:i+batch_size] for i in range(0, len(pending), batch_size)]
    total_dl_ok = total_emb_ok = 0

    for batch_idx, batch_df in enumerate(batches):
        batch_logger = get_stage_logger(
            f"stage4.batch{batch_idx:04d}",
            f"logs/stage4_embed/batch_{batch_idx:04d}.log"
        )
        batch_start = time.time()
        batch_logger.info(f"=== 批次 {batch_idx:04d} 开始，共 {len(batch_df)} 首 ===")

        # 步骤 1：并发下载
        dl_ok, dl_fail = [], []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(download_track, row, mp3_dir, batch_logger): row["track_id"]
                for _, row in batch_df.iterrows()
            }
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc=f"Batch {batch_idx:04d} Download"):
                tid, ok = future.result()
                (dl_ok if ok else dl_fail).append(tid)
                manifest.loc[tid, "download_status"] = "ok" if ok else "failed"

        batch_logger.info(
            f"[DOWNLOAD] 成功 {len(dl_ok)}/{len(batch_df)}，"
            f"失败 {len(dl_fail)} 条：{dl_fail[:10]}{'...' if len(dl_fail)>10 else ''}"
        )

        # 步骤 2：嵌入提取 + 步骤 4：删除 MP3（embed_and_cleanup 内部完成删除）
        embeddings = embed_and_cleanup(dl_ok, mp3_dir, panns_model, batch_logger)
        emb_fail = [tid for tid in dl_ok if tid not in embeddings]

        for tid in dl_ok:
            manifest.loc[tid, "embed_status"] = "ok" if tid in embeddings else "failed"
        for tid in emb_fail:
            batch_logger.error(f"[EMBED FAIL] track_id={tid}")

        batch_logger.info(
            f"[EMBED] 成功 {len(embeddings)}/{len(dl_ok)}，失败 {len(emb_fail)} 条"
        )

        # 步骤 3：持久化本批嵌入
        append_embeddings(embeddings, npy_path, row_map_path, batch_logger)

        # 清理残余 MP3（以防 embed 异常导致未删除）
        leftover = [f for f in os.listdir(mp3_dir) if f.endswith(".mp3")]
        for f in leftover:
            os.remove(os.path.join(mp3_dir, f))
        if leftover:
            batch_logger.warning(f"[CLEANUP] 清理残余 MP3：{len(leftover)} 个")

        # 保存 manifest（每批次持久化，防止中断丢失）
        manifest.to_csv(manifest_path)

        elapsed = time.time() - batch_start
        total_dl_ok  += len(dl_ok)
        total_emb_ok += len(embeddings)
        remaining_batches = len(batches) - batch_idx - 1
        eta_sec = elapsed * remaining_batches
        batch_logger.info(
            f"=== 批次 {batch_idx:04d} 完成，耗时 {elapsed:.1f}s，"
            f"预计剩余时间 {eta_sec/3600:.1f}h ==="
        )

    # 流水线结束汇总
    main_logger.info("=" * 60)
    main_logger.info(f"[STAGE 4 COMPLETE] 总处理曲目：{len(pending)}")
    main_logger.info(f"  下载成功：{total_dl_ok}（{total_dl_ok/len(pending):.1%}）")
    main_logger.info(f"  嵌入成功：{total_emb_ok}（{total_emb_ok/len(pending):.1%}）")
    main_logger.info(f"  嵌入矩阵路径：{npy_path}")
    main_logger.info("=" * 60)

    # 生成 audio_catalog.csv
    ok_ids = manifest[manifest["embed_status"] == "ok"].index
    audio_catalog = df[df["track_id"].isin(ok_ids)].copy()
    audio_catalog.to_csv("data/processed/audio_catalog.csv", index=False)
    main_logger.info(f"audio_catalog.csv 写入完成：{len(audio_catalog)} 条")

    # 合理性检验
    _sanity_check(npy_path, row_map_path, audio_catalog, main_logger)
```

**合理性检验：**

```python
def _sanity_check(npy_path, row_map_path, audio_catalog, logger):
    """随机验证同艺术家曲目对的余弦相似度高于随机对。"""
    import random
    from sklearn.metrics.pairwise import cosine_similarity

    emb = np.load(npy_path)
    with open(row_map_path) as f:
        row_map = json.load(f)

    # 按艺术家分组
    artist_groups = audio_catalog.groupby("artist_hint")["track_id"].apply(list)
    multi = artist_groups[artist_groups.apply(len) >= 2]

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

    mean_same = np.mean(same_sims)
    mean_rand = np.mean(rand_sims)
    passed = mean_same > mean_rand
    level = logging.INFO if passed else logging.WARNING
    logger.log(level,
        f"[SANITY] 同艺术家平均余弦相似度={mean_same:.4f}，"
        f"随机对={mean_rand:.4f}，{'✓ 通过' if passed else '✗ 未通过'}"
    )
```

**流水线入口：**

```python
if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_pipeline(
        catalog_path="data/processed/catalog.csv",
        mp3_dir="/Volumes/T7/MLOps_music_track",
        embedding_dir="/Volumes/T7/MLOps_music_embedding",
        manifest_path="data/processed/download_manifest.csv",
        batch_size=500,
        max_workers=5,
        limit=limit    # 传入小数字（如 50）可先做速度估算
    )
```

**输出：**
- `/Volumes/T7/MLOps_music_embedding/raw_audio_2048d.npy`，形状 `(N_audio, 2048)`，float32
- `/Volumes/T7/MLOps_music_embedding/track_id_to_row.json`
- `data/processed/download_manifest.csv`，字段 `track_id, download_status, embed_status`
- `data/processed/audio_catalog.csv`，下载且嵌入均成功的曲目子集

---

### 第五阶段 — 将所有交互表过滤至音频目录

**目标：** 将每张交互表限制为只包含拥有 PANNs 嵌入的曲目。过滤后长度 ≤ 1 的会话或播放列表整体丢弃。本阶段在第四阶段**全部批次完成后**执行。

**日志初始化：**

```python
logger = get_stage_logger("stage5", "logs/stage5_filter.log")
audio_ids = set(pd.read_csv("data/processed/audio_catalog.csv")["track_id"])
logger.info(f"音频目录加载完成：{len(audio_ids)} 个有效 track_id")
```

每张表处理后记录：原始行数 → 去重后行数 → 过滤后行数 → 丢弃短序列后行数 → 输出行数。

#### 5a — `session_tracks.csv`

**保留字段：** `session_id, user_id, position, track_id, playratio, label`。

**处理步骤：**

1. 加载保留字段，记录行数。
2. **去重（主键：`(session_id, position)`）：** 去重后保留第一次出现的行，记录丢弃行数。
3. 删除 `track_id ∉ audio_catalog` 的行，记录过滤行数。
4. 删除 `label == "unknown"` 的行，记录过滤行数。
5. 将 `playratio` 截断至 `5.0`，记录截断行数（预期约 210,483 行）。
6. 丢弃剩余行数 `< 2` 的会话，记录丢弃会话数。
7. 按 `(session_id, position)` 重新排序，保存为 `processed/session_tracks.parquet`。

**清洗后的标签语义：**

| 标签 | playratio 范围 | 训练目标 y | 样本权重 ω |
|---|---|---|---|
| `positive` | ≥ 0.5 | 1.0 | 1.0 |
| `neutral` | 0.5–0.9 | 0.5 | 0.3 |
| `skip` | < 0.5 | 0.0 | 1.0 |
| `unknown` | null | — | 排除 |

**日志示例：**
```
2026-01-01 14:00:00 | INFO | stage5 | session_tracks 加载：31,351,945 行
2026-01-01 14:00:10 | INFO | stage5 | 去重（session_id, position）：31,351,945 → 31,349,201 行，丢弃 2,744 行
2026-01-01 14:00:25 | INFO | stage5 | 过滤 track_id：→ 18,423,100 行，丢弃 12,926,101 行
2026-01-01 14:00:30 | INFO | stage5 | 过滤 unknown label：→ 17,141,717 行，丢弃 1,281,383 行
2026-01-01 14:00:32 | INFO | stage5 | playratio 截断至 5.0：210,483 行被截断
2026-01-01 14:00:35 | INFO | stage5 | 丢弃长度<2 的会话：→ 16,988,204 行，丢弃 153,513 行
2026-01-01 14:01:00 | INFO | stage5 | 输出 session_tracks.parquet：16,988,204 行，耗时 60s
```

#### 5b — `session_meta.csv`

**保留字段：** `session_id, user_id`（丢弃 `session_ts`）。**去重（主键：`session_id`）**，记录丢弃行数。仅保留在 `session_tracks.parquet` 中仍存在的 `session_id`。保存为 `processed/session_meta.parquet`，记录输出行数。

#### 5c — `playlist_tracks.csv`

**保留字段：** `playlist_id, user_id, position, track_id`（`position` 用于加权共现，不可丢弃）。**去重（主键：`(playlist_id, track_id)`）**，记录丢弃行数。删除 `track_id ∉ audio_catalog` 的行，丢弃剩余行数 `< 2` 的播放列表。保存为 `processed/playlist_tracks.parquet`，记录输出行数。

#### 5d — `playlist_meta.csv`

**保留字段：** `playlist_id, user_id`。**去重（主键：`playlist_id`）**，记录丢弃行数。仅保留在 5c 后仍存在的播放列表。保存为 `processed/playlist_meta.parquet`。

#### 5e — `love.csv`

**保留字段：** `user_id, track_id`（丢弃 `pref_id`、`timestamp`、`value`）。**去重（主键：`(user_id, track_id)`）**，记录丢弃行数。删除 `track_id ∉ audio_catalog` 的行。保存为 `processed/love_filtered.parquet`，记录输出行数。

#### 5f — `users.csv`

**保留字段：** `user_id`（所有人口统计字段空值率 ≥ 25%，不被任何模型组件使用）。**去重（主键：`user_id`）**，记录丢弃行数。保存为 `processed/users_filtered.parquet`。

**第五阶段结束日志汇总：**

```
2026-01-01 14:10:00 | INFO | stage5 | ════ 第五阶段完成 ════
2026-01-01 14:10:00 | INFO | stage5 | session_tracks：16,988,204 行
2026-01-01 14:10:00 | INFO | stage5 | session_meta：  1,054,217 行
2026-01-01 14:10:00 | INFO | stage5 | playlist_tracks：892,341 行
2026-01-01 14:10:00 | INFO | stage5 | playlist_meta：  41,203 行
2026-01-01 14:10:00 | INFO | stage5 | love_filtered：  2,318,904 行
2026-01-01 14:10:00 | INFO | stage5 | users_filtered： 44,891 行
2026-01-01 14:10:00 | INFO | stage5 | 总耗时：10m 02s
```

---

### 不使用的文件

`persons.csv`（仅用于艺术家参考，不进入模型），`albums.csv`（不使用），`tags.csv`（tag 特征直接从 `tracks.csv` 的 `tag_ids` 字段构建）。

---

### 预期输出产物汇总

| 产物 | 位置 | 内容 |
|---|---|---|
| `tracks_unique.csv` | `data/processed/` | 去重后的曲目目录 |
| `event_playcount.csv` | `data/processed/` | 每首曲目的事件计数 |
| `catalog.csv` | `data/processed/` | 含 `pop_score`、`neg_sample_weight` 的完整目录 |
| `download_manifest.csv` | `data/processed/` | 每首曲目的 `download_status` 和 `embed_status` |
| `audio_catalog.csv` | `data/processed/` | 下载且嵌入均成功的曲目子集 |
| `raw_audio_2048d.npy` | `/Volumes/T7/MLOps_music_embedding/` | PANNs 嵌入矩阵，`(N_audio, 2048)` |
| `track_id_to_row.json` | `/Volumes/T7/MLOps_music_embedding/` | `track_id → 行索引` |
| `session_tracks.parquet` | `data/processed/` | 过滤并清洗后的会话事件 |
| `session_meta.parquet` | `data/processed/` | 过滤后的会话元数据 |
| `playlist_tracks.parquet` | `data/processed/` | 过滤后的播放列表-曲目对 |
| `playlist_meta.parquet` | `data/processed/` | 过滤后的播放列表元数据 |
| `love_filtered.parquet` | `data/processed/` | 过滤后的喜爱事件 |
| `users_filtered.parquet` | `data/processed/` | 仅含 `user_id` 的用户表 |
| `logs/pipeline.log` | `logs/` | 全流水线统一日志 |
| `logs/stage*.log` | `logs/` | 各阶段专属详细日志 |
| `logs/stage4_embed/batch_*.log` | `logs/stage4_embed/` | 第四阶段各批次日志 |

---

### 实现注意事项

- **去重是每张表的第一步操作**，必须在任何过滤、合并之前完成，且每次去重后都要写入日志。
- **`track_id_to_row.json` 是全局共享的关键产物**，所有 `.npy` 嵌入矩阵的行索引均以此文件为准，一经写入不得重建。
- **第五阶段必须等第四阶段全部批次完成后再执行**，`audio_catalog.csv` 要到所有批次处理完毕才最终确定。
- **外接硬盘写入是 I/O 瓶颈**，嵌入追加时先累积到内存缓冲区，批次结束后一次性写盘。
- **`raw_audio_2048d.npy` 的降维处理**在独立的后续流水线中完成，不在本 prompt 范围内。