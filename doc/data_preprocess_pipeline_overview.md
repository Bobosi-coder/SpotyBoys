# SpotyBoys 数据预处理 Pipeline 全流程说明

## 1. 文档目的

本文档用于系统介绍当前项目中的数据预处理流水线（Data Preprocess Pipeline），作为后续报告中的流程说明基础。文档覆盖：

1. Pipeline 的整体目标与执行方式
2. 5 个 Stage 的处理逻辑、输入与输出
3. 全部关键输出 CSV 文件清单
4. `pop_score` 与 `neg_sample_weight` 的数学原理及作用

---

## 2. Pipeline 总体目标

该流水线的核心目标是：

1. 从 30Music 原始解析数据中构建高质量曲目目录
2. 统计曲目交互热度并构造训练辅助特征
3. 为可下载音频的曲目生成音频嵌入向量
4. 将会话/歌单/喜爱等交互数据过滤到“有音频特征覆盖”的统一数据宇宙

最终产物用于后续推荐建模与训练数据构建。

---

## 3. 入口与执行

主入口脚本：

- `src/data_pre_process/download_embedding/pipeline.py`

典型运行方式：

```bash
uv run python -m src.data_pre_process.download_embedding.pipeline --stages 1,2,3,4,5
```

说明：

- 默认运行 Stage 1~3
- Stage 4 可通过 `--limit` 做小样本测速
- Stage 5 依赖 Stage 4 产出的 `audio_catalog.csv`

---

## 4. 五个 Stage 详细流程

## Stage 1: 构建去重曲目目录

脚本：`stage1_catalog.py`

输入：

- `data/raw/content/30music_parsed/tracks.csv`

处理逻辑：

1. 仅保留 `track_id`, `artist_hint`, `title`
2. 以 `track_id` 为主键去重（保留首条）
3. 记录去重丢弃率（过高则告警）

输出：

- `data/processed/tracks_unique.csv`

作用：

- 建立后续统计与合并的曲目主表基础，避免同一曲目重复统计。

---

## Stage 2: 事件日志去重与播放次数统计

脚本：`stage2_playcount.py`

输入：

- `data/raw/content/30music_parsed/events.csv`

处理逻辑：

1. 分块读取大体量事件表（降低内存占用）
2. 先做块内 `event_id` 去重，再做跨块 `event_id` 去重
3. 按 `track_id` 统计事件次数（`event_count`）

输出：

- `data/processed/event_playcount.csv`

作用：

- 生成每首曲目的交互热度计数，为流行度特征构造提供基础。

---

## Stage 3: 合并目录并构造训练特征

脚本：`stage3_merge.py`

输入：

- `data/processed/tracks_unique.csv`
- `data/processed/event_playcount.csv`

处理逻辑：

1. 双表按 `track_id` 再次检查并消除重复
2. 左连接（保证曲目目录完整保留）
3. 缺失计数填 0，得到 `final_playcount`
4. 构造 `pop_score`
5. 构造 `neg_sample_weight`

输出：

- `data/processed/catalog.csv`
- 列结构：
  - `track_id`
  - `artist_hint`
  - `title`
  - `final_playcount`
  - `pop_score`
  - `neg_sample_weight`

作用：

- 形成“曲目元信息 + 热度特征 + 负采样分布”的统一目录，供 Stage 4 与后续建模使用。

---

## Stage 4: 音频下载与嵌入提取

脚本：`stage4_embed.py`

输入：

- `data/processed/catalog.csv`

处理逻辑：

1. 读取/初始化 `download_manifest.csv`（支持断点续传）
2. 仅处理状态为 `pending` 的曲目
3. 并发调用 Deezer 搜索并下载 preview MP3
4. 用 PANNs CNN14 提取 2048 维音频嵌入
5. 追加写入嵌入矩阵与 `track_id -> row` 映射
6. 每批落盘 manifest，并清理 MP3 临时文件
7. 所有批次完成后生成 `audio_catalog.csv`
8. 执行一次嵌入合理性 sanity check（同艺术家相似度 vs 随机相似度）

输出：

- `data/processed/download_manifest.csv`
- `data/processed/audio_catalog.csv`
- `/Volumes/T7/MLOps_music_embedding/raw_audio_2048d.npy`
- `/Volumes/T7/MLOps_music_embedding/track_id_to_row.json`

