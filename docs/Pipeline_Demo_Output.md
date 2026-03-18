# Data Engineering Pipeline: Execution Demo
```text
🚀 Starting Data Engineering Pipeline POC...

📊 1. Raw Interaction Data (Simulated Last.fm):
  session_id user_id           timestamp  ... track_len_sec replay_count    action
0         S1      U1 2026-03-16 08:15:00  ...           240            1  COMPLETE
1         S1      U1 2026-03-16 08:19:00  ...           212            0      SKIP
2         S2      U2 2026-03-14 22:30:00  ...           354            0      SKIP
3         S2      U2 2026-03-14 22:35:00  ...           240            3  COMPLETE

[4 rows x 9 columns]

⚙️ 1b. Feature Engineering (Granular Session Features):
  user_id                    track  ...  skip_streak    action
0      U1                Get Lucky  ...            0  COMPLETE
1      U1  Never Gonna Give You Up  ...            0      SKIP
2      U2        Bohemian Rhapsody  ...            0      SKIP
3      U2                Get Lucky  ...            1  COMPLETE

[4 rows x 7 columns]

🎧 2. Audio Acquisition & Feature Extraction:
🔍 Searching and downloading: Daft Punk - Get Lucky...
⚠️ Failed to download Daft Punk - Get Lucky: [0;31mERROR:[0m [youtube] CCHdMIEGaaM: Requested format is not available. Use --list-formats for a list of available formats. Falling back to mock file...
🧠 Extracting features from ./audio_cache/Daft_Punk_Get_Lucky_mock.mp3...
✅ Spectrum Embedding shape (Mocked): (13,)
🔍 Searching and downloading: Rick Astley - Never Gonna Give You Up...
⚠️ Failed to download Rick Astley - Never Gonna Give You Up: [0;31mERROR:[0m [youtube] dQw4w9WgXcQ: Requested format is not available. Use --list-formats for a list of available formats. Falling back to mock file...
🧠 Extracting features from ./audio_cache/Rick_Astley_Never_Gonna_Give_You_Up_mock.mp3...
✅ Spectrum Embedding shape (Mocked): (13,)
🔍 Searching and downloading: Queen - Bohemian Rhapsody...
⚠️ Failed to download Queen - Bohemian Rhapsody: [0;31mERROR:[0m [youtube] fJ9rUzIMcZQ: Requested format is not available. Use --list-formats for a list of available formats. Falling back to mock file...
🧠 Extracting features from ./audio_cache/Queen_Bohemian_Rhapsody_mock.mp3...
✅ Spectrum Embedding shape (Mocked): (13,)

🔗 3. Joining Interaction Logs with Audio Embeddings:
  user_id       artist  ... skip_streak    action
0      U1    Daft Punk  ...           0  COMPLETE
1      U1  Rick Astley  ...           0      SKIP
2      U2        Queen  ...           0      SKIP
3      U2    Daft Punk  ...           1  COMPLETE

[4 rows x 6 columns]

Sample Audio Vector Data attached to first interaction row:
Dimension (total 13): [-0.043 -0.663  2.415 -0.038  0.093] ...

🎉 POC Pipeline Complete! Vectors and Granular Features are ready for Neural Ranker training.
```
