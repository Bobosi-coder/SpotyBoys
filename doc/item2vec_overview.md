# Item2Vec 嵌入流水线方案

## 背景

基于 **PANNs 音频嵌入方法（Stage 4）** 需要从 Deezer 下载约 **450 万条 MP3 预览音频**，预计持续运行约 **10 天**，且命中率仅约 **30%**。该方案在工程上不可行。

因此我们改为使用 **Item2Vec 方法**：

将 session 中的 track_id 视为“词”，将一个 session 视为“句子”，在这些序列上训练 **Word2Vec（Skip-gram）模型**。

该方法具有以下优势：

* 不依赖任何外部下载
* 完全基于已有数据运行
* 可为所有出现在 session 中的歌曲生成 **128 维稠密向量**
* 覆盖约 **330 万首歌曲（相比 PANNs 最多约 100 万）**

---

## 分支

基于当前分支：

```bash
data-process/filtering-session-data
```

创建新分支：

```bash
git checkout -b feature/item2vec-embedding
```

---

## 数据基础（来自 data_inspection_report.txt）

| 信号                     | 数值         |
| ---------------------- | ---------- |
| session 总数             | 2,764,474  |
| 可用 session（长度 ≥ 2）     | 2,249,528  |
| 相邻正/中性对                | 21,908,813 |
| session 中唯一 track_id 数 | 3,324,298  |
| session 中位长度           | 6          |
| session 平均长度           | 11.3       |
| 活跃用户（≥50 次行为）          | 90.9%      |

过滤后的训练语料：

* 约 **2600 万条事件**
* 分布在约 **225 万个 session 中**
* 对共现学习来说是极其丰富的信号

---

## 核心设计决策

### 1. 语料中使用哪些 label

保留：

* positive（73.9%）
* neutral（7.0%）

这两类代表完整或部分收听

剔除：

* skip（4.0%）：负反馈，会污染共现关系
* unknown（15.1%）：playratio 缺失，语义不明确

---

### 2. 语料构建方式

* 一个 session → 一个 track_id 序列（按播放顺序）
* 过滤后长度 < 2 的 session 被丢弃
* 不进行跨 session 拼接（session 本身是语义单位）

---

### 3. 模型超参数（基于数据特性）

* vector_size = 128
  → 紧凑，便于下游模型（相比 PANNs 的 2048 维）

* window = 10
  → 覆盖 median=6 与 mean≈11 的范围

* min_count = 5
  → 去掉出现次数 <5 的歌曲
  → 词表规模约 40万–60万

* sg = 1
  → 使用 Skip-gram（对稀疏 item 更优）

* negative = 15
  → 负采样数
  → 分布 ∝ freq^0.75（对应 Stage 3 中的 neg_sample_weight）

* epochs = 10

* workers = 8

* seed = 42（保证可复现）

---

### 4. 替代 Stage 5 的逻辑

当前 Stage 5：

* 基于 audio_catalog.csv（PANNs）

在本方案中：

* 改为使用 item2vec_catalog.csv
* 即：所有成功训练出 embedding 的 track（满足 min_count）

---

## 新文件结构

```text
src/data_pre_process/item2vec/
├── __init__.py
├── pipeline_logging.py
├── pipeline.py
├── stage_a_corpus.py
├── stage_b_train.py
├── stage_c_validate.py
└── stage_d_filter.py
```

新增文档：

```text
item2vec_pipeline.md
```

---

## 各阶段说明

---

### Stage A：构建训练语料

**输入：**

```text
session_tracks.csv（约 3100 万行）
```

**输出：**

```text
item2vec_corpus.parquet
```

**处理流程：**

1. 分块读取（每块 50 万行）
2. 保留 label ∈ {positive, neutral}
3. 删除 track_id 为空的数据
4. 类型转换为 int
5. 按 (session_id, position) 排序
6. group by session → 得到 track_id 列表
7. 删除长度 <2 的 session
8. 保存为 parquet：

```text
columns = [session_id, track_ids(list[int]), length]
```

**日志输出：**

* 原始行数
* 过滤后行数
* session 数量变化
* token 总数
* vocabulary 大小

**预计结果：**

* ~210 万 session
* ~2400 万 token

---

### Stage B：训练 Item2Vec

**输入：**

```text
item2vec_corpus.parquet
```

**输出：**

* item2vec_model.bin（KeyedVectors）
* item2vec_128d.npy（embedding矩阵）
* item2vec_track_to_row.json
* item2vec_catalog.csv

**处理流程：**

1. 加载语料
2. 构建 iterator（输出 List[str]）
3. 训练 Word2Vec
4. 提取 embedding
5. 构建 track_id → row_index 映射
6. 导出 numpy 矩阵
7. 与 catalog.csv join 得到 metadata

**日志输出：**

* 语料规模
* 词表大小
* 每 epoch 训练时间
* 覆盖率

**预计耗时：**

* CPU：30–60 分钟

---

### Stage C：验证

输入：

* model.bin
* item2vec_catalog.csv

**检查项：**

