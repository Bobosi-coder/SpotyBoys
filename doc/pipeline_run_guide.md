# MLOps Pipeline 运行手册

本文档覆盖从原始数据到 GRU Ranker 训练的完整流水线，列出每一步的运行命令、可调超参数及 artifact 依赖关系。

---

## 整体流程

```
原始数据 (data/raw/)
    │
    ▼
【Stage A】构建 Item2Vec 语料           src/item2vec/stage_a_corpus.py
    │  artifacts/item2vec/item2vec_corpus.parquet
    ▼
【Stage B】训练 Item2Vec 嵌入           src/item2vec/stage_b_train.py
    │  artifacts/item2vec/item2vec_128d.npy
    │  artifacts/item2vec/item2vec_model.bin
    │  artifacts/item2vec/item2vec_catalog.csv
    ▼
【Stage C】验证嵌入质量（可选）          src/item2vec/stage_c_validate.py
    │  logs/item2vec_neighbors_sample.txt
    ▼
【Stage D】过滤 & 构建下游数据集         src/item2vec/stage_d_filter.py
    │  artifacts/item2vec/session_tracks_i2v.parquet
    │  artifacts/item2vec/session_meta_i2v.parquet
    │  artifacts/item2vec/playlist_tracks_i2v.parquet
    │  artifacts/item2vec/love_filtered_i2v.parquet
    │
    ├──────────────────────────────────────────────────┐
    ▼                                                  ▼
【Retriever-1】会话划分                       【Retriever-2】共现矩阵
src/retriever/split/build.py              src/retriever/cooc/build.py
artifacts/retriever/split/               artifacts/retriever/cooc/
    │                                          │
    ├──────────┬────────────────────────────────┘
    ▼          ▼
【Retriever-3】用户偏好聚类        【Retriever-4】热门度评分
src/retriever/pref_nn/build.py     src/retriever/popularity/build.py
artifacts/retriever/pref_nn/       artifacts/retriever/popularity/
    │                                          │
    └──────────────────┬────────────────────────┘
                       ▼
              【Ranker-Data】构建训练数据
              src/ranker/data/build.py
              artifacts/ranker/ranker_{train,val}.parquet
                       │
                       ▼
              【Ranker-Train】训练 GRU Ranker
              src/ranker/train.py
              artifacts/ranker/gru_ranker.pt
```

> Retriever 的四个子步骤（split / cooc / pref_nn / popularity）之间无强依赖，
> 但 cooc 依赖 split，pref_nn 依赖 split（间接，通过过滤数据），
> popularity 完全独立。建议按编号顺序执行。

---

## Stage A — 构建 Item2Vec 语料

```bash
uv run python -m src.item2vec.stage_a_corpus
```

**输入**
| 文件 | 说明 |
|------|------|
| `data/raw/.../session_tracks.csv` | 原始会话-曲目记录 |

**输出**
| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/item2vec_corpus.parquet` | 按 session 分组的序列 |

**可调超参数**（修改源文件常量）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `MIN_SEQ` | `2` | 保留的最短序列长度 |
| `KEEP_LABELS` | `{"positive","neutral"}` | 纳入语料的标签类型 |
| `CHUNK_SIZE` | `500_000` | CSV 分块读取大小 |

---

## Stage B — 训练 Item2Vec 嵌入

```bash
uv run python -m src.item2vec.stage_b_train
```

**输入**
| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/item2vec_corpus.parquet` | Stage A 输出 |
| `data/raw/.../tracks.csv` | 曲目元信息（用于构建 catalog） |

