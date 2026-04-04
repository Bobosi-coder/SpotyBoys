# Component 2: Multi-Recall Retriever — Build Guide

## Overview

C2 Multi-Recall Retriever 由三个独立的召回分支组成，为下游 GRU Ranker (C3) 提供最多 200 个候选 track：

| 分支 | 候选数 | 信号 | 核心问题 |
|------|--------|------|---------|
| Branch 1: Co-occurrence | 100 | 会话/播放列表共现 | "听完 X 之后通常会听什么？" |
| Branch 2: Preference NN | 80 | Item2Vec 向量相似度 | "与你历史偏好最接近的 track" |
| Branch 3: Popularity | 20 | 全局播放次数 | 冷启动兜底 |

各分支 artifact 独立构建，无流水线顺序依赖（cooc 需要 split 先完成）。

---

## 前置条件

以下 artifact 必须已由 Item2Vec Pipeline (Stage A–D) 生成：

| 文件 | 来源 | 说明 |
|------|------|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` | Stage D | 过滤后的会话播放记录 |
| `artifacts/item2vec/playlist_tracks_i2v.parquet` | Stage D | 过滤后的播放列表记录 |
| `artifacts/item2vec/session_meta_i2v.parquet` | Stage D | 会话元数据（session_id, user_id） |
| `artifacts/item2vec/love_filtered_i2v.parquet` | Stage D | 用户收藏记录 |
| `artifacts/item2vec/item2vec_128d.npy` | Stage B | 嵌入矩阵 (N, 128) float32 |
| `artifacts/item2vec/item2vec_track_to_row.json` | Stage B | `{str(track_id): row_index}` |
| `artifacts/item2vec/item2vec_catalog.csv` | Stage B | vocab track 元数据 |

---

## 模块结构

```
src/retriever/
├── __init__.py
├── split/
│   ├── __init__.py
│   └── build.py          # 数据集划分
├── cooc/
│   ├── __init__.py
│   └── build.py          # 共现矩阵构建
├── pref_nn/
│   ├── __init__.py
│   └── build.py          # 用户 K-Means 聚类中心
├── popularity/
│   ├── __init__.py
│   └── build.py          # 流行度得分
└── retriever.py          # MultiRecallRetriever 服务类
```

输出统一写入 `artifacts/retriever/`（`data/` 仅存放原始/处理后数据）。

---

## Step 1: 数据集划分 (`split/build.py`)

### 运行

```bash
uv run python -m src.retriever.split.build
```

### 输入

| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/session_meta_i2v.parquet` | 全量会话列表 |

### 算法

1. 读取所有唯一 `session_id`（约 172 万条）
2. 使用 `seed=42` 随机打乱
3. 按 **75 / 12.5 / 12.5** 比例切分
4. 断言三个集合无交集

### 输出

| 文件 | 大小 | 内容 |
|------|------|------|
| `artifacts/retriever/split/split_train.npy` | int64 array | 训练集 session_id（~129 万） |
| `artifacts/retriever/split/split_val.npy` | int64 array | 验证集 session_id（~21.6 万） |
| `artifacts/retriever/split/split_test.npy` | int64 array | 测试集 session_id（~21.6 万） |

### 实际结果

```
Total sessions : 1,725,976
Train          : 1,294,482  (75.0%)
Val            :   215,747  (12.5%)
Test           :   215,747  (12.5%)
```

### MLflow

Experiment: `retriever-split` — 记录 seed、split 比例、各集合大小。

---

## Step 2: 共现矩阵构建 (`cooc/build.py`)

### 前置

`artifacts/retriever/split/split_train.npy` 必须已存在。

### 运行

```bash
uv run python -m src.retriever.cooc.build
```

### 输入

| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` | 会话播放记录 |
| `artifacts/item2vec/playlist_tracks_i2v.parquet` | 播放列表记录 |
| `artifacts/retriever/split/split_train.npy` | 训练集 session_id |
| `artifacts/item2vec/item2vec_catalog.csv` | vocab track_id 列表 |

### 算法

**C_sess（会话转移矩阵）**

- 仅使用训练集会话；仅 `positive` + `neutral` 标签；仅相邻 pair
- `C_sess[tid2idx[i_j], tid2idx[i_{j+1}]] += 1`
- 内存安全：parquet → pandas filter → numpy 数组 → `del df` → numpy diff 边界扫描，无 groupby 累积

**C_pl（播放列表共现矩阵）**

- 使用全部播放列表（train + val/test，符合标准 CF 实践）
- 对每个 pair $(i, j)$ 满足 $|\text{pos}_i - \text{pos}_j| \le 5$：
  $C_{pl}[i, j] \mathrel{+}= 1 / |\text{pos}_i - \text{pos}_j|$
- 对称构建（同时写入 $(i,j)$ 和 $(j,i)$）

两个矩阵均使用 COO → CSR 转换，格式为 SciPy float32 sparse。

> **注意**：`item2vec_catalog.csv` 中存在重复 track_id（来自 tracks.csv 的左连接），构建 `tid2idx` 前需去重，否则 enumerate 产生的索引会超出矩阵维度。

### 输出

| 文件 | nnz | 磁盘大小 | 说明 |
|------|-----|---------|------|
| `artifacts/retriever/cooc/cooc_session.npz` | 8,238,824 | ~92 MB | C_sess CSR |
| `artifacts/retriever/cooc/cooc_playlist.npz` | 9,159,402 | ~31.9 MB | C_pl CSR |

矩阵维度均为 (746,609 × 746,609)，密度约 1.5e-05。

### 实际结果

```
Catalog rows    : 915,471  (含重复)
Unique track_ids: 746,609  (N)
C_sess nnz      : 8,238,824   density=1.48e-05
C_pl   nnz      : 9,159,402   density=1.64e-05
构建用时        : ~17s
```

### MLflow

Experiment: `retriever-cooc` — 记录 N_vocab、C_sess/C_pl 的 nnz 和 density。

---

## Step 3: 用户聚类中心 (`pref_nn/build.py`)

### 运行

```bash
uv run python -m src.retriever.pref_nn.build
```

### 输入

| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/love_filtered_i2v.parquet` | 用户收藏记录 |
| `artifacts/item2vec/session_tracks_i2v.parquet` | 会话记录（取 positive 标签） |
| `artifacts/item2vec/item2vec_128d.npy` | 嵌入矩阵 |
| `artifacts/item2vec/item2vec_track_to_row.json` | track_id → 行索引 |

