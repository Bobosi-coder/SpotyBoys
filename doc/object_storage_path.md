## In-Memory Loading at Startup (One-time Initialization)

```
┌────────────────────────────┬──────────────────────────────┬────────┐
│ File                       │ Purpose                      │ Size   │
├────────────────────────────┼──────────────────────────────┼────────┤
│ item2vec_128d.npy          │ Embedding lookup for C2 & C3 │ 365MB  │
├────────────────────────────┼──────────────────────────────┼────────┤
│ item2vec_track_to_row.json │ track_id → matrix row index  │ 13MB   │
├────────────────────────────┼──────────────────────────────┼────────┤
│ user_centroids.pkl         │ u_long lookup for C2 & C3    │ 110MB  │
├────────────────────────────┼──────────────────────────────┼────────┤
│ cooc_session.npz           │ Co-occurrence (C2 branch)    │ 26MB   │
├────────────────────────────┼──────────────────────────────┼────────┤
│ cooc_playlist.npz          │ Co-occurrence (C2 branch)    │ 30MB   │
├────────────────────────────┼──────────────────────────────┼────────┤
│ pop_scores.csv             │ Popularity fallback (C2)     │ 21MB   │
├────────────────────────────┼──────────────────────────────┼────────┤
│ gru_ranker.pt              │ C3 model weights             │ varies │
├────────────────────────────┼──────────────────────────────┼────────┤
│ gru_ranker_config.json     │ C3 model architecture        │ <1KB   │
└────────────────────────────┴──────────────────────────────┴────────┘
```

Total memory footprint is approximately **565MB resident in RAM**.
 No disk reads are required during inference after initialization.

------

## Per-Request Data Flow

### Request Input

```
user_id, session_track_ids, session_labels
```

------

### Redis Fetch

```
session:{user_id} → last 20 (track_id, label)
ulong:{user_id}   → 128-dim vector (cache hit)
                        │
                        │ (cache miss)
                        ▼
                user_centroids.pkl
                + mean(session track embeddings)
                → find nearest centroid → u_long
```

------

## C2 — Multi-Recall Retriever

### 1. Co-occurrence Branch

```
session_track_ids
→ item2vec_track_to_row.json → row indices
→ cooc_session.npz + cooc_playlist.npz → neighbors
→ up to 100 candidates
```

### 2. Preference NN Branch

```
u_long
→ user_centroids.pkl (centroid-based allocation)
→ dot product with item2vec_128d.npy
→ up to 80 candidates
```

### 3. Popularity Fallback Branch

```
pop_scores.csv → top-N items
→ up to 20 candidates
```

------

### Candidate Aggregation

```
Merge + deduplicate → up to 200 candidate track_ids
```

------

### Seen Filtering (Redis)

```
Filter using seen:{user_id}
```

------

## C3 — GRU Ranker Inference

```
Input: 200 candidates

→ item2vec_track_to_row.json → candidate embeddings
→ session prefix embeddings
→ u_long
→ GRU forward pass (gru_ranker.pt)
→ 200 logits
→ ranking
```

------

## Output

```
Top-N (track_id, score)
```

------

## Write-back

### Redis

```
SADD seen:{user_id} ← returned track_ids
```

### PostgreSQL

```
Asynchronously write to interactions table (triggered by play events)
```





# S3 object storage path design

Final S3 Structure

 proj23-mlflow-artifacts/
 │
 ├── mlflow/{experiment_id}/{run_id}/artifacts/   ← MLflow auto-managed
 │
 ├── Retrieve/{YYYYMMDD_HHMMSS}/                  ← retriever intermediate artifacts, every retrain
 │   ├── cooc_session.npz
 │   ├── cooc_playlist.npz
 │   ├── user_centroids.pkl
 │   └── pop_scores.csv
 │
 ├── Real_service/{YYYYMMDD_HHMMSS}/              ← PROMOTED best model only (not every retrain)
 │   ├── gru_ranker.pt
 │   ├── gru_ranker_config.json
 │   ├── cooc_session.npz
 │   ├── cooc_playlist.npz
 │   ├── user_centroids.pkl
 │   ├── pop_scores.csv
 │  
 │
 ├── Item2vec/                                    ← frozen after initial training, never versioned
 │   ├── item2vec_128d.npy
 │   ├── item2vec_track_to_row.json
 │   ├── item2vec_catalog.csv
 │   ├── item2vec_corpus.parquet
 │   ├── playlist_tracks_i2v.parquet
 │   └── playlist_meta_i2v.parquet
 │
 ├── session_event/
 │   ├── snapshot/                                ← 30Music filtered, frozen
 │   │   ├── session_tracks_i2v.parquet
 │   │   ├── session_meta_i2v.parquet
 │   │   ├── love_i2v.parquet
 │   │   └── users_i2v.parquet
 │   └── delta/{YYYYMMDD}/                        ← online batches, date-keyed
 │       ├── session_tracks_addition.parquet
 │       ├── session_meta_addition.parquet
 │       ├── love_addition.parquet
 │       └── users_addition.parquet

 │       └── manifest.json                        ← created by service VM data parser
 └── Raw_data/30music_parsed/                     ← archive only

 