1. 同艺术家相似度 > 随机对
2. genre tag 一致性
3. embedding 数值分布（均值、方差、NaN）
4. 随机样本相似歌曲

输出：

```text
logs/item2vec_validate.log
```

---

### Stage D：过滤交互数据

输入：

* 原始 interaction CSV
* item2vec_catalog.csv

输出：

```text
session_tracks_i2v.parquet
session_meta_i2v.parquet
playlist_tracks_i2v.parquet
playlist_meta_i2v.parquet
love_filtered_i2v.parquet
users_filtered_i2v.parquet
```

特点：

* 逻辑与原 stage5_filter 相同
* 使用 item2vec_catalog 替代 audio_catalog
* 输出带 _i2v 后缀
* 覆盖率显著提升（约 80–90%）

---

## Pipeline 入口

```bash
uv run python -m src.data_pre_process.item2vec.pipeline --stages a,b,c,d
```

支持：

```bash
--stages a
--stages b
```

参数：

* vector-size
* window
* min-count
* epochs
* workers

---

## 依赖

已有：

* numpy
* pandas
* pyarrow

新增：

```bash
uv add gensim
```

---

## 与 PANNs 对比

| 指标   | PANNs | Item2Vec |
| ---- | ----- | -------- |
| 时间   | ~10 天 | ~1 小时    |
| 覆盖率  | ~30%  | ~80–90%  |
| 维度   | 2048  | 128      |
| 信息来源 | 音频特征  | 用户行为共现   |
| 冷启动  | 不支持   | 不支持      |
| 可解释性 | 低     | 中        |

---

## Markdown 规范文件

路径：

```text
item2vec_pipeline.md
```

内容：

* 设计目的
* 数据流图
* 超参数解释
* 各阶段 I/O
* 验证指标
* 使用示例

---

## 执行步骤

```bash
1. git checkout -b feature/item2vec-embedding
2. 创建目录与文件
3. 创建 markdown 文档
4. 运行 pipeline
5. 检查验证结果
```

---

## 复用已有代码模式

* chunk 读取：stage2_playcount.py
* 相似度验证：stage4_embed.py
* logging：pipeline_logging.py
* parquet 输出：stage5_filter.py
* neg_sample_weight：来自 stage3，与 Word2Vec 对齐




# Item2Vec 嵌入流水线 —— 修订版方案

---

## 背景

从 **PANNs 音频嵌入方案**（预计约 10 天，覆盖率约 30%）切换到 **Item2Vec（基于 session 行为）**。

该流水线必须满足：

1. **直接读取原始数据**

   ```text
   data/raw/content/30music_parsed/
   ```

   不依赖旧 pipeline

2. **集成 MLflow**

   * 本地运行
   * 再迁移到 VM（Chameleon）

3. **适配 VM 硬件约束**

---

## 分支

```bash
git checkout -b feature/item2vec-embedding
```

要求：

* 全新代码
* 不从 download_embedding/ 导入任何内容

---

## VM 硬件约束（vm_hardware.txt）

| 资源       | 配置                        | 对设计的影响           |
| -------- | ------------------------- | ---------------- |
| CPU      | 2 vCPU                    | VM 上 workers=2   |
| RAM      | 3.8GB，总可用约 2.9GB          | 可运行完整 pipeline   |
| Swap     | 无                         | OOM 会直接崩溃        |
| 磁盘       | 17GB 可用                   | parquet 和模型文件可容纳 |
| MLflow存储 | /mnt/mlflow_persist 4.6GB | artifact 存储位置    |

---

## 内存预算（VM 可用约 2.9GB）

* Word2Vec 权重：
  $$500K \times 128 \times 2 \times 4 \approx 512MB$$

* 语料 parquet：
  ~200MB

* Python + pandas：
  ~200MB

* 总峰值：
  ~1GB

结论：

* 完全在 2.9GB 内
* vector_size=128 和 min_count=5 可以安全使用

---

## 输入数据（仅 raw）

| 文件                 | 行数         | 用途       |
| ------------------ | ---------- | -------- |
| session_tracks.csv | 31,351,945 | 训练语料     |
| tracks.csv         | 5,675,143  | metadata |

说明：

* 不依赖 data/processed

---

## 核心设计决策

---

### 1. 训练语料的 label 过滤

保留：

* positive（73.9%）
* neutral（7.0%）

剔除：

* skip（负信号）
* unknown（无意义）

---

### 2. 大规模 CSV 内存策略

针对 3100 万行数据：

* 分块读取（每次 50 万行）
* 用 dict 累积：

```python
{session_id: [track_ids]}
```

* 所有 chunk 完成后：

  * 过滤长度 <2 的 session
  * 写 parquet（约 200MB）

训练时：

* 使用 generator 从 parquet 流式读取
* 不一次性加载全部数据

---

### 3. 超参数