### K 值阈值（基于实际数据分布）

实际数据中 combined history（love×3 + min(正样本, 300)）的分位数：

| 分位数 | 值 |
|-------|-----|
| p10 | 47 |
| p20 | 107 |
| p33 | 156 |
| p50 | 259 |
| p67 | 321 |
| p80 | 387 |

原始规格的阈值（<10 / 10–30）会使约 98% 的用户落入 K=3，无法区分。采用数据驱动阈值：

| K | 条件 | 含义 |
|---|------|------|
| 1 | combined < 100 | 历史稀疏，单一偏好中心 |
| 2 | 100 ≤ combined ≤ 300 | 中等历史，两个偏好簇 |
| 3 | combined > 300 | 丰富历史，三个偏好簇 |

### 算法

1. 加载 love 数据（~136 万行）
2. 加载 session_tracks，过滤 `label == 'positive'`，按 `(user_id, session_id, position)` 排序，每用户取最后 300 条（`groupby("user_id").tail(300)`）
3. 遍历 love 用户 ∪ positive 用户（~4.4 万）：
   - 合并 `love_ids × 3 + pos_ids`，查询嵌入（跳过不在 vocab 中的）
   - 少于 2 个嵌入则跳过
   - 按阈值确定 K，运行 `KMeans(n_clusters=K, n_init=10, random_state=42)`
   - 存储 `[(centroid_128d, cluster_size), ...]`

### 输出

| 文件 | 大小 | 内容 |
|------|------|------|
| `artifacts/retriever/pref_nn/user_centroids.pkl` | 115 MB | `{user_id: [(centroid, size), ...]}` |

### 实际结果

```
总用户数          : 44,191
有聚类中心的用户  : 44,123  (99.8%)
跳过（嵌入 < 2） : 68
K=1              : 8,151  (18.5%)
K=2              : 17,187  (38.9%)
K=3              : 18,785  (42.5%)
构建用时          : ~258s
```

> ConvergenceWarning（"distinct clusters found smaller than n_clusters"）：当用户所有 track 嵌入相同（如重复播放同一首歌），K-Means 无法找到 K 个不同簇心，自动退化为更少的簇。此为正常现象，sklearn 会优雅处理，结果仍有效。

### MLflow

Experiment: `retriever-pref-nn` — 记录 K 阈值参数、用户覆盖率、K 分布。

---

## Step 4: 流行度得分 (`popularity/build.py`)

### 运行

```bash
uv run python -m src.retriever.popularity.build
```

### 输入

| 文件 | 说明 |
|------|------|
| `artifacts/item2vec/session_tracks_i2v.parquet` | 全量会话记录（所有标签） |

### 算法

```
pop_score = log(1 + track_count)
```

其中 `track_count` = 该 track 在 session_tracks_i2v 中出现的总次数（含所有标签）。

### 输出

| 文件 | 大小 | 内容 |
|------|------|------|
| `artifacts/retriever/popularity/pop_scores.csv` | 21.5 MB | `(track_id, track_count, pop_score)`，按 pop_score 降序 |

### 实际结果

```
唯一 track 数 : 746,592
最高 pop_score: 9.2382（track_id=2536586，播放 10,281 次）
中位 pop_score: 2.3979
构建用时       : ~4s
```

### MLflow

Experiment: `retriever-popularity` — 记录 unique track 数、max/median pop_score。

---

## Retriever 服务类 (`retriever.py`)

### 加载

```python
from src.retriever.retriever import MultiRecallRetriever

r = MultiRecallRetriever(
    artifacts_dir="artifacts/retriever",
    processed_dir="artifacts/item2vec",
)
```

初始化时加载所有 artifact（嵌入矩阵、稀疏矩阵、聚类中心、流行度表）。

