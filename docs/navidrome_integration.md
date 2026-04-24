# SpotyBoys — Navidrome Integration

This document covers how the SpotyBoys recommendation system is integrated into Navidrome, how the full user workflow operates end-to-end, how authentication and 30Music user ID mapping works, and what every database table stores.

---

## 1. Overall Workflow

```
User opens browser → http://<VM>:8089/
  │
  ▼
nginx:8089
  │
  ├── /               → navidrome:4533   (our patched Navidrome build)
  ├── /auth/          → recommendation-api:8001
  ├── /session/       → recommendation-api:8001
  ├── /recommendations/ → recommendation-api:8001
  ├── /stream/        → recommendation-api:8001
  ├── /covers/        → recommendation-api:8001
  └── /events/        → event-api:8002
```

### Step-by-step user journey

1. **Login to Navidrome** — user visits `/` and logs in with their Navidrome credentials. Navidrome stores the username in `localStorage`.

2. **Navigate to Recommendations** — user clicks "Recommendations" in the left sidebar (added by our `Menu.jsx` patch).

3. **Auto-authentication to recommendation-api** — `useSpotiboysSession.js` reads the Navidrome username from `localStorage`, then:
   - Calls `POST /auth/signup` with `email={username}@navidrome.local`
   - If 409 (already exists), calls `POST /auth/login` instead
   - Password is derived deterministically: `SHA-256("spotiboys:{username}")`
   - The server sets a `spotiboys_session` httpOnly cookie

4. **Bootstrap** — `GET /session/bootstrap` returns the first batch of recommendations. The response includes:
   - `browse_surface.featured_items` (up to 4 tracks)
   - `browse_surface.random_carousel_items` (up to 10 tracks)
   - `model_version`, `fallback_level`, `impression_id`, `request_id`

5. **Play a track** — user clicks a track on the Recommendations page:
   - The page calls Navidrome's Redux `PLAYER_PLAY_TRACKS` action with all recommendation tracks as the queue
   - Each track carries a custom `musicSrc` (`/stream/{track_id}`) and `_spotiboys` metadata
   - Our `playerReducer.js` patch allows `musicSrc` and `cover` overrides instead of always using the Subsonic URL
   - Navidrome's built-in audio player takes over

6. **Event capture** — `SpotiboysEventBridge.jsx` is always mounted in the app layout. It watches `state.player.current`:
   - When `current.uuid` changes and the track has `_spotiboys` metadata → `POST /events/playback` type=`playback_start`
   - When the player advances to the next track before natural end → type=`skip`
   - When audio ends naturally (`current.ended = true`) → type=`complete`
   - Tracks without `_spotiboys` (normal library browsing) are silently ignored

7. **Refresh recommendations** — user clicks the refresh button, or the page calls `POST /recommendations/next` with the current `session_id`, `user_id`, and `queue_revision`.

---

## 2. Navidrome Integration Details

### What was patched (4 files)

| File | Change |
|------|--------|
| `ui/src/routes.jsx` | Added `/recommendations` route pointing to `RecommendationsPage` |
| `ui/src/App.jsx` | Added `import SpotiboysEventBridge` + mounted `<SpotiboysEventBridge />` alongside `<Player />` |
| `ui/src/layout/Menu.jsx` | Added "Recommendations" `MenuItemLink` with `Queue` icon at bottom of sidebar |
| `ui/src/reducers/playerReducer.js` | 2-line patch: `musicSrc: item.musicSrc \|\| subsonic.streamUrl(trackId)` and `cover: item.cover \|\| subsonic.getCoverArtUrl(...)` |

### What was added (3 new files)

| File | Purpose |
|------|---------|
| `ui/src/recommendations/RecommendationsPage.jsx` | Main recommendations UI: featured grid + discover list, play button dispatches `PLAYER_PLAY_TRACKS` |
| `ui/src/recommendations/useSpotiboysSession.js` | React hook: handles auto signup/login, bootstrap fetch, recommendation refresh |
| `ui/src/eventbridge/SpotiboysEventBridge.jsx` | Side-effect-only component mounted in `App.jsx`: watches Redux player state, emits events to event-api |

### Build process

The `navidrome-patches/Dockerfile` performs a multi-stage build:

```
Stage 1 (node:20-alpine):
  git clone navidrome/navidrome @ v0.54.5
  COPY our new files into ui/src/
  patch -p1 the 4 modified files
  npm ci && npm run build  → ui/build/

Stage 2 (golang:1.23-alpine):
  go build → /navidrome binary (embeds ui/build/)

Stage 3 (alpine:3.20):
  ffmpeg + the binary → final image
```

The Go binary embeds the compiled UI using Go's `embed` package, so only one container serves everything.

---

## 3. Authentication and 30Music User ID Mapping

### Online users (new signups)

```
Navidrome username: "alice"
  → email = "alice@navidrome.local"
  → password = SHA-256("spotiboys:alice")  [deterministic, never stored client-side]
  → POST /auth/signup creates app.users row:
      user_id = "user_{uuid}"   (e.g. "user_1dd3a9b2...")
      user_int_id = 100001      (auto-sequence starting at 100,000)
  → Recommendations: cold start (popularity fallback, no centroid)
```

`user_int_id` starts at 100,000, safely above the 30Music snapshot maximum of 45,175. This ensures online users never collide with training users when events are exported for retraining.

### 30Music test users (prefNN personalisation)

The preference NN branch uses `user_centroids.pkl`, keyed by 30Music integer user IDs (1–45,175). The recommendation pipeline calls `_safe_int(user_id_string)` which extracts digits from the user_id string.

**To make prefNN fire for a test user:**

1. Create a Navidrome account with username `40305`
2. `useSpotiboysSession` derives `user_id = "user_40305"`
3. `_safe_int("user_40305") = 40305` → hits the centroid for 30Music user 40305
4. prefNN branch returns 80 personalised candidates instead of falling back to popularity

Pre-inserted test user (already in README):
```sql
INSERT INTO app.users (user_id, email, password_hash, display_name)
VALUES (
  'user_40305',
  '40305@navidrome.local',
  'pbkdf2_sha256$210000$...$...',   -- password: test123 (use hash_password() to regenerate)
  '30Music Power User (15126 plays)'
);
```

The `password_hash` must match `SHA-256("spotiboys:40305")` when hashed with `hash_password()` from `packages/auth.py`. Alternatively, use the pre-generated hash with password `test123` from the README.

### ID mapping summary

| User type | Navidrome username | email | user_id | user_int_id | prefNN |
|-----------|-------------------|-------|---------|-------------|--------|
| New online user | `alice` | `alice@navidrome.local` | `user_{uuid}` | ≥100,000 | ✗ cold start |
| 30Music test user | `40305` | `40305@navidrome.local` | `user_40305` | 40305 (pre-set) | ✓ personalised |

---

## 4. Database Schemas

The PostgreSQL database (`spotiboys`) has four schemas: `raw`, `processed`, `ml`, and `app`.

---

### `raw` schema — 30Music training data (read-only after import)

These tables are populated once from the 30Music dataset during training data preparation. The service itself never writes to `raw`.

| Table | What it stores |
|-------|---------------|
| `raw.tracks` | All 30Music tracks: id, name, title, artist_hint, duration_ms, playcount |
| `raw.artists` | Artist records: id, mbid, name |
| `raw.albums` | Album records: id, mbid, title |
| `raw.tags` | Tag vocabulary: id, value |
| `raw.users` | 30Music users: id, username, gender, age, country, playcount |
| `raw.sessions` | Listening sessions: id, user_id, timestamp, track count |
| `raw.session_tracks` | Per-session track sequence: session_id, position, track_id, play ratio, action label |
| `raw.events` | Individual play events: id, track_id, user_id, event_ts, event_type |
| `raw.track_loves` | User ♥ track relationships |
| `raw.playlists` | Playlist records |
| `raw.playlist_tracks` | Per-playlist track sequence |
| `raw.track_artists` | Track ↔ artist many-to-many |
| `raw.track_albums` | Track ↔ album many-to-many |
| `raw.track_tags` | Track ↔ tag many-to-many |

---

### `processed` schema — pipeline artifacts registry

| Table | What it stores |
|-------|---------------|
| `processed.dataset_artifacts` | Registry of exported datasets: name, version, S3 bucket + key, is_active |
| `processed.data_splits` | Train/val/test split metadata: version, split_name, S3 path, session count |

---

### `ml` schema — model artifact registry

