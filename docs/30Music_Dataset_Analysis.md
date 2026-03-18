# 30Music Dataset Analysis
*Evaluating the 30Music Dataset for the Spotiboys MLOps Project*

## 1. Overview
The **30Music dataset** is a large-scale collection of music listening behavior sourced from Last.fm, specifically designed for evaluating recommender systems. It was introduced by Turrin et al. at RecSys 2015 and has become a standard benchmark for sequence-aware and session-based recommendation tasks.

## 2. Key Statistics & Scale
The dataset is massive and provides more than enough statistical power to train our Neural Ranker:
- **Users:** ~45,000
- **Tracks:** ~5.6 Million
- **Sessions:** 2.7 Million grouped user play sessions
- **Interactions:** 31 Million explicit play events
- **Preferences:** 4.1 Million explicit "love" (positive rating) actions
- **Metadata:** 600,000 artists, 200,000 albums, 280,000 tags

## 3. Why It's Perfect for Our Methodology
Based on our project review constraint to use real user interactions, the 30Music dataset aligns perfectly with our newly designed **Data Engineering Pipeline**:

- **Real Session Logs:** Unlike datasets that just provide aggregate play-counts, 30Music explicitly records sequential listening sessions with **timestamps**. This is the critical requirement that allows us to compute our advanced features: *Skip Streaks, Percent Completion, Session Position, and Hour of Day*.
- **Implicit & Explicit Feedback:** It contains passive "play" events alongside explicit "loved" events. We can map short listening durations to "Skips" and "Loved" tags to high-weight "Completions".
- **String Identifiers for `yt-dlp`:** The dataset uniquely identifies tracks and attaches standard metadata (Artist and Track Name strings). These are exactly the strings our pipeline will feed into `yt-dlp` to download the corresponding audio for our Spectrum Extraction phase.

## 4. Required Preprocessing (Data Engineering Steps)
To ingest 30Music into our pipeline, we will need to perform the following Spark jobs:
1. **Session Splitting:** Raw events are continuous; we will define a "session break" as any period of inactivity greater than 30 minutes.
2. **Target Filtering:** 5.6 Million tracks is too many to download via `yt-dlp`. We will aggregate play counts and filter the dataset to only include the Top 100,000 most-interacted tracks to construct our core embedding library.
3. **Feature Derivation:** Time-deltas between sequential play events will be used to estimate `play_duration` and subsequently `percent_completion`. 

## 5. Conclusion
Your teammates are exactly right. **30Music** is the ideal choice for this project. It provides the exact longitudinal, timestamped session data required to build the contextual features we just added to the LaTeX report, while providing the artist/track strings necessary to scrape the physical audio for our MTG vector alignment.
