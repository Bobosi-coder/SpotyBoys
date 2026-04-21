下面是你给出的整份文档的**完整、逐段、无省略中文翻译**。我将严格保留原有结构、层级与技术含义，只做中文表达转换，不删减内容。

---

# Component 3: GRU Ranker — Build Plan

组件 3：GRU 排序器 —— 构建方案

---

## Context

背景

C2 多路召回检索器已经完成并验证通过。C3 GRU Ranker 是唯一一个包含可学习参数的组件。它接收来自 C2 的最多 200 个候选项，并在当前 session 上下文与用户长期偏好的条件下，对每个候选项进行打分，从而为 C4 产出排序后的列表。

C3 消费的已有产物如下：

* `artifacts/item2vec/item2vec_128d.npy`
  形状为 `（746609, 128）` 的 `float32` embedding 矩阵
* `artifacts/item2vec/item2vec_track_to_row.json`
  `track_id → 行索引`
* `artifacts/item2vec/session_tracks_i2v.parquet`
  带标签的 session 事件数据
* `artifacts/retriever/split/split_{train,val}.npy`
  `session_id` 数组
* `artifacts/retriever/cooc/cooc_session.npz`
  用于 hard negative sampling 的 `C_sess`
* `artifacts/retriever/pref_nn/user_centroids.pkl`
  用于 `u_long` 的 K-Means 聚类中心
* `artifacts/retriever/popularity/pop_scores.csv`
  用于 `neg_sample_weights`

---

## File Structure

文件结构

```text
src/ranker/
├── __init__.py
├── data/
│   ├── __init__.py
│   └── build.py       # 生成 ranker_{train,val}.parquet + neg_sample_weights.npy
├── model.py           # GRURanker（SessionEncoder + ScoringHead）
├── dataset.py         # RankerDataset（PyTorch，加载 parquet，按需计算 embedding）
├── train.py           # 训练循环 + MLflow + HR/NDCG/MRR 评估
└── ranker.py          # GRURankerInference 服务类
```

输出写入 `artifacts/ranker/`：

```text
artifacts/ranker/
├── ranker_train.parquet      # 约 21M 个上下文 × 6 行 ≈ 4-5 GB
├── ranker_val.parquet        # 约 215K 个上下文 × 6 行 ≈ 约 30 MB
├── neg_sample_weights.npy    # float32 (N,) —— 归一化后的 pop_count^0.75
├── gru_ranker.pt             # 按 NDCG@5 选出的最佳 checkpoint
└── gru_ranker_config.json    # 模型超参数 + 词表大小
```

---

## data/build.py — Training Data Generation

`data/build.py` —— 训练数据生成

### Parquet Schema（每个上下文-候选对一行）

```text
┌───────────────┬───────────────┬─────────────────────────────────────────────────────────┐
│    Column     │     Type      │                       Description                       │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ context_id    │ int64         │ 将属于同一上下文的 6 行（1 个正样本 + 5 个负样本）分组    │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ session_id    │ int64         │ 源 session                                              │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ user_id       │ int64         │ 训练时用于查找 u_long                                   │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ prefix_ids    │ list (len=20) │ 左侧 padding 后的 prefix track_id 列表（pad=0）         │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ prefix_labels │ list (len=20) │ 0=positive, 1=neutral, 2=skip, 3=pad                    │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ prefix_len    │ int16         │ padding 前 prefix 的真实长度                            │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ candidate_id  │ int32         │ 候选 track_id                                           │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ y             │ float32       │ 目标值：1.0（positive）/ 0.5（neutral）/ 0.0（negative） │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ weight        │ float32       │ ω：1.0 / 0.3（neutral）/ 1.0                            │
├───────────────┼───────────────┼─────────────────────────────────────────────────────────┤
│ is_positive   │ bool          │ 对唯一正样本行为 True（在 BPR loss 中使用）             │
└───────────────┴───────────────┴─────────────────────────────────────────────────────────┘
```

标签编码 `prefix_labels`：

