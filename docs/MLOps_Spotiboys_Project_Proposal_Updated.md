# Project Proposal: Real-Time Adaptive Music Recommendation System
*(Updated to address review feedback regarding user interaction datasets)*

## 1. Project Vision
Our goal is to build an end-to-end, real-time music recommendation system that continuously adapts to user feedback (skips and completions). Unlike static batch-trained recommenders, our system will ingest streaming interaction events, update a personalized candidate queue in real time, and trigger selective model retraining when performance drifts.

## 2. Objectives
- **Real-Time Serving:** Deliver Top-K track recommendations with sub-100ms latency.
- **Adaptive Ranking:** Adjust candidate scores on-the-fly based on immediate user actions (e.g., heavily penalize a genre if the user skips it twice in a row).
- **Automated Retraining Pipeline:** Monitor model outcomes (skip rates) and trigger offline retraining when concept drift is detected.
- **Data Alignment Strategy:** Bridge the gap between user interaction logs and rich audio content using spectrum extraction.

## 3. System Architecture
- **Client/Music Server:** Navidrome (open-source music server) acting as the frontend, simulating the user environment.
- **Serving Layer:** FastAPI application hosting the inference endpoints (Top-K retrieval + neural ranking).
- **Stream Processing:** Kafka or Redis Streams to ingest `END` (completion) and `SKIP` events.
- **Storage Layer:** Redis for low-latency session state and feature caching; PostgreSQL for durable event logging.
- **Retraining Pipeline:** Airflow/Prefect to orchestrate scheduled or trigger-based model fine-tuning.

## 4. Datasets & Data Alignment (Updated)
*Addressing the blocking review feedback to incorporate user interactions.*

Connecting a pure proxy user-interaction dataset (like XITE) to a pure audio dataset (like MTG-Jamendo) using IDs or internal metadata is impossible. To incorporate genuine user preferences while maintaining rich audio features, we propose a **Content-Based Spectrum Alignment** approach:

1. **User Interaction Data:** We will utilize a real-world user interaction dataset (such as 30Music) to obtain genuine session timelines, including user preferences, skips, and completion events.
2. **Targeted Audio Acquisition:** We will use tools like `yt-dlp` to programmatically download the physical audio for the music tracks mentioned in the interaction dataset.
3. **Audio Feature/Spectrum Extraction:** We will pass both the newly downloaded audio and the real music tracks from the MTG-Jamendo dataset through pre-trained large audio models to extract rich audio spectrums and deep embeddings.
4. **Data Alignment:** By representing all tracks in a unified, content-based embedding space (extracted spectrums), we can align the user actions from the interaction dataset with our audio catalog. The ranker learns user preferences over these universal audio features rather than disconnected catalog IDs.

## 5. Model Overview
- **Stage 1 (Retrieval):** Approximate Nearest Neighbor (ANN) search over the extracted audio embeddings to generate a candidate pool (e.g., Top-100).
- **Stage 2 (Ranking):** A lightweight neural network (e.g., Two-Tower or simple deep ranker) that scores candidates based on the unified audio spectrums, recent session context, and real-time user state (e.g., current skip-rate).

## 6. Real-Time Feedback Loop & Retraining
1. **Inference Trigger:** A Top-5 queue is maintained for the user. When a user skips or finishes a track, the player requests a new Top-K list.
2. **Context Update:** The skipped/completed track’s spectrum is immediately factored into the Redis session state, dynamically shifting the user's preference vector.
3. **Retraining Trigger:** If aggregated skip-rates exceed a threshold or fallback rates spike, Airflow triggers a retraining job using the durable Postgres event logs, updating the ranker weights.

## 7. Success Metrics
- **System Metrics:** p95 latency < 300ms; steady 0.5–1 RPS throughput; zero blocking of basic music playback.
- **Recommendation Metrics:** Reduced skip-rate compared to a baseline popularity/content-similarity heuristic; high Top-5 queue acceptance.

## 8. Challenges & Mitigation
- **Latency Spikes:** Handled via aggressive Redis caching and a strict timeout (e.g., 800ms) that falls back to a baseline queue to ensure uninterrupted playback.
- **Data Alignment Overhead:** Spectrum extraction is computationally heavy, so it will be performed entirely offline during the initialization phase.
