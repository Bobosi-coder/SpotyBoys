# Agent Kickoff Report

## Source of Truth Used

1. `../music_recommender_updated_implementation_plan_agent.md`
2. `../MlOps_system_architecture-Technicaldesign.pdf`
3. `../MlOps_system_architecture-retraining_data_flow.pdf`
4. `../MlOps_system_architecture-UI.pdf`
5. User-provided latest SpotiBoys UI rules in this implementation request

`spotiboys_implementation_plan_agent.md` and `OptionB_UI_redraft_spotiboys.*` were not present in the workspace. The available implementation plan explicitly says it is UI-synced to the latest SpotiBoys spec, so it is the active execution artifact.

## Repo Audit

The git root is `Mlops project/`. Existing code is a Python-first ML/data workspace with:

- 30Music parsing and preprocessing under `src/data_parse` and `src/data_pre_process`
- Item2Vec, retriever, and GRU ranker code under `src/item2vec`, `src/retriever`, and `src/ranker`
- older serving benchmark implementations under `src/serving` and `serving/src`
- release/object-storage helpers under `src/data_release`
- database bootstrap SQL under `db`
- mock recommendation server under `src/service/mock_recommendation_server.py`

No existing production frontend, event API, shared API contracts, Redis queue model, or Navidrome proxy boundary existed.

## Existing Components Found

- Python ML pipeline and serving benchmark code
- object-storage helper code
- initial raw/processed/ml/serving database schemas
- deterministic mock recommendation service for old `/recommend`, `/impression`, `/outcome` endpoints
- Dockerfiles for older model-serving benchmark options

## Missing Components

- Option B API contracts and shared DTOs
- SpotiBoys UI implementation
- first-party playlist drawer and bottom dock state model
- event API with idempotent impression/playback/feedback endpoints
- playable-only recommendation API contract
- media proxy/fail-closed boundary
- Redis queue/session abstraction
- new durable online application schema
- parser/export and artifact-refresh scaffolding
- demo orchestration and health checks

## Normalized Architecture Summary

Option B remains selected. The custom frontend is the product surface. Navidrome is media infrastructure only. PostgreSQL is durable application/event storage. Redis is runtime queue/session/dedupe state only. Object storage is the VM1/VM2 handoff layer. Online serving and offline retraining remain separate. VM1 does not use FAISS in the current serving runtime.

The first implementation pass adds the production boundaries without moving the existing ML code. Demo mode uses deterministic fixture data behind the same interfaces so the app can run before real Postgres/Redis/Navidrome wiring is completed.

## Latest UI Summary

The UI is SpotiBoys, not the older dashboard or persistent-right-queue layout:

- left rail shows only `SpotiBoys`
- no persistent right queue panel
- queue appears only in an on-demand playlist drawer
- desktop drawer slides from the right
- narrow drawer rises from the bottom
- bottom dock is the persistent playback anchor
- primary dock controls are skip back, play/pause, skip next, and playlist
- center browse surface has max 4 featured items and max 10 random carousel items
- queue contents are backend-owned
- drawer open/close state is local UI state only

## Implementation Order Followed

1. Shared contracts and enum freeze
2. Repository/runtime/media boundaries
3. Backend API apps
4. Frontend shell and state model
5. Parser/object-storage/artifact scaffolding
6. Demo orchestration and health checks
7. Unit and integration-style tests

## Blockers and Defaults Chosen

- Missing newest redraft files: resolved by the UI-synced plan and user-provided rules.
- Local Python is 3.8.6: new Python code is Python 3.8 compatible.
- `pytest`, `uvicorn`, Redis client, and Postgres client are not installed locally: tests use `unittest`; demo gateway uses stdlib HTTP while FastAPI apps remain available for container/runtime use.
- Real Navidrome is not configured locally: media proxy boundary generates deterministic WAV bytes for mapped fixture tracks and fails closed for unmapped/quarantined tracks.

## Directories Touched First

- `packages/shared_contracts`
- `packages/db_access`
- `packages/recommendation_engine`
- `packages/navidrome_adapter`
- `apps/recommendation-api`
- `apps/event-api`
- `apps/frontend-web`
- `infra/docker`
- `infra/scripts`
- `fixtures`
- `docs`