* `positive → 0`
* `neutral → 1`
* `skip → 2`
* `unknown → 3`，将其视作 pad
* `pad → 3`

---

### Algorithm

算法

#### 预计算（一次性，在遍历 session 之前完成）

1. 加载 `neg_sample_weights.npy`
   它由 `pop_scores.csv` 计算而来：
   `weight = count^0.75`，然后归一化
2. 从 `item2vec_catalog.csv` 构建 `tid2idx`
   需要先去重，方式与 `cooc/build.py` 一致
3. 加载 `cooc_session.npz`
   即用于 hard negative pool 的 `C_sess`
4. 从 split 文件中加载 train/val session 集合

---

### 对训练数据：遍历 train sessions

* 加载 `session_tracks_i2v.parquet`
* 过滤出 train sessions
* 按 `(session_id, position)` 排序
* 对于每个长度为 $$N \ge 3$$ 的 session：

  * 对于每个位置 $$t = 1 .. N-2$$
    也就是 0-based 下的 $$0 .. N-3$$：

    * Prefix：`tracks[0..t]`，左侧 padding 到固定长度 $$L=20$$
    * Positive：`tracks[t+1]`，连同它的 label 一起作为正样本
      根据标签表得到对应的 $$y$$ 和 $$\omega$$
      如果 label=`unknown`，则跳过
    * Hard negatives（3 个）：
      从 `C_sess.getrow(tid2idx[last_session_track])` 中取 top 邻居，按得分排序，且排除正样本
      如果该 `C_sess` 行为空，或可用候选不足，则退化为随机采样
    * Random negatives（2 个）：
      按 `neg_sample_weights` 的分布进行采样
      排除正样本与 hard negatives
    * 输出 6 行，这 6 行共享同一个 `context_id`

---

### 对验证数据：遍历 val sessions（每个 session 只构造一个上下文）

* 对于每个长度为 $$N \ge 2$$ 的 val session：

  * Prefix = `tracks[0..N-2]`
  * label = `tracks[N-1]`
  * 同样构造：1 个正样本 + 3 个 hard negatives + 2 个 random negatives

---

### 写出方式

使用 `pq.ParquetWriter`，按滚动批次写出，每批 100K 行，模式与 Stage A 相同。

训练集和验证集分别写入不同文件。

---

### CLI

```bash
uv run python -m src.ranker.data.build
uv run python -m src.ranker.data.build --max-train-sessions 50000  # 用于开发调试
```

CLI 参数：

* `--max-train-sessions`，默认全部
* `--max-val-sessions`，默认全部
* `--seed`，默认 42

---

### neg_sample_weights.npy

* 形状：`(N,) float32`，其中 $$N = len(tid2idx)$$
* 第 $$i$$ 个位置的值：
  `count[i]^0.75 / sum(count^0.75)`
* 与 `tid2idx` 的行顺序严格对齐
  去重逻辑与 `cooc/build.py` 保持一致
* 保存到：`artifacts/ranker/neg_sample_weights.npy`

内存占用：

* `C_sess`：26 MB
* `neg_weights`：3 MB
* `session df`：过滤后约 500 MB

整体远低于 2.9 GB。

---

## model.py — GRURanker Architecture

`model.py` —— GRURanker 模型结构

```python
LABEL_VOCAB = {"positive": 0, "neutral": 1, "skip": 2, "pad": 3}
```

### `SessionEncoder(nn.Module)`

* `label_emb`: `Embedding(4, 128)`
* `gru`: `GRU(128, 128, num_layers=2, batch_first=True, dropout=0.1)`
* `forward(item_embs: (B,L,128), labels: (B,L)) -> (B,128)`
  输出最后一个 hidden state

---

### `ScoringHead(nn.Module)`

* 输入维度：387
* 结构：

  * `Linear + ReLU + dropout(0.1) -> 256`
  * `Linear + ReLU + dropout(0.1) -> 64`
  * `Linear -> 1`
* `forward(z: (B,387)) -> (B,1)`

