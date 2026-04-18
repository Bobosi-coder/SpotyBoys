# SpotyBoys Iteration 3 Model-Stack Verification

## Normalized Stages

- C1: offline track encoder / Item2Vec artifact generation.
- C2: online retrieval / candidate generation.
- C3: online GRU ranker.
- C4: online policy reranker.

## C1 Fulfillment

C1 is correctly offline-only. The online request path does not recompute track encoders or Item2Vec. Serving consumes offline-produced artifacts through the manifest-confirmed bundle under `fixtures/serving_bundle/Real_service/demo-fixture-v1`.

Relevant artifact/interface files:

- `manifest.json`
- `user_centroids.pkl`
- `pop_scores.csv`
- `cooc_session.npz`
- `cooc_playlist.npz`

The same interface is used locally and on VM1 through `SPOTIBOYS_SERVING_BUNDLE_PATH`.

## C2 Fulfillment

C2 now executes online in `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline.retrieve_candidates`.

Inputs:

- playable track inventory from PostgreSQL/in-memory repository,
- `pop_scores.csv`,
- `cooc_session.npz`,
- `cooc_playlist.npz`,
- `user_centroids.pkl`.

Branches implemented now:

- co-occurrence candidate scoring,
- user-centroid candidate scoring,
- popularity fallback candidate scoring.

Nearest-neighbour retrieval is not implemented in the VM1 runtime and remains deferred because FAISS is explicitly out of current serving scope.

## C3 Fulfillment

C3 now executes online in `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline.rank_candidates`.

The ranker loads `gru_ranker.pt` from the serving bundle. In the local fixture bundle this is a lightweight JSON checkpoint that changes ordering through ranker weights and track bias. This proves the serving path invokes the ranker artifact and that recommendation ordering is not static fixture order. Production tensor GRU inference remains iteration 4 work.

## C4 Fulfillment

C4 now executes online in `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline.apply_policy`.

Implemented policy behavior:

- removes recent tracks from the runtime queue context,
- applies same-artist diversity penalty,
- preserves playable-only filtering by operating only after repository playability filtering.

Dislike filtering and exploration are not yet wired to durable feedback history and remain iteration 4 work.

## Runtime Truth

The final recommendation path is:

1. Recommendation API loads a manifest-confirmed serving bundle at startup.
2. Repository returns currently playable tracks only.
3. C2 generates candidates from serving artifacts.
4. C3 ranks candidates through the ranker artifact.
5. C4 applies runtime policy.
6. The API persists impression context before returning.
7. Queue metadata retains request and impression linkage.

The returned queue is no longer a static fixture ordering shortcut.

## Tests

`tests/test_contracts_backend.py` verifies:

- retrieval/candidate generation runs,
- ranker invocation changes ordering,
- policy removes recent tracks,
- final returned items pass playable filtering,
- generated beep audio is isolated to `fixture_beep`.
