# Delta Trigger Worker

VM1-owned worker for monitoring session accumulation and triggering delta export.

Runs hourly (via cron or scheduled job) to check if new sessions count has reached the threshold (default: 1000).
When threshold is reached, triggers `export_delta.py` to export accumulated session data.

## Behavior

```
Every hour:
  1. Check new_sessions_count since last checkpoint
  2. If count >= 1000:
     a. Call export_delta()
     b. Record checkpoint in app.delta_export_metadata
     c. Update last_exported_session_id
  3. Otherwise:
     - No-op, retry next hour
```

## Output

- Logs: Standard logger with INFO level
- Exit codes:
  - 0: Success (export triggered or threshold not reached)
  - 1: Fatal error

## Integration

Runs via cron or Kubernetes CronJob:

```bash
# Hourly via cron
0 * * * * cd /app && python workers/delta-trigger-worker/trigger_delta_export.py
```