---

### `GRURanker(nn.Module)`

* `session_encoder: SessionEncoder`
* `scoring_head: ScoringHead`

`forward(item_embs, labels, u_long, cand_emb, phi) -> (B,) scores`

输入含义：

* `item_embs: (B, L, 128)`
  prefix 的 embedding
* `labels: (B, L)`
  整数编码后的 prefix labels
* `u_long: (B, 128)`
  长期偏好中心
* `cand_emb: (B, 128)`
  候选项 embedding
* `phi: (B, 3)`
  交叉特征

---

### 交叉特征 $$\phi(t,i)$$

可以在 `forward` 中计算，也可以在 `Dataset` 中计算：

1. `cos(cand_emb, item_embs[:, -1, :])`
   候选歌曲与最近一首歌的相似度

2. `cos(cand_emb, mean(item_embs[:, :prefix_len, :]))`
   候选歌曲与当前 session 平均 embedding 的相似度

3. `cos(cand_emb, u_long)`
   候选歌曲与长期偏好中心的相似度

---

## dataset.py — RankerDataset

`dataset.py` —— RankerDataset

```python
class RankerDataset(Dataset):
    def __init__(self, parquet_path, emb_path, t2r_path, centroids_path):
        # Load parquet into memory (or mmap for large train set)
        # Load emb matrix (365 MB) as float32 numpy array
        # Load t2r dict
        # Load user_centroids dict

    def __len__(self): return n_contexts  # not n_rows

    def __getitem__(self, idx):
        # Fetch 6 rows for context idx
        # Look up prefix embeddings: emb[t2r[pid]] for each pid != pad
        # Compute u_long: nearest centroid to session mean
        # Compute cand_emb and phi for each of 6 candidates
        # Return dict with tensors: item_embs, labels, u_long, cand_embs, phi, ys, weights, is_positive
```

翻译如下：

### 初始化

`__init__(self, parquet_path, emb_path, t2r_path, centroids_path)`

* 将 parquet 加载到内存
  或者对于大训练集使用 mmap
* 以 `float32 numpy array` 的形式加载 embedding 矩阵（365 MB）
* 加载 `t2r` 字典
* 加载 `user_centroids` 字典

### `__len__`

返回的是 `n_contexts`，不是 `n_rows`

### `__getitem__(self, idx)`

* 取出对应上下文 `idx` 的 6 行
* 对每个 `pid != pad` 的 prefix id 执行 embedding 查表：`emb[t2r[pid]]`
* 计算 `u_long`：即当前 session 均值对应的最近 centroid
* 对 6 个 candidate 分别计算 `cand_emb` 和 `phi`
* 返回一个包含如下 tensor 的字典：

  * `item_embs`
  * `labels`
  * `u_long`
  * `cand_embs`
  * `phi`
  * `ys`
  * `weights`
  * `is_positive`

---

### Collate function

对 6 行的 tensor 进行堆叠。

batch size = 512 个上下文，则张量形状为：

* `(512×6, ...)`
* 或 `(512, 6, ...)`

---

### 关于大训练集的读取方式

对于大型训练集：

* 大约 21M 行
* 约等于 `21M / 6 = 3.5M contexts`

建议使用：

* memory-mapped numpy
* 或 chunked pyarrow reads

因为直接全量加载 parquet 会很大。

---

### 实际内存分析

实际上：

* parquet 文件约为 4-5 GB
* 将其加载成 pandas 后，内存会膨胀到 10-15 GB

这对 2.9 GB 的 VM 来说过大。

---

### 解决方案

使用 `pyarrow.dataset` 做 row group streaming，按需读取。

或者使用 `parquet_file.iter_batches()` API。

---

### 最简单的方式

在 `Dataset` 初始化时，仅加载 metadata：

* `context_ids`
* `row offsets`

之后在 `__getitem__` 中做目标 row group 读取。

但是这对随机访问来说速度较慢。

---

### 更好的方案

在构建阶段将 parquet 切成 100 个 shard 文件：