### 主接口

```python
candidates = r.retrieve(
    user_id=41504,
    session_track_ids=[1337950, 918120, 232971, ...],
    session_labels=["positive", "positive", "neutral", ...],
)
# 返回: [(track_id, score), ...], 最多 200 条，按 score 降序
```

**Branch 1 评分公式**（最多 100 个）：

$$\text{score}(c) = \sum_{j \in S_t} w_j \cdot (C_{\text{sess}}[r_j, r_c] + C_{\text{pl}}[r_j, r_c])$$

其中 $w_j = e^{-0.3 \cdot (t-j)}$，使用最近 10 条 track，稀疏行查找延迟 < 2ms。

**Branch 2 评分**（最多 80 个）：

按 cluster size 比例分配名额 $k_i = \lfloor 80 \cdot n_k / \sum n_k \rfloor$，对每个聚类中心做 `emb @ centroid`（numpy 矩阵乘法，无 FAISS，74.6 万向量 × 128 维约 1ms）。

**Branch 3**（最多 20 个）：

从 pop_scores.csv 中取不在前两个分支候选集中的 top track。

**候选合并规则**：
- 去重：同一 track 出现在多个分支时保留最高分
- 上限：最多 200 个
- 下限：不足 50 个时从 pop list 补充

```python
# 获取 C3 长期偏好向量 u_long
u_long = r.get_ulong(user_id=41504, session_track_ids=[...])
# 返回: ndarray (128,) 或 None（冷启动用户）
```

---

## 验证步骤

### 1. Split 无交集验证

```python
import numpy as np
train = np.load("artifacts/retriever/split/split_train.npy")
val   = np.load("artifacts/retriever/split/split_val.npy")
test  = np.load("artifacts/retriever/split/split_test.npy")
assert np.intersect1d(train, val).size  == 0
assert np.intersect1d(train, test).size == 0
assert np.intersect1d(val,   test).size == 0
```

### 2. C_sess 非零验证

```python
import scipy.sparse as sp, pandas as pd, numpy as np
C = sp.load_npz("artifacts/retriever/cooc/cooc_session.npz")
assert C.nnz > 1_000_000, "C_sess seems too sparse"

# 验证已知同会话相邻 pair 有非零值
catalog = pd.read_csv("artifacts/item2vec/item2vec_catalog.csv")["track_id"].tolist()
tid2idx = {int(t): i for i, t in enumerate(dict.fromkeys(int(t) for t in catalog))}
# 取任意两条已知训练集中相邻记录进行验证
```

### 3. 用户聚类中心验证

```python
import pickle
uc = pickle.load(open("artifacts/retriever/pref_nn/user_centroids.pkl", "rb"))
print(f"Users: {len(uc)}")
sample_uid = next(iter(uc))
print(f"Sample user {sample_uid}: {len(uc[sample_uid])} centroids")
centroid_vec, cluster_size = uc[sample_uid][0]
print(f"  centroid dim={len(centroid_vec)}, cluster_size={cluster_size}")
assert len(centroid_vec) == 128
```

### 4. Retriever 端到端 Smoke Test

```python
from src.retriever.retriever import MultiRecallRetriever
import numpy as np

r = MultiRecallRetriever()
val_ids = set(np.load("artifacts/retriever/split/split_val.npy").tolist())

# 用任意已知 user_id 和 session 测试
cands = r.retrieve(user_id=41504, session_track_ids=[1337950, 918120], session_labels=["positive", "positive"])
assert 0 < len(cands) <= 200
assert all(isinstance(tid, int) and isinstance(s, float) for tid, s in cands)
assert not any(s != s for _, s in cands)  # no NaN

u_long = r.get_ulong(user_id=41504, session_track_ids=[1337950, 918120])
if u_long is not None:
    assert u_long.shape == (128,)
print("Smoke test PASSED")
```

---

## 完整 Artifact 清单

```
artifacts/retriever/
├── split/
│   ├── split_train.npy       # 1,294,482 session_ids
│   ├── split_val.npy         #   215,747 session_ids
│   └── split_test.npy        #   215,747 session_ids
├── cooc/
│   ├── cooc_session.npz      # CSR (746609×746609), nnz=8,238,824
│   └── cooc_playlist.npz     # CSR (746609×746609), nnz=9,159,402, 31.9 MB
├── pref_nn/
│   └── user_centroids.pkl    # 44,123 users, 115 MB
└── popularity/
    └── pop_scores.csv        # 746,592 tracks, 21.5 MB
```

---

## 注意事项

1. **内存顺序**：cooc 和 pref_nn 峰值内存均约 900 MB，勿并发运行（总内存 2.9 GB 可用）
2. **catalog 去重**：`item2vec_catalog.csv` 含重复 track_id，构建 `tid2idx` 时必须先去重
3. **K 阈值**：当前阈值 (100/300) 基于本数据集实际分布（p20≈107, p60≈306），迁移至新数据集时需重新分析
4. **Retriever 不使用 FAISS**：746K × 128d 的 numpy 矩阵乘法延迟 < 3ms，在 80ms C2 预算内，无需 FAISS
