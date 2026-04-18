# Retraining Runner

VM2-owned scaffold. It assembles `session_event/snapshot` plus all `session_event/delta/*` batches from object storage and runs offline retraining.

It must not read VM1 PostgreSQL directly.