**输出**
| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/item2vec_128d.npy` | (N, 128) float32 嵌入矩阵 |
| `artifacts/item2vec/item2vec_model.bin` | Gensim 模型文件 |
| `artifacts/item2vec/item2vec_track_to_row.json` | track_id → 矩阵行号 |
| `artifacts/item2vec/item2vec_catalog.csv` | 词表 |

**可调超参数**（修改 `run()` 函数默认值或调用时传参）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `vector_size` | `128` | 嵌入维度 |
| `window` | `10` | Skip-gram 上下文窗口 |
| `min_count` | `5` | 词频下限（过滤低频曲目） |
| `negative` | `15` | 负采样数 |
| `epochs` | `10` | 训练轮数 |
| `workers` | `8` | 并行线程数（建议 = CPU 核心数） |

---

## Stage C — 验证嵌入质量（可选）

```bash
uv run python -m src.item2vec.stage_c_validate
```

**输入** `artifacts/item2vec/item2vec_model.bin`

**输出** `logs/item2vec_neighbors_sample.txt`（最近邻样本，人工检查用）

无超参数，仅作定性验证。

---

## Stage D — 过滤 & 构建下游数据集

```bash
uv run python -m src.item2vec.stage_d_filter
```

**输入**
| 文件 |
|------|
| `data/raw/.../session_tracks.csv` |
| `data/raw/.../session_meta.csv` |
| `data/raw/.../playlist_tracks.csv` |
| `data/raw/.../love.csv` |
| `data/raw/.../users.csv` |
| `artifacts/item2vec/item2vec_catalog.csv` |

**输出**
| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` | 带 label 的会话曲目 |
| `artifacts/item2vec/session_meta_i2v.parquet` | 会话元信息 |
| `artifacts/item2vec/playlist_tracks_i2v.parquet` | 歌单曲目 |
| `artifacts/item2vec/love_filtered_i2v.parquet` | 收藏记录 |

**可调超参数**

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `PLAYRATIO_CAP` | `5.0` | play_ratio 上限（异常值截断） |
| `MIN_SEQ` | `2` | 最短会话长度 |
| `CHUNK_SIZE` | `500_000` | CSV 分块大小 |

---

## Retriever-1 — 会话划分

```bash
uv run python -m src.retriever.split.build
```

**输入** `artifacts/item2vec/session_meta_i2v.parquet`

**输出**
```
artifacts/retriever/split/split_train.npy   # ~75% sessions
artifacts/retriever/split/split_val.npy     # ~12.5%
artifacts/retriever/split/split_test.npy    # ~12.5%
```

**可调超参数**

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `TRAIN_FRAC` | `0.75` | 训练集比例 |
| `VAL_FRAC` | `0.125` | 验证集比例（测试集 = 1 - train - val） |
| `SEED` | `42` | 随机种子 |

---

## Retriever-2 — 共现矩阵

```bash
uv run python -m src.retriever.cooc.build
```

**输入**
| 文件 |
|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` |
| `artifacts/item2vec/playlist_tracks_i2v.parquet` |
| `artifacts/item2vec/item2vec_catalog.csv` |
| `artifacts/retriever/split/split_train.npy` |

**输出**
```
artifacts/retriever/cooc/cooc_session.npz    # C_sess: 会话邻接矩阵
artifacts/retriever/cooc/cooc_playlist.npz   # C_pl:   歌单共现矩阵
```

**可调超参数**

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `PL_MAX_DIST` | `5` | 歌单中共现对的最大位置距离；权重 = 1/dist |
| `KEEP_LABELS` | `{"positive","neutral"}` | C_sess 纳入的标签 |

---

## Retriever-3 — 用户偏好聚类（Preference NN）

```bash
uv run python -m src.retriever.pref_nn.build
```

**输入**
| 文件 |
|------|
| `artifacts/item2vec/love_filtered_i2v.parquet` |
| `artifacts/item2vec/session_tracks_i2v.parquet` |
| `artifacts/item2vec/item2vec_128d.npy` |
| `artifacts/item2vec/item2vec_track_to_row.json` |

**输出** `artifacts/retriever/pref_nn/user_centroids.pkl`

**可调超参数**

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `LOVE_WEIGHT` | `3` | 收藏曲目在聚类中的重复权重 |
| `POS_CAP` | `300` | 每用户最近正向 session 事件上限 |
| `K1_THRESHOLD` | `100` | combined < 100 → K=1 聚类（约 18% 用户） |
| `K2_THRESHOLD` | `300` | combined 100~300 → K=2（约 32%），>300 → K=3（约 50%） |

---

## Retriever-4 — 热门度评分

```bash
uv run python -m src.retriever.popularity.build
```

**输入** `artifacts/item2vec/session_tracks_i2v.parquet`

**输出** `artifacts/retriever/popularity/pop_scores.csv`（`pop_score = log(1 + count)`，降序）

无超参数，计算规则固定。

---

## Ranker-Data — 构建训练数据

```bash
# 开发调试（快速）
uv run python -m src.ranker.data.build --max-train-sessions 50000

