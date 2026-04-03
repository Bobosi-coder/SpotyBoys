'''
This script deleted the track which are not downloaded successuffly.
And update the universe track.
Based on the updated universe track data, we clean the session, playlist and love.
Removed un-downloaded tracks from session, playlist and love.
If the session and playlist have zero tracks after filtering, we delete the session/playlist metadata entry.

'''
import pandas as pd
import os
from tqdm import tqdm




# ==========================================
# 配置区域 (Configuration)
# ==========================================
DATA_DIR = "./data/raw/content/30music_parsed"
PROCESSED_DIR = "./data/processed"
MP3_DIR = "/Volumes/T7/MLOps_music_track"

#input data path
UNIVERSE_META = os.path.join(PROCESSED_DIR, "universe_metadata.csv")
LOVE_FILE = os.path.join(DATA_DIR, "love.csv")
PLAYLIST_TRACKS_FILE = os.path.join(DATA_DIR, "playlist_tracks.csv")
PLAYLIST_META_FILE = os.path.join(DATA_DIR, "playlist_meta.csv")
SESSION_TRACKS_FILE = os.path.join(DATA_DIR, "session_tracks.csv")
SESSION_META_FILE = os.path.join(DATA_DIR, "session_meta.csv")

# outpu data file path
FILTERED_UNIVERSE = os.path.join(PROCESSED_DIR, "universe_track_filtered.csv")
OUT_LOVE = os.path.join(PROCESSED_DIR, "love_filtered.csv")
OUT_PLAYLIST_TRACKS = os.path.join(PROCESSED_DIR, "playlist_tracks_filtered.csv")
OUT_PLAYLIST_META = os.path.join(PROCESSED_DIR, "playlist_meta_filtered.csv")
OUT_SESSION_TRACKS = os.path.join(PROCESSED_DIR, "session_tracks_filtered.csv")
OUT_SESSION_META = os.path.join(PROCESSED_DIR, "session_meta_filtered.csv")


def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    print("Start the data filtering process pipeline")
    print("We will delete the entry that downloaded unsuccesully. And filtering other realated files")

    # ==========================================
    # Phase 1: Check the mp3 files (generate Source of Truth)
    # ==========================================
    print(f"Checking the mp3 files. Delete the tracks downloaded unsuccessfully.")
    df_universe = pd.read_csv(UNIVERSE_META)
    initial_universe_count = len(df_universe)

    def is_mp3_exists(track_id):
        file_path = os.path.join(MP3_DIR, f"{int(track_id)}.mp3")
        return os.path.exists(file_path)
    
    mask = df_universe['track_id'].apply(is_mp3_exists)
    df_universe_filtered = df_universe[mask]
    df_universe_filtered.to_csv(FILTERED_UNIVERSE, index=False)

    valid_track_ids = set(df_universe_filtered['track_id'].astype(int))
    print(f"    Universe is filtered. Keep {len(valid_track_ids)} / {initial_universe_count} tracks")


    # ==========================================
    # Phase 2: clean Love table
    # ==========================================
    print("\n Cleaning love.csv...")
    love_df = pd.read_csv(LOVE_FILE)
    initial_love = len(love_df)
    love_df = love_df[love_df['track_id'].isin(valid_track_ids)]
    love_df.to_csv(OUT_LOVE, index=False)
    print(f"   Love Keep/Original: {len(love_df)} / {initial_love}")

    # ==========================================
    # Phase 3: clean Playlist and playlist Meta
    # ==========================================
    print("\n cleaning Playlist data...")
    # 3.1 filter tracks
    pt_df = pd.read_csv(PLAYLIST_TRACKS_FILE)
    initial_pt = len(pt_df)
    pt_df = pt_df[pt_df['track_id'].isin(valid_track_ids)]
    pt_df.to_csv(OUT_PLAYLIST_TRACKS, index=False)
    
    valid_playlist_ids = set(pt_df['playlist_id'].unique())
    print(f"   Playlist Tracks Keep/Original: {len(pt_df)} / {initial_pt}")

    # 3.2 过滤 meta 并删除失效的统计列
    pm_df = pd.read_csv(PLAYLIST_META_FILE)
    initial_pm = len(pm_df)
    pm_df = pm_df[pm_df['playlist_id'].isin(valid_playlist_ids)]
    
    # 顺手 Drop 掉不再准确且不再使用的特征列
    cols_to_drop_pm = ['num_tracks', 'duration']
    pm_df = pm_df.drop(columns=[c for c in cols_to_drop_pm if c in pm_df.columns], errors='ignore')
    
    pm_df.to_csv(OUT_PLAYLIST_META, index=False)
    print(f"   Playlist Meta Keep/Original: {len(pm_df)} / {initial_pm} (Removed Col #: {cols_to_drop_pm})")

    # ==========================================
    # 阶段 4: 清理 Session Tracks (Chunk 处理)
    # ==========================================
    print("\n 级联清理 Session Tracks (Chunked)...")
    chunk_size = 1000000 
    valid_session_ids = set()
    
    reader = pd.read_csv(SESSION_TRACKS_FILE, chunksize=chunk_size)
    if os.path.exists(OUT_SESSION_TRACKS):
        os.remove(OUT_SESSION_TRACKS)

    total_session_tracks_kept = 0
    first_chunk = True
    
    for chunk in tqdm(reader, desc="处理 session_tracks"):
        filtered_chunk = chunk[chunk['track_id'].isin(valid_track_ids)]
        valid_session_ids.update(filtered_chunk['session_id'].unique())
        
        filtered_chunk.to_csv(OUT_SESSION_TRACKS, mode='a', header=first_chunk, index=False)
        total_session_tracks_kept += len(filtered_chunk)
        first_chunk = False

    print(f"   Session Tracks 最终保留条目: {total_session_tracks_kept}")
    print(f"   存活的 Session 数量: {len(valid_session_ids)}")

    # ==========================================
    # 阶段 5: 清理 Session Meta
    # ==========================================
    print("\n 级联清理 Session Meta...")
    sm_df = pd.read_csv(SESSION_META_FILE)
    initial_sm = len(sm_df)
    sm_df = sm_df[sm_df['session_id'].isin(valid_session_ids)]
    
    # 顺手 Drop 掉不再准确且不再使用的特征列
    cols_to_drop_sm = ['num_tracks', 'total_playtime']
    sm_df = sm_df.drop(columns=[c for c in cols_to_drop_sm if c in sm_df.columns], errors='ignore')
    
    sm_df.to_csv(OUT_SESSION_META, index=False)
    print(f"   Session Meta 保留/原始: {len(sm_df)} / {initial_sm} (已移除列: {cols_to_drop_sm})")

    print("\n 全量端到端清洗完成！数据现已绝对干净、对齐，且移除了冗余字段。")

if __name__ == "__main__":
    main()