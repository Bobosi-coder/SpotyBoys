# Item2Vec Embedding Pipeline

## Overview

Replaces the PANNs audio embedding approach with **Item2Vec** — Word2Vec Skip-gram trained on listening session sequences. No external downloads required. Runs entirely on the existing 30Music dataset.

| | PANNs (audio) | Item2Vec (session) |
|--|--|--|
| Completion time | ~10 days | ~1 hour |
| Track coverage | ~30% (~1.35M) | ~65–80% (~3M) |
| Embedding dim | 2048 | 128 |
| Requires downloads | Yes (Deezer MP3s) | No |
| Captures | Audio acoustics | Co-listening patterns |

---

## Input Data

All inputs read directly from `data/raw/content/30music_parsed/`:

| File | Rows | Used by |
|------|------|---------|
| `session_tracks.csv` | 31,351,945 | Stage A (corpus), Stage D |
| `tracks.csv` | 5,675,143 | Stage B (catalog metadata) |
| `session_meta.csv` | 2,764,474 | Stage D |
| `playlist_tracks.csv` | 1,603,040 | Stage D |
| `playlist_meta.csv` | 57,561 | Stage D |
| `love.csv` | 4,106,341 | Stage D |
| `users.csv` | 45,167 | Stage D |

---

## Stages

### Stage A — Build Training Corpus
**Script**: `src/data_pre_process/item2vec/stage_a_corpus.py`

Reads `session_tracks.csv` in 500K-row chunks. Keeps only `label ∈ {positive, neutral}` events (80.9% of rows). Groups by `session_id`, sorts by `position`, and filters sessions shorter than 2 tracks.

**Output**: `data/processed/item2vec_corpus.parquet`
- Schema: `session_id (int64)`, `track_ids (list<int32>)`, `length (int16)`
- Expected: ~2.1M sessions, ~24M tokens

---

### Stage B — Train Item2Vec
**Script**: `src/data_pre_process/item2vec/stage_b_train.py`

Trains a gensim Word2Vec model using a streaming corpus iterator (memory-efficient). Exports the embedding matrix and builds `item2vec_catalog.csv` by joining the trained vocabulary with `tracks.csv`.

**Hyperparameters**:
| Param | Default | Rationale |
|-------|---------|-----------|
| `vector_size` | 128 | Compact; sufficient for recommendation tasks |
| `window` | 10 | Covers session median=6, mean=11 |
| `min_count` | 5 | Prune very rare tracks |
| `sg` | 1 | Skip-gram: better for rare items |
| `negative` | 15 | `ns_exponent=0.75` matches playcount neg_sample_weight |
| `epochs` | 10 | Converges well on 24M-token corpus |
| `workers` | 8 local / 2 VM | Match available CPUs |

**Outputs**:
- `models/item2vec_model.bin` — gensim KeyedVectors
- `data/processed/item2vec_128d.npy` — float32 `(vocab_size, 128)`
- `data/processed/item2vec_track_to_row.json` — `{track_id: row_index}`
- `data/processed/item2vec_catalog.csv` — tracks with valid embeddings

**MLflow**: logs all params, `vocab_size`, `catalog_coverage_pct`, `training_time_sec`, and artifacts.

---

### Stage C — Validate
**Script**: `src/data_pre_process/item2vec/stage_c_validate.py`

Validates embedding quality:
1. **Same-artist cosine similarity** vs random pairs — should be higher
2. **Norm statistics** — checks for NaN/Inf
3. **Top-5 nearest neighbours** for 5 random tracks — logged as artifact

**MLflow**: logs `sanity_passed`, `sanity_same_artist_cosine`, `sanity_random_cosine`, `embed_norm_mean`.

---

### Stage D — Filter Interaction Tables
**Script**: `src/data_pre_process/item2vec/stage_d_filter.py`

Filters all raw interaction tables to tracks present in `item2vec_catalog.csv` (the trained vocabulary). Applies the same cleaning rules as the original Stage 5.

**Outputs** (`data/processed/`):
- `session_tracks_i2v.parquet`
- `session_meta_i2v.parquet`
- `playlist_tracks_i2v.parquet`
- `playlist_meta_i2v.parquet`
- `love_filtered_i2v.parquet`
- `users_filtered_i2v.parquet`

(`_i2v` suffix coexists with any PANNs outputs)

---

## Usage

```bash
# Full pipeline (local)
uv run python -m src.data_pre_process.item2vec.pipeline --stages a,b,c,d

# Individual stages
uv run python -m src.data_pre_process.item2vec.pipeline --stages a
uv run python -m src.data_pre_process.item2vec.pipeline --stages b,c

# Custom hyperparameters
uv run python -m src.data_pre_process.item2vec.pipeline \
  --stages a,b,c,d \
  --vector-size 128 \
  --epochs 10 \
  --workers 8

# VM run (2 CPUs)
MLFLOW_TRACKING_URI=http://<vm-ip>:5000 \
uv run python -m src.data_pre_process.item2vec.pipeline \
  --stages a,b,c,d --workers 2
```

## View MLflow Results

```bash
mlflow ui
# open http://localhost:5000
```

## Dependencies

```bash
uv add gensim mlflow
```

## Logs

- `logs/item2vec_pipeline.log` — pipeline-level log
- `logs/item2vec_neighbors_sample.txt` — nearest neighbour samples (Stage C)
