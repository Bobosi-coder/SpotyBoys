# Parser Export Worker

VM1-owned scaffold for exporting PostgreSQL serving truth into snapshot-compatible parquet deltas.

The contract is frozen in `packages/parser_transform/contracts.py`; implementation should read durable online tables and write:

- `session_tracks.parquet`
- `session_meta.parquet`
- `love.parquet`
- `manifest.json`

VM2 must consume object storage output only, never PostgreSQL directly.