| Table | What it stores |
|-------|---------------|
| `ml.track_embeddings` | Item2Vec embedding file locations: track_id, model version, S3 path, embedding dim, row_index |
| `ml.model_artifacts` | All model artifacts: name, version, type, MLflow run_id, S3 path, metadata JSON |
| `ml.dataset_versions` | Versioned training datasets: name, version, S3 path, description |

---

### `app` schema — live service (written continuously by the running system)

#### User & Auth

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.users` | recommendation-api `/auth/signup` | user_id (TEXT), email, password_hash (PBKDF2-SHA256), display_name, user_int_id (BIGINT, auto-seq from 100,000) |
| `app.sessions` | recommendation-api session creation | session_id, user_id, auth_state, session_int_id (auto-seq from 3,000,000) |
| `app.auth_sessions` | recommendation-api login | session_token_hash (SHA-256 of cookie), session_id, user_id, expires_at, revoked_at |

#### Music Catalog

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.playable_tracks` | catalog-sync-worker | track_id, title, artist, album, duration_sec, cover_art_url, is_playable, availability_status |
| `app.navidrome_track_mapping` | catalog-sync-worker | track_id → navidrome_track_id mapping, mapping_confidence, quarantine_reason |

#### Recommendation Events

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.recommendation_impressions` | recommendation-api `/session/bootstrap`, `/recommendations/next` | Full recommendation response snapshot: impression_id, model_version, fallback_level, browse_surface JSON, queue JSON |
| `app.rendered_impressions` | event-api `POST /events/impression` | What the user actually saw: surface, visible_items JSON, rendered_at timestamp |
| `app.playback_events` | event-api `POST /events/playback` | playback_start / skip / complete events: track_id, position_ms, playback_ms, client_event_seq |
| `app.feedback_events` | event-api `POST /events/feedback` | Explicit feedback (like/dislike): feedback_type, track_id |
| `app.recommendation_outcomes` | outcome-deriver-worker | Derived training labels per recommendation: derived_label (positive/skip/negative) |

#### Model Lifecycle

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.model_versions` | artifact-fetch-worker / artifact-refresh-worker | version, serving_bundle_version, S3 manifest URI, activated_at, is_active, status, rollback_parent_version |
| `app.retraining_runs` | delta-trigger-worker | Retraining job records: snapshot version, delta version, MLflow run_id, status, metrics JSON |

#### Serving Monitoring

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.serving_request_metrics` | recommendation-api (per-request) | Per-request latency, endpoint, status, candidate_count, final_count, fallback_level, error_type |
| `app.serving_metric_rollups` | serving-monitor-worker (every 5 min) | Aggregated windows (5m, 1h): error_rate, fallback_rate, p50/p95 latency, skip_rate, completion_rate, diversity metrics |
| `app.model_trigger_decisions` | rollback-check-worker | Rollback/promotion decisions: decision_type, model_version, reason, metrics snapshot |

#### Delta Export & Data Quality

| Table | Written by | What it stores |
|-------|-----------|---------------|
| `app.delta_export_metadata` | parser-export-worker | Checkpoint per export run: delta_version, status, last_exported_session_int_id, row_counts JSON |
| `app.ingestion_rejections` | event-api (validation gate) | Events rejected at ingestion: event_type, rejection reasons[], raw_payload JSON |

---

## 5. Data Flow for Retraining

```
Live service writes:
  app.playback_events
  app.rendered_impressions
  app.recommendation_impressions
       │
       ▼
outcome-deriver-worker
  → app.recommendation_outcomes   (derived_label: positive/skip/negative)
       │
       ▼
parser-export-worker
  → exports delta parquet files to S3:
      session_tracks_addition.parquet
      session_meta_addition.parquet
      love_addition.parquet
      users_addition.parquet
  → updates app.delta_export_metadata
       │
       ▼
delta-trigger-worker
  → checks if enough new data exists
  → triggers Airflow DAG retrain_phase2 via REST API
       │
       ▼
GPU VM (Airflow SSHOperator)
  → bash scripts/retrain.sh --phase2
  → rebuilds retriever + trains GRU ranker
  → promote.py --mode auto (gate: composite ≥ 99% of baseline)
  → uploads Real_service/{VERSION}/ to S3
       │
       ▼
artifact-fetch-worker (Service VM)
  → downloads new serving bundle from S3
  → updates app.model_versions
       │
       ▼
rollback-check-worker
  → monitors serving_metric_rollups
  → if metrics degrade: records rollback decision, reverts to previous version
```
