# Restart Monitor Worker

VM1-owned worker for monitoring restart markers and executing graceful restarts.

Runs every minute (via cron or scheduled job) to check for `restart_required.json` marker file.
When detected, executes `docker-compose restart recommendation-api` and cleans up marker.

## Behavior

```
Every 1 minute:
  1. Check for /serving-bundle/Real_service/vm1_staged_serving/restart_required.json
  2. If exists:
     a. Read marker (version info)
     b. Execute docker-compose restart recommendation-api
     c. Delete marker file
     d. Log completion
  3. If not exists:
     - No-op, retry next minute
```

## Output

- Logs: Standard logger with INFO level
- Exit codes:
  - 0: Success (restart performed or no restart needed)
  - 1: Fatal error

## Integration

Runs via cron or Docker container:

```bash
# Every minute via cron
* * * * * cd /app && docker compose exec -T restart-monitor-worker python workers/restart-monitor-worker/monitor_restart.py
```

## Log Examples

**No restart needed**:
```
No restart marker found at /serving-bundle/Real_service/vm1_staged_serving/restart_required.json
```

**Restart performed**:
```
🔄 Restart marker detected!
   Model version: model_v1
   Serving bundle: 20260420_120000
   Executing: docker compose restart recommendation-api
   ✅ Restart command executed successfully
   ✅ Marker file deleted
✅ Restart completed
```
