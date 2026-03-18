# SpotyBoys MLOps Pipeline (Local)

This project runs directly on your local Python environment with `uv`. Docker is not required.

## 0. Install uv (if needed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 1. Setup with uv

```bash
uv sync
```

## 2. Run Full Pipeline

```bash
uv run python main.py --limit 100
```

This runs:
1. `src/download_previews.py`
2. `src/pipeline_stage1_catalog.py`
3. `src/pipeline_stage2_training.py`

## 3. Optional: Rebuild Universe First

If you want to regenerate `universe_metadata.csv` and `filtered_sessions.csv` before stage 1/2:

```bash
uv run python main.py --with-universe --limit 100
```

## 4. Audio Output Directory

By default previews are written to `./data/raw/audio_previews`.

You can override with an environment variable:

```bash
export AUDIO_PREVIEWS_DIR=/Volumes/T7/MLOps_music_track
uv run python main.py --limit 100
```

## 5. 30Music Parsing Scripts

`src/data_parse` is kept intact. If you need to parse raw 30Music idomaar files:

```bash
uv run python src/data_parse/parse_30music.py
```
