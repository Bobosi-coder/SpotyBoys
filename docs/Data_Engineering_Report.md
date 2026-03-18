# MLOps Data Engineering Pipeline Report

This report provides a comprehensive, step-by-step breakdown of the two-stage data engineering pipeline designed to generate a sequence-aware dataset for a music recommendation model. We will explain the process and detail the purpose of every column generated along the way.

---

## Part 1: Pipeline Process Step-by-Step

### Phase 1: Storage-Optimized Audio Ingestion (`pipeline_stage1_catalog.py`)

This phase tackles the problem of acquiring high-quality audio features for songs without storing massive amounts of raw audio data, which would overwhelm local storage.

1. **Universe Selection**: Instead of randomly scraping millions of tracks from the raw `30Music` dataset, we select the top $N$ globally popular tracks (the "Universal Song Set"). This bounds the scope of our catalog to the most frequently interacted songs.
2. **Audio Acquisition (Deezer API)**: For each track in the universal set, the pipeline queries the Deezer API to download a high-quality 30-second MP3 preview. This bypasses the unreliability and bot-detection blocks associated with scraping full YouTube videos.
3. **Format Normalization**: The pipeline uses local `ffmpeg` to rapidly cross-convert the downloaded MP3 into a standard $32 \text{kHz}$ Mono WAV file, ensuring compatibility with PyTorch audio handlers.
4. **Standalone Feature Extraction**: Raw audio is useless to standard sequential models. We pass the $32\text{kHz}$ WAV through a custom, zero-dependency PyTorch implementation of the `PANN-Cnn14` architecture. This calculates a Mel spectrogram and aggregates it into a robust, 2048-dimensional dense vector representing the acoustic properties of the song.
5. **Stream-and-Delete Optimization**: As soon as the 2048-dimensional vector is acquired, the temporary MP3 and WAV files are instantly deleted. Only the vector is retained and written to `song_catalog_embeddings.csv`.

### Phase 2: Session Feature Engineering (`pipeline_stage2_training.py`)

This phase bridges the gap between raw user interactions (from the `30Music` event logs) and the acoustic vectors generated in Phase 1 to construct a contextualized dataset.

1. **Data Joining**: The raw interaction logs (filtered to our Universal Song Set) are merged with the `song_catalog_embeddings.csv` using the common `track_id` key. 
2. **Sequential Sorting**: To prepare the data for next-item prediction, interactions are strictly grouped by `session_id` and then sorted chronologically by their `position` within the session.
3. **Rolling Context Aggregation**: The model needs to know what the user was listening to *prior* to their current choice. We calculate the `context_embedding` as the expanding, cumulative mean of all previous track vectors within that specific session.
4. **Zero-Leakage Padding (Cold Starts)**: The very *first* track in any given session has no prior history. To prevent data leakage (using the current track's audio to predict the current track), the pipeline forces the first interaction's context vector to an array of zeroes (a cold start). It shifts the rolling mean down by one position for all subsequent tracks.

---

## Part 2: Generated Datasets and Column Explanations

The pipeline generates several crucial CSV datasets along the way. Below is an exhaustive explanation of every column in the output files located in the `processed_data/` directory.

### 1. `universe_metadata.csv`
*This file defines the strict boundary of our catalog. Any song outside this universe is ignored by the pipeline.*
*   **`track_id`**: The unique integer identifier assigned to a song in the `30Music` dataset. This acts as the Foreign Key connecting the catalog to the user sessions.
*   **`artist_hint`**: A string containing the name of the artist. Used to query external APIs (like Deezer) for audio.
*   **`title`**: A string containing the name of the song. Also used for querying external APIs.

### 2. `filtered_sessions.csv`
*This file contains the raw user interaction logs from the `30Music` dataset, filtered to ensure every listed `track_id` exists securely within our `universe_metadata.csv`.*
*   **`session_id`**: A unique identifier for a specific, uninterrupted listening session by a user.
*   **`user_id`**: The unique identifier for the specific person interacting with the platform.
*   **`position`**: An integer indicating the chronological order of the track played within that specific session (e.g., $1$ means the first song played, $8$ means the eighth).
*   **`track_id`**: The song that was interacted with.
*   **`playstart`**: A UNIX timestamp indicating the exact moment the interaction began.
*   **`playtime`**: Integer representing the duration the track was played, in seconds.
*   **`playratio`**: The ratio of the `playtime` to the actual length of the track. If $>1.0$, the user repeated parts of the song; if $<1.0$, the user skipped.
*   **`action`**: The type of interaction recorded (e.g., "play", "skip").
*   **`label`**: A categorized sentiment of the action (e.g., "positive" for a full play, "negative" for a quick skip).

### 3. `song_catalog_embeddings.csv`
*The direct output of Phase 1. This is a highly efficient lookup table linking track IDs directly to their acoustic fingerprints.*
*   **`track_id`**: The unique identifier for the song.
*   **`embedding`**: A 2048-dimensional float32 vector represented as a list string. This array captures the dense acoustic features of the audio snippet as interpreted by the `PANN-Cnn14` neural network. 

### 4. `training_dataset_v1.csv`
*The final output of Phase 2. This is the sequence-aware dataset that will be directly fed into PyTorch Dataloaders for training the recommendation engine.*
*   *(All columns from `filtered_sessions.csv` are inherited intact, preserving the session and sequence data)*
*   **`context_embedding`**: A 2048-dimensional array string representing the historical acoustic context *before* the current `track_id` was selected. 
    *   *Purpose*: It aggregates the embeddings of all songs the user listened to previously in that specific `session_id`.
    *   *Importance*: This acts as the primary Input (`X`) for the model. The model will analyze this context vector and attempt to predict the Target (`y`, the actual `track_id` selected). If `position == 1`, this column is purely zeroes, forcing the model to make a generic popularity prediction without any contextual hints. 
