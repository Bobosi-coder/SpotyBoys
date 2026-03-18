# Data Engineering Pipeline: Audio & Interaction Alignment Platform
*(Incorporating 30Music/LastFM + MTG-Jamendo via Spectrum Extraction)*

## 1. Overview & The "Missing Link" Problem
The core challenge in building a real-time music recommender is the **dataset disconnect**:
- **Dataset A (User Actions - 30Music Dataset):** Contains 31 Million explicit play events, 2.7 Million rich user interaction sessions (Plays, Skips, Timestamps) but *no actual audio files*. Tracks are represented as abstract strings/IDs.
- **Dataset B (Catalog - MTG-Jamendo):** Contains rich, royalty-free audio files and metadata, but *zero user interaction* data.

**The Solution:** A unified Data Platform that uses **Content-Based Spectrum Extraction** to bridge the gap. We will programmatically acquire the audio for the 30Music interaction tracks, extract deep audio embeddings for both datasets, and train the model purely on these universal "sound spectrums" rather than disconnected catalog IDs.

---

## 2. Data Engineering Architecture (Step-by-Step)

### Phase 1: Ingestion & Sourcing (The "Raw" Zone)
1. **User Interaction Ingestion:**
   - **Source:** 30Music Dataset.
   - **Storage:** Load raw CSV/JSON files into a Data Lake. **Local Storage Feasibility:** Since 30Music metadata is purely text/CSV, it is relatively small (estimated 2-5GB total). It can be easily stored on local development machines or standard block storage attached to the active database.
   - **Tech:** Apache Spark for initial parsing, deduplication, and cleaning of invalid short sessions.
2. **Audio Catalog Ingestion:**
   - **Source:** MTG-Jamendo dataset (pre-downloaded open-source audio files).
   - **Storage:** Store `.mp3`/`.ogg` files alongside metadata in object blob storage.

### Phase 2: Target Audio Acquisition (The "Scraping" Worker Fleet)
1. **Identify High-Value Targets:**
   - Use Spark to extract the most frequently interacted tracks (Track Name + Artist) from the 30Music dataset to avoid downloading unused long-tail songs. We will target the top 100,000 to limit storage overhead.
2. **Distributed Downloading (`yt-dlp`):**
   - Deploy a worker queue.
   - Workers execute `yt-dlp` scripts to search and download the high-value tracks based on the strings `Artist Name - Track Name`.
   - **Local Storage Strategy:** Audio files are large. 100,000 MP3s at 3 minutes each (~5MB) equals ~500GB. This *can* be stored on a local machine with a dedicated external SSD or a large internal drive, but cloud object storage (S3) is recommended if local space is restricted. We can also aggressively delete the `.mp3` files locally the moment the embedding vector is extracted.

### Phase 3: Processing & Feature Extraction (The "Spectrum Bridge")
1. **Batch Audio Processing Job:**
   - **Framework:** Ray or Spark with GPU compute nodes.
   - **Model:** A pre-trained deep audio model like **CLAP** (Contrastive Language-Audio Pretraining), **Jukebox**, or **Essentia** embeddings.
   - **Process:**
     - Worker reads an `.mp3` from the Data Lake.
     - Converts it to a Mel-Spectrogram (STFT).
     - Passes through the neural network to output a dense vector embedding (e.g., 512 dimensions).
     - **Cleanup:** If operating strictly on Local Storage, the raw `.mp3` is deleted locally to save space immediately after the vector is computed.
2. **Unified Embedding Storage:**
   - Store all extracted embeddings in a Vector Database (e.g., **Milvus**, **Qdrant**, or **Pinecone**).
   - **Local Storage:** Dense vectors are extremely lightweight array floats. 100,000 track embeddings multiplied by 512 dimensions uses under 500MB of storage. This easily fits into local RAM/Redis.
   - *Crucial Impact:* Now, track "123" from 30Music and track "456" from MTG-Jamendo are represented by the exact same mathematical medium (audio vectors) in the exact same dimensional space.

### Phase 4: Training Data Alignment & Granular Feature Engineering
1. **Granular Session Feature Extraction:**
   - Raw `SKIP`/`COMPLETE` labels are too binary. Using Spark, we will calculate continuous and categorical behavioral features from the session timelines:
     - **Percent Completion:** `(Play Duration / Total Track Duration)`. A 90% completion is a strong positive signal compared to a 10% immediate skip.
     - **Relative Skip Position:** Did the skip occur before or after the median track length? (e.g., `Early_Skip`, `Late_Skip`, `Drop_Off_At_Chorus`).
     - **Replay Count:** Number of times the track was played repeatedly within a single session or historical window.
     - **Temporal Context:** Hour of the day and Day of the week. User music preferences vary drastically between morning commutes and weekend nights.
     - **Sequence Dynamics (Skip Streaks):** Count of consecutive skips prior to the current track. A high streak indicates the user is in an active, highly selective mood (or highly dissatisfied).
     - **Session Position:** The index of the track in the current listening session (e.g., track 1 vs track 50). Users experience "listening fatigue" later in sessions, making them more likely to skip regardless of track quality.
     - **Session Context Momentum:** Expanding the "user context embedding" to weight recently replayed tracks higher than single-play tracks.
2. **Joining Logs with Vectors & Features:**
   - Join the enriched session timelines with the extracted audio embeddings.
   - **Example Output Row:** `[User_Context_Embedding, Target_Track_Embedding, Percent_Completed: 0.85, Replay_Count: 2, Skip_Streak: 0, Hour_of_Day: 8, Target_Label: 1.0]`
3. **Feature Store Publishing:**
   - Push the clean training artifacts (TFRecords or Parquet files) to the offline model training pipeline.
   - Publish the MTG-Jamendo candidate embeddings to the **Online Feature Store (Redis/Feast)** for low-latency Real-Time inference later.

### Phase 5: Online Serving & Real-Time Feedback Loop
- **Streaming Ingestion:** The web player client (Navidrome) sends streaming `END/SKIP` events to an Apache Kafka or Redpanda topic.
- **Session Update:** A streaming job (Flink or Faust) consumes events and immediately updates the active user's "current context embedding" in Redis.
- **Inference Trigger:** The Neural Ranker retrieves the Top-K closest MTG-Jamendo candidate embeddings from the Vector DB, scoring them against the real-time user state.

---

## 3. Technology Stack Summary
- **Storage Layer:** S3 / MinIO (Raw Audio & Datasets)
- **Data Processing & ETL:** Apache Spark (Batch Joins), Celery/Ray (yt-dlp async workers)
- **Feature Extraction:** PyTorch (GPU), CLAP / Essentia
- **Online Feature Store:** Redis (Session state), Milvus/Qdrant (Vector DB for ANN search)
- **Streaming / Event Bus:** Apache Kafka
- **Orchestration:** Apache Airflow or Prefect

## 4. Why This Solves the Professor's Blocking Issue
By building this pipeline, we no longer rely on simplistic (and flawed) metadata matching between datasets or IDs that don't overlap. The professor required us to incorporate *real user interaction*. By aggressively mirroring the real audio for the interacting Last.fm tracks, and uniting them with our MTG open catalog via deep spectrum vectors, we create a mathematically sound bridge that enables training a generalized audio recommender.
