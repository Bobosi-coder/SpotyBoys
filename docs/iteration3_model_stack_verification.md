# SpotyBoys Iteration 3 Model-Stack Verification

## Normalized Stages

- C1: offline track encoder / Item2Vec artifact generation.
- C2: online retrieval / candidate generation.
- C3: online GRU ranker.
- C4: online policy reranker.

## C1 Fulfillment

C1 is correctly offline-only. The online request path does not recompute track encoders or Item2Vec. Serving consumes offline-produced artifacts through the manifest-confirmed bundle mounted at `SPOTIBOYS_SERVING_BUNDLE_PATH`.

Relevant artifact/interface files:

- `manifest.json`
- `user_centroids.pkl`
- `pop_scores.csv`
- `cooc_session.npz`
- `cooc_playlist.npz`

The same interface is used locally and on VM1 through `SPOTIBOYS_SERVING_BUNDLE_PATH`. The VM artifact fetch worker stages the latest object-storage `Real_service/<version>` bundle and Item2Vec artifacts into `/serving-bundle`.

## C2 Fulfillment

C2 executes online through the production retriever when `SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true`. The exact path is `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline._recommend_with_real_runtime` -> `src/retriever/retriever.py::MultiRecallRetriever.retrieve`.

Inputs:

- playable track inventory from PostgreSQL/in-memory repository,
- `pop_scores.csv`,
- `cooc_session.npz`,
- `cooc_playlist.npz`,
- `user_centroids.pkl`.

Branches executed by the production retriever:

- co-occurrence candidate scoring,
- user-centroid preference-nearest-neighbour candidate scoring,
- popularity fallback candidate scoring.

FAISS is not used. The preference branch uses NumPy dot products over memory-mapped Item2Vec embeddings.

## C3 Fulfillment

C3 executes online through the production GRU ranker when `SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true`. The exact path is `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline._recommend_with_real_runtime` -> `src/ranker/ranker.py::GRURankerInference.score`.

The ranker loads `gru_ranker.pt` and `gru_ranker_config.json` from the serving bundle, consumes Item2Vec embeddings and user centroids, and performs a batched PyTorch forward pass through `src/ranker/model.py::GRURanker`.

The lightweight fixture ranker remains usable only when `RecommendationService(..., require_full_ml_pipeline=False)` is selected by tests/debug tooling. Docker serving defaults to `SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true`; if the production C2/C3 runtime cannot load, `recommendation-api` fails instead of silently falling back.

## C4 Fulfillment

C4 executes online after C3 in `packages/recommendation_engine/pipeline.py::ServingRecommendationPipeline._recommend_with_real_runtime`.

Implemented policy behavior:

- removes recent tracks from the runtime queue context,
- filters disliked tracks,
- applies same-artist diversity limiting,
- preserves playable-only filtering by operating only after repository playability filtering.

Exploration remains conservative; it must not override playability or dislike filtering.

## Runtime Truth

The final recommendation path is:

1. Recommendation API loads a manifest-confirmed serving bundle at startup.
2. Repository returns currently playable tracks only.
3. C2 generates candidates from production retriever artifacts.
4. C3 ranks candidates through production GRU inference.
5. C4 applies runtime policy and playable/dislike/recent filters.
6. The API persists impression context before returning.
7. Queue metadata retains request and impression linkage.

The returned queue must not be a static fixture ordering shortcut. With `SPOTIBOYS_REQUIRE_FULL_ML_PIPELINE=true`, startup or recommendation fails if the real runtime cannot execute or if trained artifact track IDs do not overlap the playable Navidrome catalog.

## Tests

`tests/test_contracts_backend.py` verifies:

- retrieval/candidate generation runs,
- ranker invocation changes ordering,
- policy removes recent tracks,
- final returned items pass playable filtering,
- generated beep audio is isolated to `fixture_beep`.
