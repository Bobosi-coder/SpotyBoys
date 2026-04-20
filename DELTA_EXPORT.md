# Delta Export - 설계 및 구현

## 📐 설계 개요

- **트리거 조건**: `app.sessions` 신규 행이 1000개 이상 누적
- **실행 주기**: 매 시간 체크 (cron)
- **Checkpoint**: `app.delta_export_metadata` 테이블에 저장
- **Export 범위**: 마지막 checkpoint 이후의 신규 데이터만 (중복 방지)

---

## ✅ 구현 파일 목록

| 파일 | 상태 | 역할 |
|------|------|------|
| `db/004_delta_export_metadata.sql` | ✅ | Checkpoint 추적 테이블 |
| `packages/db_access/postgres.py` | ✅ | Repository 메서드 5개 추가 |
| `workers/parser-export-worker/export_delta.py` | ✅ | Checkpoint 기반 export |
| `workers/delta-trigger-worker/trigger_delta_export.py` | ✅ | 세션 수 체크 + 자동 트리거 |
| `docker-compose.yml` | ✅ | `delta-trigger-worker` 서비스 추가 |

---

## 📦 DB 스키마

```sql
-- db/004_delta_export_metadata.sql
CREATE TABLE app.delta_export_metadata (
    metadata_id   SERIAL PRIMARY KEY,
    delta_version VARCHAR NOT NULL UNIQUE,
    status        VARCHAR NOT NULL,   -- 'in_progress', 'completed', 'failed'
    last_exported_session_id TEXT,
    row_counts    JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);
```

---

## 🔧 PostgresRepository 추가 메서드

| 메서드 | 역할 |
|--------|------|
| `count_new_sessions_since_checkpoint()` | 마지막 checkpoint 이후 신규 sessions 수 반환 |
| `get_sessions_for_export()` | 마지막 checkpoint의 session_id 반환 |
| `record_delta_export_start(version)` | Export 시작 기록 (status='in_progress') |
| `record_delta_export_success(version, session_id, counts)` | Export 성공 기록 + checkpoint 저장 |
| `record_delta_export_failure(version, error)` | Export 실패 기록 |

---

## 📋 Manifest 형식

```json
{
  "version": "20260420_120000",
  "created_at": "2026-04-20T12:00:00Z",
  "output_files": ["playback_events.parquet", "impressions.parquet", "feedback.parquet", "users.parquet", "manifest.json"],
  "row_counts": {
    "playback_events.parquet": 1234,
    "impressions.parquet": 567,
    "feedback.parquet": 89,
    "users.parquet": 150
  },
  "source": "vm1-postgres-parser",
  "checkpoint": {
    "last_exported_session_id": "sess_5000",
    "previous_checkpoint_session_id": "sess_4000"
  }
}
```

---

## 🧪 동작 시나리오

### Checkpoint 메커니즘
```
1회차 export (checkpoint 없음):
  → SELECT * FROM app.sessions (전체)
  → max session_id = "sess_1000" 저장

2회차 export:
  → SELECT COUNT(*) FROM app.sessions WHERE session_id > "sess_1000"
  → count >= 1000 이면 export 실행
  → 새 데이터만 parquet에 포함
```

### 누적 카운팅 예시
```
Hour 1: +250 sessions → 250 < 1000, skip
Hour 2: +350 sessions → 600 < 1000, skip
Hour 3: +450 sessions → 1050 >= 1000, export! → checkpoint = "sess_1050"
Hour 4: +100 sessions → 100 < 1000, skip
Hour 5: +950 sessions → 1050 >= 1000, export! → checkpoint = "sess_2100"
```

---

## 🚀 실행 방법

```bash
# DB 마이그레이션
docker compose exec postgres psql -U postgres -d spotiboys -f db/004_delta_export_metadata.sql

# 수동 실행 (테스트)
docker compose exec delta-trigger-worker python workers/delta-trigger-worker/trigger_delta_export.py

# 로그 확인
docker compose logs delta-trigger-worker

# Export 상태 확인
docker compose exec postgres psql -U postgres -d spotiboys -c \
  "SELECT delta_version, status, row_counts, completed_at FROM app.delta_export_metadata ORDER BY created_at DESC LIMIT 5;"
```
