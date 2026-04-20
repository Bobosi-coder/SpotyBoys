# Process Restart - 설계 및 구현

## 📐 설계 개요

새 ML 모델이 VM2에서 S3에 업로드되면, VM1의 recommendation-api를 자동으로 재시작해서 새 모델을 적용하는 메커니즘.

- **환경**: Docker Compose (개발/데모)
- **방식**: `restart_required.json` marker 파일 폴링
- **주기**: 매 1분마다 marker 감시
- **실행**: `docker compose restart recommendation-api`

---

## ✅ 구현 파일 목록

| 파일 | 상태 | 역할 |
|------|------|------|
| `workers/restart-monitor-worker/monitor_restart.py` | ✅ | Marker 감시 + restart 실행 |
| `docker-compose.yml` | ✅ | `restart-monitor-worker` 서비스 추가 |

---

## 🔄 전체 흐름

```
VM2 모델 훈련 완료
    ↓
S3: Real_service/{VERSION}/ 업로드
    ↓
artifact-fetch-worker (매시간)
    → /serving-bundle/Real_service/active 다운로드
    ↓
artifact-refresh-worker (수동/API)
    → validation
    → restart_required.json 생성
    ↓
[매 1분] restart-monitor-worker
    ├─ marker 없음 → 종료
    └─ marker 감지!
        ├─ docker compose restart recommendation-api
        ├─ marker 파일 삭제
        └─ 로그 출력
    ↓
recommendation-api 재시작 → 새 모델 로드
    ↓
사용자에게 새 추천 제공 ✅
```

---

## 📄 Marker 파일 형식

```
경로: /serving-bundle/Real_service/vm1_staged_serving/restart_required.json

내용:
{
  "status": "restart_required",
  "staged_bundle": "/object-storage/vm1_staged_serving/Real_service/20260420_120000",
  "serving_bundle_version": "20260420_120000",
  "model_version": "model_v1",
  "created_at": "2026-04-20T12:00:00Z"
}
```

Marker는 restart 완료 후 자동 삭제 → 중복 restart 방지

---

## 🧪 동작 예시

```
12:00:00  VM2 완료, 새 모델 S3 업로드
12:00:05  artifact-fetch-worker: 다운로드
12:00:10  artifact-refresh-worker: restart_required.json 생성
12:01:00  restart-monitor-worker 실행
          → marker 감지!
          → docker compose restart recommendation-api
          → marker 삭제
          → 로그: "Restart completed"
12:01:05  recommendation-api: 새 모델 로드 완료
12:02:00  restart-monitor-worker 실행
          → marker 없음 → 종료
```

---

## 🚀 실행 방법

```bash
# jobs profile 포함하여 전체 실행
docker compose --profile jobs up --build -d

# 수동 테스트
docker compose exec restart-monitor-worker python workers/restart-monitor-worker/monitor_restart.py

# 로그 확인
docker compose logs restart-monitor-worker -f

# Cron 설정 (매 1분)
# /etc/cron.d/spotiboys-restart
# * * * * * cd /path/to/SpotyBoys && docker compose exec -T restart-monitor-worker python workers/restart-monitor-worker/monitor_restart.py
```

---

## 📊 3개 Worker 역할 요약

| Worker | 실행 주기 | 역할 |
|--------|-----------|------|
| `delta-trigger-worker` | 매 1시간 | 1000 sessions 체크 → delta export |
| `artifact-refresh-worker` | 수동/API | 아티팩트 validation → marker 생성 |
| `restart-monitor-worker` | 매 1분 | marker 감시 → recommendation-api restart |