| 参数          | 默认值       | 解释               |
| ----------- | --------- | ---------------- |
| vector_size | 128       | 比 PANNs 2048 更紧凑 |
| window      | 10        | 覆盖 session 长度分布  |
| min_count   | 5         | 去掉极低频歌曲          |
| sg          | 1         | Skip-gram，适合稀疏数据 |
| negative    | 15        | 负采样              |
| epochs      | 10        | 足够训练             |
| workers     | 本地8 / VM2 | 与 CPU 匹配         |

说明：

* 所有参数支持 CLI
* VM 只需调 workers

---

### 4. MLflow 实验追踪

* 本地：

  ```bash
  MLFLOW_TRACKING_URI=./mlruns
  ```

* VM：

  ```bash
  MLFLOW_TRACKING_URI=http://<vm-ip>:5000
  ```

记录：

* Stage B（训练）
* Stage C（验证）

---

## 文件结构

```text
src/data_pre_process/item2vec/
├── __init__.py
├── pipeline.py
├── stage_a_corpus.py
├── stage_b_train.py
├── stage_c_validate.py
└── stage_d_filter.py
```

项目根目录：

```text
item2vec_pipeline.md
```

说明：

* 使用 Python 标准 logging
* 不使用 pipeline_logging.py

---

## Stage A —— 构建训练语料

输入：

```text
session_tracks.csv
```

输出：

```text
item2vec_corpus.parquet
```

结构：

```text
session_id (int64)
track_ids (list<int32>)
length (int16)
```

---

### 算法流程

```python
sessions = defaultdict(list)

for 每个 chunk:
    保留 label ∈ {positive, neutral}
    删除空 track_id
    转换类型
    sessions[session_id].append((position, track_id))

# 所有 chunk 后：
排序 → 提取 track_ids
过滤 len < 2
写 parquet
```

---

### 记录指标（供 Stage B 使用）

* 原始行数
* 过滤后行数
* session 数量变化
* token 总数
* unique track 数

---

### 预计输出

* ~210 万 session
* ~2400 万 token
* ~200MB

---

## Stage B —— 训练 Item2Vec

输入：

```text
item2vec_corpus.parquet
```

输出：

* model.bin
* embedding.npy
* mapping.json
* catalog.csv

---

### MLflow 记录

参数：

```python
vector_size
window
min_count
negative
epochs
workers
sg
corpus_sessions
corpus_tokens
```

指标：

```python
vocab_size
coverage
training_time
```

artifact：

* model
* embedding
* catalog

---

### 内存安全设计

* 使用 generator
* 每次只加载一个 session
* gensim 不加载全部数据

---

### 运行时间

* 本地：30–60 分钟
* VM：90–120 分钟

---

## Stage C —— 验证

输入：

* model.bin
* tracks.csv

---

### 验证内容

1. 同 artist 相似度 > 随机
2. embedding 分布检查
3. 输出邻居样本

---

### MLflow 记录

```python
same_artist_cosine
random_cosine
sanity_passed
```

artifact：

```text
neighbors_sample.txt
```

---

## Stage D —— 过滤交互数据

输入：

* 所有 raw 表
* item2vec_catalog.csv

输出：

```text
session_tracks_i2v.parquet
session_meta_i2v.parquet
playlist_tracks_i2v.parquet
playlist_meta_i2v.parquet
love_filtered_i2v.parquet
users_filtered_i2v.parquet
```

---

### 过滤规则

#### session_tracks

* 去重 (session_id, position)
* track ∈ vocab
* 去掉 unknown
* playratio 截断
* session 长度 ≥ 2

#### playlist_tracks

* 去重
* track ∈ vocab
* 长度 ≥ 2

#### meta 表

* 只保留 surviving id

#### love

* 去重
* track ∈ vocab

#### users

* 去重

---

### MLflow

记录：

* 每一步过滤前后行数

---

## Pipeline 入口

本地运行：

```bash
uv run python -m src.data_pre_process.item2vec.pipeline --stages a,b,c,d
```

---

VM 运行：

```bash
MLFLOW_TRACKING_URI=http://<vm-ip>:5000 \
uv run python -m src.data_pre_process.item2vec.pipeline \
  --stages a,b,c,d \
  --vector-size 64 \
  --min-count 10 \
  --epochs 5 \
  --workers 2
```

---

## CLI 参数

* stages
* vector_size
* window
* min_count
* negative
* epochs
* workers
* mlflow_experiment
* run_name

---

## 依赖

```bash
uv add gensim mlflow
```

---

## 与 PANNs 对比

| 指标   | PANNs | Item2Vec |
| ---- | ----- | -------- |
| 时间   | ~10 天 | ~1 小时    |
| 覆盖率  | ~30%  | ~65–80%  |
| 维度   | 2048  | 128      |
| 是否下载 | 是     | 否        |
| 冷启动  | 否     | 否        |

---

## 执行流程

1. 创建分支
2. 安装依赖
3. 编写代码
4. 写 markdown 文档
5. 本地运行
6. 打开 MLflow UI
7. 部署到 VM 运行

---

## 总结一句话

这个修订版 pipeline 的核心思想是：

> 用用户行为数据替代音频数据，通过流式训练和内存控制，在低资源 VM 上高效构建高覆盖率 embedding 