## Retraining Pipeline — VERSION Flow

 VERSION=$(date +%Y%m%d_%H%M%S)
          │
          ▼
 Step 1: Retriever retrain
   Input:  session_event/snapshot/ + session_event/delta/*/
           Item2vec/ (embeddings)
   Output: → Retrieve/{VERSION}/cooc_session.npz
             → Retrieve/{VERSION}/cooc_playlist.npz
             → Retrieve/{VERSION}/user_centroids.pkl
             → Retrieve/{VERSION}/pop_scores.csv
          │
          ▼
 Step 2: Ranker retrain
   Input:  ← Retrieve/{VERSION}/* (downloaded locally)
           session_event data (for train/val splits)
   Output: MLflow logs metrics (loss, NDCG, etc.)
           → mlflow/{exp_id}/{run_id}/artifacts/gru_ranker.pt
          │
          ▼
 Step 3: Evaluate
   Compare new run metrics vs current Real_service version
   (manual review or automated threshold check)
          │
          ▼ (if better)
 Step 4: Promote
   Copy Retrieve/{VERSION}/* → Real_service/{VERSION}/
   Copy gru_ranker.pt + config from MLflow → Real_service/{VERSION}/
   Write manifest.json → Real_service/{VERSION}/manifest.json

 Serving VM only reads from Real_service/ — checks manifest.json to confirm version is complete.


  Phase 1：初始训练（只用 30Music 历史数据）

  目标：用 Ray Tune 找到最优超参数，训练出第一个上线模型。

  前提条件

  - Service VM 上 MLflow (http://129.114.25.207:8000) 已运行
  - S3 里已有 Retrieve/20260417_051148/ 的 retriever artifacts

  ---
  步骤 1：GPU VM 拉代码、构建镜像

  # 在 GPU VM (192.5.87.187) 上执行
  git clone git@github.com:Bobosi-coder/SpotyBoys.git
  cd SpotyBoys
  git checkout feature/gpu-docker-training
  docker-compose build

  得到：Docker 镜像构建完成，包含所有依赖（PyTorch + Ray + MLflow 等）。

  ---
  步骤 2：启动 Phase 1 训练

  docker-compose run --rm training bash scripts/retrain.sh --phase1 --retrieve-version 20260417_051148

  内部自动执行以下三件事：

  2a. 下载数据（scripts/download_data.sh --no-delta）
  - 从 S3 下载 Item2vec/item2vec_128d.npy、item2vec_track_to_row.json
  - 下载 Retrieve/20260417_051148/ 里的 retriever artifacts（cooc、pref_nn、popularity、split）
  - 存到 artifacts/ 目录

  2b. 构建 ranker 训练数据（src.ranker.data.build）
  - 读取 retriever artifacts + 历史 session 数据
  - 生成 artifacts/ranker/ranker_train.parquet 和 ranker_val.parquet
  - 每行包含 1 个 positive + 5 个 negative 候选

  2c. Ray Tune sweep（scripts/tune_phase1.py）
  - 在 108 种超参数组合里随机采样 18 个 trial
  - 每个 trial 最多训练 5 个 epoch，ASHA 在第 1 epoch 后淘汰一半差的 trial
  - 每个 trial 结果自动写入 MLflow 实验 "training before online service"

  预计时间：取决于数据集大小，大约 2-4 小时。

  得到：
  - MLflow 里 18 条训练记录，每条有 NDCG5、HR5、MRR5、val_loss、所有超参数
  - 最优的 gru_ranker.pt checkpoint 保存在 MLflow artifact store（S3）

  ---
  步骤 3：查看结果，选出最优 trial

  打开 http://129.114.25.207:8000，进入实验 "training before online service"：
  - 按 NDCG5 降序排列
  - 也可以看 composite score = 0.5×NDCG5 + 0.3×HR5 + 0.2×MRR5

  ---
  步骤 4：Promote 最优模型（手动）

  docker-compose run --rm training python3 scripts/promote.py \
      --mode manual \
      --retrieve-version 20260417_051148

  得到：
  - S3 Real_service/{VERSION}/ 里写入：
    - gru_ranker.pt（模型权重）
    - gru_ranker_config.json（模型结构）
    - cooc_session.npz、user_centroids.pkl 等 retriever artifacts
    - manifest.json（版本、run_id、所有指标）
  - S3 Real_service/baseline.json（记录当前最优 composite score 和 val_loss，供 Phase 2 对比用）
  - 终端输出 VERSION（如 20260418_153012），记住这个值

  此时模型可以部署上线服务。

  ---
  Phase 2：在线后增量重训练

  目标：当 Service VM 收到新的用户行为数据（delta）时，自动重新训练并验证新模型质量，只有优于 baseline
  才替换。

  触发方式：Airflow DAG 手动触发，或设定 cron 定时触发。

  ---
  触发方式 A：Airflow UI（推荐）

  打开 http://129.114.25.207:8080 → DAG retrain_phase2 → 点击 Trigger DAG

  触发方式 B：命令行

  # 在 Service VM 上
  docker exec airflow-webserver airflow dags trigger retrain_phase2

  ---
  Phase 2 内部执行的 7 个步骤

  触发后，Airflow SSH 到 GPU VM，执行 scripts/retrain.sh --phase2，自动完成：

  Step 1：下载 delta 数据
  - 从 S3 session_event/delta/{YYYYMMDD}/ 下载新增的用户行为数据到 /tmp/delta/

  Step 2：合并数据（scripts/merge_delta.py）
  - 把 delta parquet 和历史 snapshot 合并
  - 更新 artifacts/item2vec/ 里的数据

  Step 3：重建 retriever artifacts
  - 依次跑 split.build → cooc.build → popularity.build → pref_nn.build
  - 用新数据重新计算共现矩阵、用户偏好中心等

  Step 4：上传新 retriever 到 S3
  - 上传到 s3://proj23-mlflow-artifacts/Retrieve/{VERSION}/

  Step 5：构建新的 ranker 训练数据
  - 基于新 retriever 重新生成 train/val parquet

  Step 6：查询最优超参数 + 训练
  - get_best_params.py 从 MLflow 查出 Phase 1 最优 trial 的参数
  - 用这组参数直接跑一次完整训练（不再 sweep）
  - 结果写入 MLflow 实验 "retraining after online service"

  Step 7：Auto promote（有门槛）
  - promote.py --mode auto 计算新模型的 composite score
  - 与 baseline.json 对比：
  composite >= baseline × 0.99   # 不能比原来差超过 1%
  val_loss  <= baseline × 1.05   # loss 不能增大超过 5%
  - 通过：上传到 Real_service/{VERSION}/，服务可以切换到新版本
  - 不通过：脚本 exit 1，Airflow 标记任务失败，不替换现有模型，需要人工介入

  ---
  两阶段对比

  ┌──────────────┬──────────────────────────────────┬───────────────────────────────────┐
  │              │             Phase 1              │              Phase 2              │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ 数据         │ 只有 30Music 历史数据            │ 历史 + 新 delta                   │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ 超参数       │ Ray Tune 搜索 18 个 trial        │ 直接用 Phase 1 最优参数           │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ 触发方式     │ 手动                             │ Airflow 自动                      │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ Promote 条件 │ 无门槛，人工选                   │ composite ≥ baseline×0.99         │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ MLflow 实验  │ "training before online service" │ "retraining after online service" │
  ├──────────────┼──────────────────────────────────┼───────────────────────────────────┤
  │ 耗时         │ 2-4 小时                         │ ~30-60 分钟                       │
  └──────────────┴──────────────────────────────────┴───────────────────────────────────┘