# 完整构建（~1.3M 训练会话，耗时较长）
uv run python -m src.ranker.data.build
```

**输入**
| 文件 |
|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` |
| `artifacts/item2vec/item2vec_catalog.csv` |
| `artifacts/retriever/split/split_{train,val}.npy` |
| `artifacts/retriever/cooc/cooc_session.npz` |
| `artifacts/retriever/popularity/pop_scores.csv` |

**输出**
```
artifacts/ranker/ranker_train.parquet      # 完整约 4-5 GB；50K sessions 约 200 MB
artifacts/ranker/ranker_val.parquet        # 约 30 MB
artifacts/ranker/neg_sample_weights.npy   # (N_vocab,) float32
```

**CLI 参数**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-train-sessions` | `None`（全量） | 截断训练会话数，用于快速验证 |
| `--max-val-sessions` | `None`（全量） | 截断验证会话数 |
| `--seed` | `42` | 随机采样种子 |

**可调常量**（修改源文件）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `L` | `20` | 前缀长度（左填充） |
| `N_HARD` | `3` | 每个 context 的硬负样本数（来自 C_sess） |
| `N_RAND` | `2` | 每个 context 的随机负样本数（∝ pop^0.75） |

---

## Ranker-Train — 训练 GRU Ranker

```bash
# 开发调试
uv run python -m src.ranker.train --epochs 1 --batch-size 256

# 完整训练（需要 GPU）
uv run python -m src.ranker.train
```

**输入**
| 文件 |
|------|
| `artifacts/ranker/ranker_train.parquet` |
| `artifacts/ranker/ranker_val.parquet` |
| `artifacts/item2vec/item2vec_128d.npy` |
| `artifacts/item2vec/item2vec_track_to_row.json` |
| `artifacts/retriever/pref_nn/user_centroids.pkl` |

**输出**
```
artifacts/ranker/gru_ranker.pt             # 最佳 NDCG@5 checkpoint
artifacts/ranker/gru_ranker_config.json   # 模型超参配置
```

**CLI 参数**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--epochs` | `3` | 训练轮数 |
| `--batch-size` | `512` | 每批 context 数（实际前向 512×6=3072 行） |
| `--lr` | `1e-4` | AdamW 学习率 |
| `--weight-decay` | `1e-4` | AdamW 权重衰减 |
| `--max-norm` | `1.0` | 梯度裁剪阈值 |
| `--device` | `"auto"` | 设备选择：`auto` / `cuda` / `mps` / `cpu` |
| `--mlflow-experiment` | `"gru-ranker-training"` | MLflow 实验名 |
| `--run-name` | `"gru-ranker"` | MLflow run 名称 |

**模型固定超参**（修改 `src/ranker/model.py` 常量后重新训练）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `D_EMB` | `128` | 嵌入维度（与 Item2Vec 一致） |
| `N_LABELS` | `4` | 标签词表大小（positive/neutral/skip/pad） |
| `L_PREFIX` | `20` | 最大前缀长度 |
| GRU `n_layers` | `2` | GRU 层数 |
| GRU `dropout` | `0.1` | Dropout 比例 |
| ScoringHead | `387→256→64→1` | MLP 结构（128×3+3 → 256 → 64 → 1） |

---

## 完整顺序执行（一键式参考）

```bash
# ── Item2Vec ─────────────────────────────────────────────────
uv run python -m src.item2vec.stage_a_corpus
uv run python -m src.item2vec.stage_b_train
uv run python -m src.item2vec.stage_c_validate   # 可选，用于人工质检
uv run python -m src.item2vec.stage_d_filter

# ── Retriever（可并行执行 pref_nn 和 popularity）─────────────
uv run python -m src.retriever.split.build
uv run python -m src.retriever.cooc.build
uv run python -m src.retriever.pref_nn.build
uv run python -m src.retriever.popularity.build

# ── Ranker ───────────────────────────────────────────────────
uv run python -m src.ranker.data.build
uv run python -m src.ranker.train
```

---

## MLflow 实验索引

| 步骤 | 实验名 |
|------|--------|
| Stage B | `item2vec-training` |
| Retriever split | `retriever-split` |
| Retriever cooc | `retriever-cooc` |
| Retriever pref_nn | `retriever-pref-nn` |
| Retriever popularity | `retriever-popularity` |
| Ranker data build | `ranker-data-build` |
| Ranker training | `gru-ranker-training` |

查看 MLflow UI：

```bash
uv run mlflow ui --port 5000
# 浏览器访问 http://localhost:5000
```