作用：

- 把曲目目录收敛到“有可用音频嵌入”的子集，并提供可直接训练使用的向量表示。

---

## Stage 5: 按音频目录过滤全量交互表

脚本：`stage5_filter.py`

前置条件：

- Stage 4 全部完成，`audio_catalog.csv` 已最终确定

输入：

- `data/processed/audio_catalog.csv`
- `data/raw/content/30music_parsed/session_tracks.csv`
- `data/raw/content/30music_parsed/session_meta.csv`
- `data/raw/content/30music_parsed/playlist_tracks.csv`
- `data/raw/content/30music_parsed/playlist_meta.csv`
- `data/raw/content/30music_parsed/love.csv`
- `data/raw/content/30music_parsed/users.csv`

处理逻辑（核心）：

1. 仅保留 `track_id` 在 `audio_catalog` 中的交互
2. 各表按业务主键去重
3. 过滤低质量样本：
   - 去掉 `label == "unknown"`
   - `playratio` 上截断到 5.0
   - 丢弃长度 < 2 的 session/playlist
4. 输出 Parquet 数据集

输出：

- `data/processed/session_tracks.parquet`
- `data/processed/session_meta.parquet`
- `data/processed/playlist_tracks.parquet`
- `data/processed/playlist_meta.parquet`
- `data/processed/love_filtered.parquet`
- `data/processed/users_filtered.parquet`

作用：

- 得到一个与音频嵌入严格对齐、质量可控的下游训练数据宇宙。

---

## 5. 全部输出 CSV 文件清单

以下为该预处理流水线中直接产出的 CSV 文件：

1. `data/processed/tracks_unique.csv`（Stage 1）
2. `data/processed/event_playcount.csv`（Stage 2）
3. `data/processed/catalog.csv`（Stage 3）
4. `data/processed/download_manifest.csv`（Stage 4）
5. `data/processed/audio_catalog.csv`（Stage 4）

补充：Stage 5 输出为 Parquet，不新增 CSV。

---

## 6. `pop_score` 与 `neg_sample_weight` 的数学原理

在 Stage 3 中，基于 `final_playcount` 构造两个核心统计特征。

### 6.1 `pop_score`

定义：

\[
\text{pop\_score}_i = \log(1 + c_i)
\]

其中：

- \(c_i\) 为曲目 \(i\) 的 `final_playcount`

原理：

1. 原始播放次数通常呈长尾分布，头部曲目计数远高于尾部
2. 直接使用计数会导致尺度过大、训练不稳定
3. 对数变换可压缩头部差距，同时保留相对流行度排序

作用：

- 作为流行度强度特征参与排序/召回建模
- 降低极端热门曲目对模型梯度和采样的主导效应

### 6.2 `neg_sample_weight`

定义：

先构造未归一化权重：

\[
\tilde{w}_i = c_i^{0.75}
\]

再归一化：

\[
\text{neg\_sample\_weight}_i = \frac{\tilde{w}_i}{\sum_j \tilde{w}_j}
\]

当全部 \(c_i=0\) 时，退化为均匀分布：

\[
\text{neg\_sample\_weight}_i = \frac{1}{N}
\]

原理：

1. 负采样若均匀采样，会过度抽到极冷门物品，训练信号弱
2. 按流行度采样更贴近真实曝光分布
3. 指数 `0.75`（次线性）在“热门偏置”和“多样性”之间折中：
   - 若指数过高（接近 1），样本过于集中头部
   - 若指数过低（接近 0），又接近均匀采样

作用：

- 作为负样本抽样概率分布
- 提升训练效率与样本信息密度
- 控制热门项偏置，减少模型过拟合头部物品

---

## 7. 端到端数据流总结

1. `tracks.csv` -> `tracks_unique.csv`
2. `events.csv` -> `event_playcount.csv`
3. `tracks_unique + event_playcount` -> `catalog.csv`（含 `pop_score`、`neg_sample_weight`）
4. `catalog.csv` -> `download_manifest.csv + audio_catalog.csv + audio embedding`
5. `audio_catalog.csv` 约束下过滤交互表 -> 一组训练用 Parquet

该流程将“原始异构日志”转化为“可建模、可追踪、可续跑”的统一数据资产。
