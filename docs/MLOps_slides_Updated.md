# Real-Time Adaptive Music Recommendation System
*(Updated Slides Outline)*

---

## Slide 1: Title
**Real-Time Adaptive Music Recommendation System**
*Addressing the Cold-Start and Feedback Loop*
*(Team Spotiboys)*

---

## Slide 2: The Problem
- **Static Models:** Most recommenders are trained in batches (daily/weekly).
- **Delayed Feedback:** User skips or finishes are ignored until the next batch run.
- **The Result:** Frustrating user experience when mood or context shifts suddenly.

---

## Slide 3: Our Solution
- **Real-Time Responsiveness:** Update the “Up Next” queue instantly based on explicit triggers (`END` or `SKIP`).
- **Dynamic Context:** Shift the active user preference vector on the fly using redis caching.
- **Automated Retraining:** Monitor fallback rates and skip storms, triggering offline fine-tuning when necessary.

---

## Slide 4: Real-World Challenge: Data Alignment
- **The Issue:** We needed genuine user interactions (skips, completions) to train a real-time responsive model.
- **The Roadblock:** Datasets like XITE or 30Music contain user sessions but disconnected track IDs that don’t map to our audio catalog (MTG-Jamendo). Metadata-based joins are impossible.
- **The Objective:** Bridge interaction data with real audio content.

---

## Slide 5: The Fuel: Content-Based Spectrum Alignment
1. **User Interaction Data (30Music etc.):** Source genuine skip and completion events from real users.
2. **Audio Acquisition:** Programmatically download the music tracks from the interaction dataset using `yt-dlp`.
3. **The Bridge (Spectrum Extraction):** Pass both the downloaded interaction audio and the pure MTG-Jamendo audio through pre-trained large audio models (e.g., CLAP, Jukebox).
4. **Data Alignment:** Map all User IDs, Interactions, and candidate tracks into a single unified embedding space based purely on the acoustic spectrum.

---

## Slide 6: Model Architecture (Two-Stage)
1. **Retrieval (Stage 1):** Fast Approximate Nearest Neighbor (ANN) search on the extracted audio spectrum embeddings. Returns Top-100 candidates.
2. **Ranking (Stage 2):** Lightweight Neural Network (e.g., Two-Tower). Scores candidates dynamically using:
   - Unified audio embeddings
   - Real-time skip-rates
   - Session context window

---

## Slide 7: System Architecture Layer
- **Client:** Navidrome (streaming server frontend)
- **Serving layer:** FastAPI + ONNX Runtime (sub-100ms latency)
- **Storage:** Redis (cache+session) + PostgreSQL (event log)
- **Retraining:** Airflow triggers based on event drift

---

## Slide 8: Inference Flow Trigger
- **Event Driven:** Inference is *only* triggered on track `END` or `SKIP`.
- **Why?** Saves compute, protects against browsing noise, and aligns model calls strictly with moments of choice.
- **Fallback:** Strict 800ms timeout defaults to a baseline semantic queue to prevent playback gaps.

---

## Slide 9: Retraining & Monitoring
- **Data Collection:** Postgres stores all impressions and outcomes sequentially.
- **Trigger-Based:** Airflow monitors for elevated skip rates or latency timeouts.
- **Offline Retraining:** Model weights update when sufficient negative/positive labels accumulate without data leakage (time-split training).

---

## Slide 10: Expected Success Metrics
- **Serving:** 0.5 - 1.0 RPS steady state, handling up to 5 RPS burst.
- **Latency:** p50 < 100ms, p95 < 300ms.
- **Quality:** Lower skip-rate and higher completion-rate versus the content-similarity baseline.

---

## Slide 11: Questions & Discussion
*Thank You!*
