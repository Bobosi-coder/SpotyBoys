# SpotyBoys VM1 - 전체 구현 현황

## 📊 구현 완료도 요약

| 컴포넌트 | 상태 | 완성도 | 비고 |
|---------|------|--------|------|
| recommendation-api | ✅ | 100% | 온라인 서빙 완벽 구현 |
| event-api | ✅ | 100% | 이벤트 수집 완벽 구현 |
| PostgreSQL Storage | ✅ | 100% | 스키마 완벽 구현 |
| Parser Export | ✅ | 100% | Checkpoint 기반 export 구현 |
| Delta Trigger Worker | ✅ | 100% | 1000 sessions 기준 자동 trigger |
| Artifact Fetch/Refresh | ✅ | 90% | 다운로드 및 validation 구현 |
| Process Restart Monitor | ✅ | 100% | 매 1분 marker 감시 + restart 구현 |
| Schema Validation | ⚠️ | 0% | VM2 스키마 확정 후 구현 예정 |
| Monitoring/Alerting | ❌ | 0% | 미구현 |

---

## 🗂️ 데이터 구조

### PostgreSQL 테이블 (app schema)

| 테이블 | 역할 | Parquet 파일 |
|--------|------|------------|
| `app.playback_events` | 재생 이벤트 | `playback_events.parquet` |
| `app.recommendation_impressions` | 추천 노출 데이터 | `impressions.parquet` |
| `app.feedback_events` | 좋아요/싫어요 | `feedback.parquet` |
| `app.sessions` | 사용자 세션 | (trigger 기준) |
| `app.users` | 사용자 정보 | `users.parquet` |
| `app.delta_export_metadata` | Delta export checkpoint | (내부 관리) |

### Object Storage 레이아웃

```
proj23-mlflow-artifacts/
├── Real_service/{VERSION}/     → 추천 서빙 번들 (VM2 생성)
├── session_event/snapshot/     → 기초 데이터 (불변)
│   ├── session_tracks_i2v.parquet
│   ├── session_meta_i2v.parquet
│   └── love_i2v.parquet
└── session_event/delta/{VERSION}/  → 신규 데이터 (append-only)
    ├── playback_events.parquet
    ├── impressions.parquet
    ├── feedback.parquet
    ├── users.parquet
    └── manifest.json
```

---

## ⚠️ 스키마 불일치 이슈 (미해결)

**Snapshot 파일** (30Music 원본): 컬럼 수가 적음
- `session_tracks_i2v.parquet`: 6개 컬럼 (session_id, user_id, position, track_id, playratio, label)
- `session_meta_i2v.parquet`: 2개 컬럼 (session_id, user_id)
- `love_i2v.parquet`: 2개 컬럼 (user_id, track_id)

**현재 Delta Export**: app 테이블 전체를 export하므로 컬럼 수가 더 많음
- `playback_events.parquet`: 12개 컬럼
- `impressions.parquet`: 9개 컬럼
- `feedback.parquet`: 9개 컬럼

**해결 방향**: VM2 retraining 코드의 실제 입력 형식 확인 후 export 컬럼 조정 필요

---

## 🔄 전체 데이터 흐름

```
사용자 상호작용
    ↓
Event API → app.playback_events / app.feedback_events
Recommendation API → app.recommendation_impressions
    ↓
[매시간] delta-trigger-worker
    ├─ app.sessions 신규 개수 체크
    └─ 1000개 이상 → export_delta() 실행
            ↓
    session_event/delta/{VERSION}/ 생성
            ↓
    VM2 retraining 시작
            ↓
    새 모델 → Real_service/{VERSION}/
            ↓
[매시간] artifact-fetch-worker → 다운로드
[수동/API] artifact-refresh-worker → validation + restart_required.json 생성
[매 1분] restart-monitor-worker → marker 감지 → recommendation-api restart
            ↓
새 모델로 추천 제공 ✅
```

---

## 🛠️ 남은 작업

### 단기 (필요 시)
- **Schema Validation**: VM2 코드의 실제 입력 스키마 확인 후 export_delta.py 컬럼 조정
- **Error Handling**: export 실패 시 부분 파일 정리 로직

### 중기
- **Monitoring**: export 실패 알림, artifact availability 모니터링
- **Graceful Degradation**: S3 장애 시 이전 artifact 사용 전략
