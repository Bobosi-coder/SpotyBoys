import json
import pandas as pd

def parse_30music_events(filepath, nrows=1000):
    """
    Parses the 30Music events.idomaar file from its graph/JSON format
    into a flat Pandas DataFrame.
    """
    data = []
    
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= nrows: # Limit for demo
                break
                
            # Idomaar format is tab-separated
            parts = line.strip().split('\t')
            if len(parts) < 5 or parts[0] != "event.play":
                continue
                
            try:
                # Column 1: Timestamp
                timestamp = int(parts[1])
                
                # Column 2: Event properties (JSON)
                properties = json.loads(parts[2])
                play_time_sec = properties.get('playtime', 0)
                
                # Column 3: Subjects (Users)
                subjects = json.loads(parts[3])
                user_id = "unknown"
                for sub in subjects.get('subjects', []):
                    if sub.get('type') == 'user':
                        user_id = sub.get('id')
                
                # Column 4: Objects (Tracks)
                objects = json.loads(parts[4])
                track_id = "unknown"
                for obj in objects.get('objects', []):
                    if obj.get('type') == 'track':
                        track_id = obj.get('id')
                        
                data.append({
                    "timestamp": timestamp,
                    "user_id": user_id,
                    "track_id": track_id,
                    "play_time_sec": play_time_sec
                })
                
            except json.JSONDecodeError:
                continue
                
    return pd.DataFrame(data)

def parse_30music_tracks(filepath, nrows=5000):
    """
    Parses the 30Music tracks.idomaar file to get track metadata.
    """
    data = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= nrows:
                break
            
            parts = line.strip().split('\t')
            if len(parts) < 3 or parts[0] != "track":
                continue
                
            try:
                track_id = parts[1]
                properties = json.loads(parts[2])
                
                data.append({
                    "track_id": track_id,
                    "track": properties.get('Title', 'Unknown'),
                    "track_len_sec": properties.get('duration', 0)
                })
            except json.JSONDecodeError:
                continue
                
    return pd.DataFrame(data)

if __name__ == "__main__":
    # Example Usage for Dawei
    print("1. Parsing events (edges)...")
    df_events = parse_30music_events("/content/30music/relations/events.idomaar")
    df_events.to_csv("events.csv", index=False)


    print("2. Parsing tracks (nodes)...")
    df_tracks = parse_30music_tracks("/content/30music/entities/tracks.idomaar")
    df_tracks.to_csv("tracks.csv", index=False)
    
    #print("3. Joining to create Tabular Format...")
    # df = pd.merge(df_events, df_tracks, on="track_id", how="left")
    
    # print(df.head())
    print("Script provides the structure to convert graph logic to tabular data.")