* 每个文件约 200K contexts
* Dataset 按 shard 顺序迭代
* 不做跨 shard 的随机打乱
* 但可以在 shard 内 shuffle

这属于大规模训练中的标准做法。

---

### 另一种情况

如果仅仅在本地训练，并且本地机器拥有超过 16 GB 内存，则可以直接完整加载。

但在 VM 上，本地训练是不可行的，因为规范要求使用 GPU。这个 VM 的用途只是 CPU 预处理。GPU 训练在另一台机器上进行。

---

### 因此，实际决策

对于拥有 16GB 以上内存的开发机：

* 在 `__init__` 中全量加载 parquet 到 pandas DataFrame

同时提供 CLI 参数：

* `--max-train-sessions N`

用于开发时做子采样。

---

## train.py — Training Loop

`train.py` —— 训练循环

### Loss

```python
def compute_loss(scores, ys, weights, is_positive, mask):
    # scores: (B*6,), ys: (B*6,), weights: (B*6,), is_positive: (B*6,)

    # Pointwise BCE (weighted)
    loss_point = F.binary_cross_entropy_with_logits(scores, ys, weight=weights, reduction='mean')

    # Pairwise BPR: for each context, pair positive vs each negative
    # Reshape to (B, 6): scores_ctx, is_pos_ctx
    # pos_score = scores_ctx[is_pos_ctx]  # (B,)
    # neg_scores = scores_ctx[~is_pos_ctx]  # (B, 5)
    # loss_pair = -log sigmoid(pos_score.unsqueeze(1) - neg_scores).mean()

    return loss_point + 0.5 * loss_pair
```

翻译如下：

定义损失函数 `compute_loss(scores, ys, weights, is_positive, mask)`：

* `scores: (B*6,)`
* `ys: (B*6,)`
* `weights: (B*6,)`
* `is_positive: (B*6,)`

#### 第一部分：点式 BCE 损失（带权重）

```python
loss_point = F.binary_cross_entropy_with_logits(scores, ys, weight=weights, reduction='mean')
```

#### 第二部分：成对 BPR 损失

对于每个上下文，将正样本与每个负样本配对。

* 先 reshape 成 `(B, 6)`：

  * `scores_ctx`
  * `is_pos_ctx`
* `pos_score = scores_ctx[is_pos_ctx]`，形状为 `(B,)`
* `neg_scores = scores_ctx[~is_pos_ctx]`，形状为 `(B, 5)`
* 成对损失：

```python
loss_pair = -log sigmoid(pos_score.unsqueeze(1) - neg_scores).mean()
```

最终损失：

```python
return loss_point + 0.5 * loss_pair
```

---

### Training hyperparameters

训练超参数

```text
┌───────────────────────┬───────────────────────────────────────┐
│         Param         │                 Value                 │
├───────────────────────┼───────────────────────────────────────┤
│ Batch size            │ 512 contexts (3072 rows)              │
├───────────────────────┼───────────────────────────────────────┤
│ Optimizer             │ AdamW, lr=1e-4, weight_decay=1e-4     │
├───────────────────────┼───────────────────────────────────────┤
│ LR schedule           │ CosineAnnealingLR + 10% linear warmup │
├───────────────────────┼───────────────────────────────────────┤
│ Epochs                │ 3                                     │
├───────────────────────┼───────────────────────────────────────┤
│ Gradient clip         │ max_norm=1.0                          │
├───────────────────────┼───────────────────────────────────────┤
│ Max sequence length L │ 20                                    │
├───────────────────────┼───────────────────────────────────────┤
│ Device                │ cuda / mps / cpu（自动检测）          │
└───────────────────────┴───────────────────────────────────────┘
```

翻译如下：

* Batch size：512 个上下文，也就是 3072 行
* Optimizer：AdamW，学习率 $$1e-4$$，权重衰减 $$1e-4$$
* 学习率调度：`CosineAnnealingLR + 10% linear warmup`
* 训练轮数：3
* 梯度裁剪：`max_norm=1.0`
* 最大序列长度 $$L=20$$
* 设备：`cuda / mps / cpu`，自动检测

