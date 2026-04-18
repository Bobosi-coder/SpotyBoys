# Contracts Summary

## API Contracts

### `GET /session/bootstrap`

Returns:

- `session_id`
- `user_id`
- `auth_state`
- `browse_surface.featured_items`
- `browse_surface.random_carousel_items`
- `queue.items`
- `queue.fallback_level`
- `queue.generated_at`
- `queue.drawer_default_open = false`
- `queue.revision`
- `current_track`
- `degraded.logging`
- `degraded.recommendations`

### `POST /recommendations/next`

Request includes `session_id`, `user_id`, optional `request_id`, optional `seed_track_ids`, and optional `queue_revision`.

Response includes `request_id`, `impression_id`, `model_version`, `fallback_level`, `browse_surface`, and separate server-owned `queue`.

### `POST /events/impression`

Dedupe key: `impression_id`. Confirms rendered browse-surface items and their `surface_slot`.

### `POST /events/playback`

Dedupe key: `event_id`. Links playback to `track_id`, `request_id`, `impression_id`, `session_id`, and `user_id`.

### `POST /events/feedback`

Dedupe key: `event_id`. Links explicit feedback to the same track/request/impression tuple.

### `GET /playable-track/{track_id}`

Returns `is_playable`, `stream_policy = proxy`, and same-origin `stream_path`. Missing or quarantined mappings fail closed.

### `GET /stream/{track_id}`

Proxy-only MVP stream path. It never returns raw Navidrome credentials.

## Shared Enums

- `fallback_level`: `none`, `cached-popularity`, `non-personalized`, `catalog-safe-fallback`, `session-recovery`
- `playback_event_type`: `playback_start`, `heartbeat`, `pause`, `resume`, `skip`, `complete`
- `feedback_type`: `like`, `dislike`, `save`
- `browse_surface_slot`: `featured_1` through `featured_4`, `random_1` through `random_10`

## Browse-Surface Contract

- `featured_items` is capped at 4.
- `random_carousel_items` is capped at 10.
- Browse items are not queue items.
- Frontend renders only server-approved items and never infers playability.

## Queue Contract

- Queue contents are backend-owned.
- Queue is separate from browse surface.
- Queue items carry `request_id` and `impression_id` linkage.
- `queue.revision` changes when the backend replaces queue contents.
- `queue.drawer_default_open` is false.
- Drawer open/close is local UI state and must not mutate queue contents.

## Event Linkage Contract

All playback and feedback events must include:

- `event_id`
- `session_id`
- `user_id`
- `track_id`
- `request_id`
- `impression_id`
- timestamp

`playback_start` is emitted once per playback attempt after audio starts.

## Parser Export Contract

VM1 parser exports:

- `session_tracks.parquet`
- `session_meta.parquet`
- `love.parquet`
- `manifest.json`

VM2 reads object storage only.

## Delta Manifest Contract

Path: `session_event/delta/<version>/manifest.json`

Required files:

- `session_tracks.parquet`
- `session_meta.parquet`
- `love.parquet`
- `manifest.json`

## Serving Bundle Manifest Contract

Path: `Real_service/<version>/manifest.json`

Required current VM1 serving files:

- `gru_ranker.pt`
- `gru_ranker_config.json`
- `cooc_session.npz`
- `cooc_playlist.npz`
- `user_centroids.pkl`
- `pop_scores.csv`
- `manifest.json`

FAISS files are rejected for the current VM1 serving runtime.