---

### Evaluation（每个 epoch 结束后在 val 上评估）

验证集的指标计算方式如下：

在 val parquet 中，每个上下文有 6 行：

* 对每个上下文，将 6 个候选按预测得分排序
* 正样本的排名记为 $$r$$，从 1 开始计数

定义：

* `HR@5 = 1`，当且仅当 $$r \le 5$$，否则为 0
  由于只有 6 个候选，因此这个值会天然偏高
  它主要用于相对比较
* `NDCG@5 = 1 / log2(r+1)`，当 $$r \le 5$$，否则为 0
* `MRR@5 = 1 / r`，当 $$r \le 5$$，否则为 0

注意：

在 6 候选验证集上的指标只是近似值。它们的绝对数值会高于真实线上服务指标。它们的用途主要是作为 epoch 选择信号，也就是用于不同 epoch 之间的相对比较。

---

### MLflow tracking

MLflow 追踪

实验名：

* `gru-ranker-training`

每个 run 记录：

* Params：

  * 所有超参数
  * `vocab_size`
  * `d_emb`
  * `n_layers`
  * `dropout`
* 每个 epoch 的 Metrics：

  * `train_loss`
  * `val_loss`
  * `HR@5`
  * `NDCG@5`
  * `MRR@5`
* Artifacts：

  * 最优的 `gru_ranker.pt`
  * `gru_ranker_config.json`

---

### Checkpointing

模型保存策略

当 `val NDCG@5` 改善时，将模型保存到：

* `artifacts/ranker/gru_ranker.pt`

同时保存配置文件 `gru_ranker_config.json`，内容包括：

* `d_emb`
* `n_labels`
* `n_layers`
* `dropout`
* `vocab_size`
* `L`

---

### CLI

```bash
uv run python -m src.ranker.train
uv run python -m src.ranker.train --epochs 1 --batch-size 256  # 开发调试
```

参数：

* `--epochs(3)`
* `--batch-size(512)`
* `--lr(1e-4)`
* `--weight-decay(1e-4)`
* `--max-norm(1.0)`
* `--device(auto)`
* `--mlflow-experiment`
* `--run-name`

---

## ranker.py — GRURankerInference

`ranker.py` —— GRURankerInference

```python
class GRURankerInference:
    def __init__(self, artifacts_dir="artifacts/ranker",
                 i2v_dir="artifacts/item2vec",
                 retriever_dir="artifacts/retriever"):
        # Load gru_ranker_config.json
        # Instantiate GRURanker, load state_dict from gru_ranker.pt
        # Load emb, t2r, user_centroids (for get_ulong via MultiRecallRetriever)
        # Set model.eval()

    def score(self, user_id, session_track_ids, session_labels, candidates):
        """
        candidates: list of track_ids from C2 (up to 200)
        Returns: list of (track_id, score) sorted descending
        """
        # Build prefix tensors (left-pad to L=20)
        # Compute u_long from user_centroids (nearest centroid to session mean)
        # For each candidate: look up cand_emb, compute phi
        # Batch score all candidates in one forward pass
        # Return sorted list
```

翻译如下：

### `__init__`

初始化时：

* 加载 `gru_ranker_config.json`
* 实例化 `GRURanker`
* 从 `gru_ranker.pt` 加载 `state_dict`
* 加载：

  * embedding
  * `t2r`
  * `user_centroids`
  * 其中 `user_centroids` 用于通过 `MultiRecallRetriever` 计算 `get_ulong`
* 将模型设置为 `eval()` 模式

### `score(self, user_id, session_track_ids, session_labels, candidates)`

输入：

* `candidates`：来自 C2 的 track_id 列表，最多 200 个

输出：

* 按得分降序排列的 `(track_id, score)` 列表

处理过程：

* 构建 prefix tensors，左侧 padding 到 $$L=20$$
* 从 `user_centroids` 计算 `u_long`
  即取当前 session 均值最近的 centroid
* 对每个候选项：

  * 查找 `cand_emb`
  * 计算 `phi`
* 在一次 forward pass 中对所有 candidate 批量打分
* 返回排好序的列表

---

## VM Memory Budget

VM 内存预算

```text
┌──────────────────────────┬─────────────────────────────────────────────┐
│          Stage           │                  Peak RAM                   │
├──────────────────────────┼─────────────────────────────────────────────┤
│ data/build.py            │ ~700 MB (session df + C_sess + neg weights) │
├──────────────────────────┼─────────────────────────────────────────────┤
│ train.py (CPU, dev only) │ emb(365MB) + model(~5MB) + batch tensors    │
└──────────────────────────┴─────────────────────────────────────────────┘
```

翻译如下：

* `data/build.py`
  峰值内存约 700 MB
  包括：

  * session dataframe
  * `C_sess`
  * `neg weights`

* `train.py`（仅 CPU 开发测试）
  包括：

  * embedding（365MB）
  * model（约 5MB）
  * batch tensors

GPU 训练时：

* embedding 保持在 CPU 上
* batch 被移动到 GPU

因此不会受到 VM 内存限制的影响。

---

## Verification

验证

1. 数据构建验证：检查 `ranker_train.parquet`
   确认：

   * 每个 `context_id` 恰好有 6 行
   * 没有上下文中出现 positive 与某个 negative 相同
   * label 分布与先前分析一致
     即大约为：

     * positive：73%
     * neutral：7%
     * skip：19%

2. 模型前向传播验证：
   实例化模型，输入 dummy tensors，确认输出形状为 `(B,)`

3. 训练冒烟测试：
   使用 `--max-train-sessions 1000 --epochs 1`
   验证：

   * loss 会下降
   * 不会出现 NaN

4. 推理冒烟测试：
   执行
   `ranker.score(user_id, session_tracks, session_labels, candidates)`
   验证返回结果为 200 个排序后的 `(track_id, score)` 元组

---

## Critical Files

关键文件

### 输入（已有）

* `artifacts/item2vec/session_tracks_i2v.parquet`
* `artifacts/item2vec/item2vec_128d.npy`
* `artifacts/item2vec/item2vec_track_to_row.json`
* `artifacts/item2vec/item2vec_catalog.csv`
* `artifacts/retriever/split/split_train.npy`
* `artifacts/retriever/split/split_val.npy`
* `artifacts/retriever/cooc/cooc_session.npz`
* `artifacts/retriever/pref_nn/user_centroids.pkl`
* `artifacts/retriever/popularity/pop_scores.csv`

参考文档：

* `doc/model_structure_training.tex` 第 1176–1435 行
  即 C3 的完整规范

可复用的模式：

* `ParquetWriter streaming`：`src/item2vec/stage_a_corpus.py:96`
* `numpy diff boundary scan`：`src/item2vec/stage_a_corpus.py:85`
* `tid2idx dedup`：`src/retriever/cooc/build.py:225-233`
* `u_long computation`：`src/retriever/retriever.py:_pref_nn_branch` 和 `get_ulong`

---

# 附：整份文档的直译式总结

这份文档描述的是 C3 GRU Ranker 的完整构建方案。它包括四大部分：

1. **数据构建**
   从 session 数据中构造训练样本，每个上下文配 1 个正样本和 5 个负样本，共 6 行。

2. **模型结构**
   使用：

   * SessionEncoder，基于 GRU 编码 prefix
   * ScoringHead，将 session 表示、候选 embedding、长期偏好和交叉特征融合后打分

3. **训练与评估**
   使用：

   * 点式 BCE 损失
   * 成对 BPR 损失
   * 在验证集上以 HR@5、NDCG@5、MRR@5 进行比较
   * 以 `NDCG@5` 作为 checkpoint 选择标准

4. **推理服务**
   接收 C2 输出的最多 200 个候选项，批量打分并排序，输出给 C4。

