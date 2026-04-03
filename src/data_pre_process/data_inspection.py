"""
30Music Dataset — Complete Data Inspection
Outputs: src/data_pre_process/data_inspection_report.txt
         src/data_pre_process/figures/*.png
Console: only file-loading progress lines
"""

import ast
import os
import time
from collections import defaultdict, Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────────
RAW     = "./data/raw/content/30music_parsed"
OUT_TXT = "./src/data_pre_process/data_inspection_report.txt"
FIG_DIR = "./src/data_pre_process/figures"

os.makedirs(FIG_DIR, exist_ok=True)

CHUNK = 500_000

# ── helpers ───────────────────────────────────────────────────────────────────

def load(filename, **kwargs):
    path = os.path.join(RAW, filename)
    print(f"Loading {filename}...")
    t0 = time.time()
    df = pd.read_csv(path, low_memory=False, **kwargs)
    print(f"Done. ({time.time()-t0:.1f}s)")
    return df


def safe_literal(x):
    try:
        return ast.literal_eval(x)
    except Exception:
        return []


def fmt(n):
    return f"{int(n):,}"


def pct(n, total):
    return f"{100*n/total:.1f}%" if total else "N/A"


def save_fig(name):
    plt.savefig(os.path.join(FIG_DIR, name), dpi=120, bbox_inches="tight")
    plt.close()


def iter_session_tracks(usecols=None):
    """Yield chunks of session_tracks.csv."""
    path = os.path.join(RAW, "session_tracks.csv")
    for chunk in pd.read_csv(path, chunksize=CHUNK, low_memory=False,
                              usecols=usecols):
        yield chunk


# ── open report ───────────────────────────────────────────────────────────────
rep = open(OUT_TXT, "w", encoding="utf-8")


def w(*args, **kwargs):
    print(*args, **kwargs, file=rep)


def section(title):
    w("\n" + "=" * 70)
    w(f"  {title}")
    w("=" * 70)


def subsection(title):
    w(f"\n--- {title} ---")


# ─────────────────────────────────────────────────────────────────────────────
# Load small tables
# ─────────────────────────────────────────────────────────────────────────────
w("30Music Dataset — Data Inspection Report")
w(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")

users_df      = load("users.csv")
tracks_df     = load("tracks.csv")
persons_df    = load("persons.csv")
albums_df     = load("albums.csv")
tags_df       = load("tags.csv")
love_df       = load("love.csv")
playlist_meta = load("playlist_meta.csv")
playlist_trk  = load("playlist_tracks.csv")
session_meta  = load("session_meta.csv")

# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — Data Integrity
# ─────────────────────────────────────────────────────────────────────────────
section("PART 1 — DATA INTEGRITY")

# 1-1 row counts
subsection("1.1 Actual row counts")
small_tables = {
    "tracks.csv":        len(tracks_df),
    "persons.csv":       len(persons_df),
    "albums.csv":        len(albums_df),
    "tags.csv":          len(tags_df),
    "users.csv":         len(users_df),
    "love.csv":          len(love_df),
    "playlist_meta.csv": len(playlist_meta),
    "playlist_tracks.csv": len(playlist_trk),
    "session_meta.csv":  len(session_meta),
}
for name, n in small_tables.items():
    w(f"  {name:<30} {fmt(n)} rows")

print("Counting session_tracks.csv rows...")
t0 = time.time()
st_nrows = sum(len(c) for c in iter_session_tracks(usecols=["session_id"]))
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  {'session_tracks.csv':<30} {fmt(st_nrows)} rows")

# 1-2 null values
subsection("1.2 Null values per column")

def null_report(name, df):
    w(f"\n  [{name}]")
    shown = 0
    for col in df.columns:
        n = df[col].isna().sum()
        if n > 0:
            w(f"    {col:<30} nulls: {fmt(n)}  ({pct(n, len(df))})")
            shown += 1
    if not shown:
        w("    (no nulls)")

for name, df in [
    ("tracks.csv", tracks_df), ("persons.csv", persons_df),
    ("albums.csv", albums_df), ("tags.csv", tags_df),
    ("users.csv", users_df), ("love.csv", love_df),
    ("playlist_meta.csv", playlist_meta), ("playlist_tracks.csv", playlist_trk),
    ("session_meta.csv", session_meta),
]:
    null_report(name, df)

print("Scanning nulls in session_tracks.csv...")
t0 = time.time()
st_null_acc = None
for chunk in iter_session_tracks():
    cn = chunk.isna().sum()
    st_null_acc = cn if st_null_acc is None else st_null_acc + cn
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"\n  [session_tracks.csv]  (total rows: {fmt(st_nrows)})")
shown = 0
for col, n in st_null_acc.items():
    if n > 0:
        w(f"    {col:<30} nulls: {fmt(n)}  ({pct(n, st_nrows)})")
        shown += 1
if not shown:
    w("    (no nulls)")

# 1-3 orphan track_ids
subsection("1.3 Orphan track_ids in session_tracks")
track_id_set = set(tracks_df["track_id"].dropna().astype(int))
print("Scanning orphan track_ids in session_tracks.csv...")
t0 = time.time()
orphan_tids = set()
for chunk in iter_session_tracks(usecols=["track_id"]):
    ids = chunk["track_id"].dropna().astype(int)
    orphan_tids.update(ids[~ids.isin(track_id_set)])
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  Unique orphan track_ids: {fmt(len(orphan_tids))}")

# 1-4 orphan user_ids
subsection("1.4 Orphan user_ids in session_tracks")
user_id_set = set(users_df["user_id"].dropna().astype(int))
print("Scanning orphan user_ids in session_tracks.csv...")
t0 = time.time()
orphan_uids = set()
for chunk in iter_session_tracks(usecols=["user_id"]):
    uids = chunk["user_id"].dropna().astype(int)
    orphan_uids.update(uids[~uids.isin(user_id_set)])
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  Unique orphan user_ids: {fmt(len(orphan_uids))}")

# 1-5 orphan playlist_ids
subsection("1.5 Orphan playlist_ids in playlist_tracks")
valid_plids  = set(playlist_meta["playlist_id"].dropna().astype(int))
orphan_plids = set(playlist_trk["playlist_id"].dropna().astype(int)) - valid_plids
w(f"  Unique orphan playlist_ids: {fmt(len(orphan_plids))}")

# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — session_tracks core  (single comprehensive pass)
# ─────────────────────────────────────────────────────────────────────────────
section("PART 2 — SESSION_TRACKS CORE ANALYSIS")

print("Full stats pass over session_tracks.csv...")
t0 = time.time()

PR_BINS   = [-np.inf, 0.5, 0.9, 1.1, np.inf]
PR_LABELS = ["<0.5", "0.5-0.9", "0.9-1.1", ">1.1"]

label_counts   = Counter()
pr_buckets     = Counter()          # "null" + PR_LABELS
pr_label_cross = defaultdict(Counter)   # bucket → label → count
session_len_map = Counter()
user_event_acc  = Counter()
user_session_set = defaultdict(set)    # uid → set of session_ids
user_track_set   = defaultdict(set)    # uid → set of track_ids
user_positive    = set()
pr_sample        = []
PR_SAMPLE_CAP    = 1_000_000
pr_sampled       = 0
pr_gt5           = 0

for chunk in iter_session_tracks():
    # label
    label_counts.update(
        chunk["label"].fillna("<NA>").value_counts().to_dict()
    )

    # playratio
    pr = chunk["playratio"].astype(float, errors="ignore")
    null_mask = pr.isna()
    nonnull   = pr[~null_mask]
    pr_buckets["null"] += int(null_mask.sum())
    buckets = pd.cut(nonnull, bins=PR_BINS, labels=PR_LABELS)
    pr_buckets.update(buckets.value_counts().to_dict())

    # playratio × label cross (vectorized)
    tmp = pd.DataFrame({"pr_bucket": "null", "label": chunk["label"].fillna("<NA>")})
    tmp.loc[~null_mask, "pr_bucket"] = buckets.values
    for bkt, grp in tmp.groupby("pr_bucket", observed=True):
        pr_label_cross[str(bkt)].update(grp["label"].value_counts().to_dict())

    # playratio > 5
    pr_gt5 += int((nonnull > 5.0).sum())

    # playratio sample for describe
    if pr_sampled < PR_SAMPLE_CAP:
        take = min(PR_SAMPLE_CAP - pr_sampled, len(nonnull))
        pr_sample.extend(nonnull.iloc[:take].tolist())
        pr_sampled += take

    # session lengths
    session_len_map.update(chunk["session_id"].value_counts().to_dict())

    # per-user
    for uid, grp in chunk.groupby("user_id"):
        user_event_acc[uid] += len(grp)
        user_session_set[uid].update(grp["session_id"].dropna().astype(int).tolist())
        user_track_set[uid].update(grp["track_id"].dropna().astype(int).tolist())
        if (grp["label"] == "positive").any():
            user_positive.add(uid)

print(f"Done. ({time.time()-t0:.1f}s)")

# ── 2-1 label ─────────────────────────────────────────────────────────────────
subsection("2.1 label value_counts")
total_labels = sum(label_counts.values())
for lbl, cnt in sorted(label_counts.items(), key=lambda x: -x[1]):
    w(f"  {lbl:<12} {fmt(cnt):>15}  {pct(cnt, total_labels):>7}")

# ── 2-2 playratio ─────────────────────────────────────────────────────────────
subsection("2.2 playratio distribution")
pr_arr = np.array(pr_sample, dtype=np.float64)
w(f"  describe (non-null sample, n={fmt(len(pr_arr))}):")
for stat, val in pd.Series(pr_arr).describe().items():
    w(f"    {stat:<8} {val:.4f}")

w("\n  Bucket distribution:")
bucket_order = ["null"] + PR_LABELS
for bkt in bucket_order:
    cnt = pr_buckets.get(bkt, 0)
    w(f"    {bkt:<12} {fmt(cnt):>15}  {pct(cnt, st_nrows):>7}")

w("\n  Cross-analysis: bucket × label")
all_cross_labels = sorted({l for d in pr_label_cross.values() for l in d})
w("  " + f"{'bucket':<12} " + "  ".join(f"{l:<12}" for l in all_cross_labels))
for bkt in bucket_order:
    row_d = pr_label_cross.get(bkt, {})
    row_s = f"  {bkt:<12} " + "  ".join(f"{fmt(row_d.get(l,0)):<12}" for l in all_cross_labels)
    w(row_s)

# ── 2-3 session length ────────────────────────────────────────────────────────
subsection("2.3 Session length distribution")
sess_lens = np.array(list(session_len_map.values()), dtype=np.int64)
w(f"  Total sessions: {fmt(len(sess_lens))}")
w("  describe:")
for stat, val in pd.Series(sess_lens).describe().items():
    w(f"    {stat:<8} {val:.2f}")

len_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, ">50": 0}
for ln in sess_lens:
    if   ln == 1:  len_buckets["1"]     += 1
    elif ln <=  5: len_buckets["2-5"]   += 1
    elif ln <= 10: len_buckets["6-10"]  += 1
    elif ln <= 20: len_buckets["11-20"] += 1
    elif ln <= 50: len_buckets["21-50"] += 1
    else:          len_buckets[">50"]   += 1

w("\n  Bucket distribution:")
for bkt, cnt in len_buckets.items():
    w(f"    {bkt:<8} {fmt(cnt):>12}  {pct(cnt, len(sess_lens)):>7}")
w(f"\n  Length-1 sessions: {fmt(len_buckets['1'])} ({pct(len_buckets['1'], len(sess_lens))})")

fig, ax = plt.subplots(figsize=(9, 5))
ax.hist(sess_lens[sess_lens <= 50], bins=50, color="steelblue", edgecolor="white")
ax.set_xlabel("Session length (tracks, capped at 50)")
ax.set_ylabel("Count")
ax.set_title("Session Length Distribution")
fig.tight_layout()
save_fig("session_length_dist.png")

# ── 2-4 per-user stats ────────────────────────────────────────────────────────
subsection("2.4 Per-user statistics")
u_events   = np.array([user_event_acc[u] for u in user_event_acc], dtype=np.int64)
u_sessions = np.array([len(user_session_set[u]) for u in user_event_acc], dtype=np.int64)
u_tracks   = np.array([len(user_track_set[u])   for u in user_event_acc], dtype=np.int64)

w("  Events per user:")
for stat, val in pd.Series(u_events).describe().items():
    w(f"    {stat:<8} {val:.2f}")
w("  Sessions per user:")
for stat, val in pd.Series(u_sessions).describe().items():
    w(f"    {stat:<8} {val:.2f}")
w("  Unique tracks per user:")
for stat, val in pd.Series(u_tracks).describe().items():
    w(f"    {stat:<8} {val:.2f}")

cold   = int((u_events <  10).sum())
light  = int(((u_events >= 10) & (u_events < 50)).sum())
warm   = int((u_events >= 50).sum())
total_active = len(u_events)
w(f"\n  Activity tiers:")
w(f"    cold  (<10)   {fmt(cold):>10}  {pct(cold,  total_active):>7}")
w(f"    light (10-49) {fmt(light):>10}  {pct(light, total_active):>7}")
w(f"    warm  (>=50)  {fmt(warm):>10}  {pct(warm,  total_active):>7}")

fig, ax = plt.subplots(figsize=(9, 5))
ax.hist(np.log1p(u_events), bins=60, color="seagreen", edgecolor="white")
ax.set_xlabel("log1p(events per user)")
ax.set_ylabel("Count")
ax.set_title("User Activity Distribution (log scale)")
fig.tight_layout()
save_fig("user_activity_dist.png")

# ── 2-5 positive coverage ─────────────────────────────────────────────────────
subsection("2.5 Positive-label user coverage")
w(f"  Users with >=1 positive label: {fmt(len(user_positive))} / {fmt(total_active)} "
  f"({pct(len(user_positive), total_active)})")

# ─────────────────────────────────────────────────────────────────────────────
# PART 3 — Tracks catalog
# ─────────────────────────────────────────────────────────────────────────────
section("PART 3 — TRACKS CATALOG ANALYSIS")

# ── 3-1 duration ──────────────────────────────────────────────────────────────
subsection("3.1 Duration analysis")
dur = tracks_df["duration"]
n_unknown = (dur == -1).sum()
w(f"  duration=-1 (unknown): {fmt(n_unknown)} ({pct(n_unknown, len(tracks_df))})")
valid_dur = dur[dur > 0].astype(float) / 1000.0   # ms → s
w("  Valid duration (seconds) describe:")
for stat, val in valid_dur.describe().items():
    w(f"    {stat:<8} {val:.2f}")

fig, ax = plt.subplots(figsize=(9, 5))
cap = valid_dur[valid_dur <= 600]
ax.hist(cap, bins=60, color="darkorange", edgecolor="white")
ax.set_xlabel("Duration (seconds, capped at 600s)")
ax.set_ylabel("Count")
ax.set_title("Track Duration Distribution")
fig.tight_layout()
save_fig("track_duration_dist.png")

# ── 3-2 playcount ─────────────────────────────────────────────────────────────
subsection("3.2 Playcount analysis")
pc = tracks_df["playcount"].dropna()
w("  describe:")
for stat, val in pc.describe().items():
    w(f"    {stat:<8} {val:.2f}")
w("  Quantiles:")
for q in [0.5, 0.8, 0.9, 0.95, 0.99, 1.0]:
    w(f"    {q:.2f}  ->  {pc.quantile(q):.1f}")

fig, ax = plt.subplots(figsize=(9, 5))
pc_pos = pc[pc > 0]
ax.hist(np.log1p(pc_pos.values), bins=60, color="mediumpurple", edgecolor="white")
ax.set_xlabel("log1p(playcount)  [tracks with playcount>0]")
ax.set_ylabel("Count")
ax.set_title("Track Playcount Distribution (log scale)")
fig.tight_layout()
save_fig("track_playcount_dist.png")

# ── 3-3 tag_ids ────────────────────────────────────────────────────────────────
subsection("3.3 tag_ids analysis")
print("Parsing tag_ids in tracks.csv...")
t0 = time.time()
tags_parsed = tracks_df["tag_ids"].apply(safe_literal)
print(f"Done. ({time.time()-t0:.1f}s)")

tag_cnt_per_track = tags_parsed.apply(len)
no_tag = (tag_cnt_per_track == 0).sum()
w(f"  Tracks with no tags: {fmt(no_tag)} ({pct(no_tag, len(tracks_df))})")

w("  Tags-per-track describe:")
for stat, val in tag_cnt_per_track.describe().items():
    w(f"    {stat:<8} {val:.2f}")

tc_bkt = {"0": 0, "1": 0, "2-5": 0, "6-10": 0, ">10": 0}
for n in tag_cnt_per_track:
    if   n ==  0: tc_bkt["0"]   += 1
    elif n ==  1: tc_bkt["1"]   += 1
    elif n <=  5: tc_bkt["2-5"] += 1
    elif n <= 10: tc_bkt["6-10"]+= 1
    else:         tc_bkt[">10"] += 1
w("  Tag-count buckets:")
for bkt, cnt in tc_bkt.items():
    w(f"    {bkt:<6} {fmt(cnt):>12}  {pct(cnt, len(tracks_df)):>7}")

global_tag_freq = Counter()
for lst in tags_parsed:
    global_tag_freq.update(lst)
n_unique_tags = len(global_tag_freq)
w(f"\n  Unique tags in use: {fmt(n_unique_tags)}")
for thr in [1, 10, 50, 100, 500]:
    cnt = sum(1 for v in global_tag_freq.values() if v >= thr)
    w(f"  Tags in >= {thr:>4} tracks: {fmt(cnt)}")

tag_id_to_name = dict(zip(tags_df["tag_id"], tags_df["value"]))
w("\n  Top-20 tags by track frequency:")
w(f"  {'rank':<5} {'tag_id':<10} {'name':<35} count")
for rank, (tid, cnt) in enumerate(global_tag_freq.most_common(20), 1):
    name = tag_id_to_name.get(tid, "<unknown>")
    w(f"  {rank:<5} {tid:<10} {str(name):<35} {fmt(cnt)}")

fig, ax = plt.subplots(figsize=(9, 5))
ax.hist(tag_cnt_per_track[tag_cnt_per_track <= 20], bins=21, color="teal", edgecolor="white")
ax.set_xlabel("Tags per track (capped at 20)")
ax.set_ylabel("Count")
ax.set_title("Track Tag-Count Distribution")
fig.tight_layout()
save_fig("track_tag_count_dist.png")

# ── 3-4 artist_ids ────────────────────────────────────────────────────────────
subsection("3.4 artist_ids — multi-artist tracks")
print("Parsing artist_ids in tracks.csv...")
t0 = time.time()
artist_cnt = tracks_df["artist_ids"].apply(safe_literal).apply(len)
print(f"Done. ({time.time()-t0:.1f}s)")
multi = (artist_cnt > 1).sum()
w(f"  Tracks with >1 artist: {fmt(multi)} ({pct(multi, len(tracks_df))})")

# ─────────────────────────────────────────────────────────────────────────────
# PART 4 — User behaviour
# ─────────────────────────────────────────────────────────────────────────────
section("PART 4 — USER BEHAVIOUR ANALYSIS")

# ── 4-1 love ──────────────────────────────────────────────────────────────────
subsection("4.1 love.csv analysis")
love_per_user = love_df.groupby("user_id").size()
w("  Love events per user:")
for stat, val in love_per_user.describe().items():
    w(f"    {stat:<8} {val:.2f}")
uwl = love_per_user.index.nunique()
w(f"\n  Users with >=1 love: {fmt(uwl)} / {fmt(len(users_df))} ({pct(uwl, len(users_df))})")
orphan_love = set(love_df["track_id"].dropna().astype(int)) - track_id_set
w(f"  Love track_ids not in tracks.csv: {fmt(len(orphan_love))}")

# ── 4-2 playlists ─────────────────────────────────────────────────────────────
subsection("4.2 Playlist analysis")
pl_len = playlist_meta["num_tracks"].dropna()
w("  Playlist length (num_tracks) describe:")
for stat, val in pl_len.describe().items():
    w(f"    {stat:<8} {val:.2f}")
uwp = playlist_meta["user_id"].nunique()
w(f"\n  Users with >=1 playlist: {fmt(uwp)} / {fmt(len(users_df))} ({pct(uwp, len(users_df))})")

fig, ax = plt.subplots(figsize=(9, 5))
ax.hist(pl_len[pl_len <= 100], bins=50, color="crimson", edgecolor="white")
ax.set_xlabel("Playlist length (capped at 100 tracks)")
ax.set_ylabel("Count")
ax.set_title("Playlist Length Distribution")
fig.tight_layout()
save_fig("playlist_length_dist.png")

# ─────────────────────────────────────────────────────────────────────────────
# PART 5 — Model-design checks
# ─────────────────────────────────────────────────────────────────────────────
section("PART 5 — MODEL-DESIGN CHECKS")

# ── 5-1 ALS matrix density ────────────────────────────────────────────────────
subsection("5.1 ALS interaction matrix density")
print("Collecting positive (user, track) pairs for ALS density...")
t0 = time.time()
pos_user_ids = []
pos_track_ids = []
for chunk in iter_session_tracks(usecols=["user_id", "track_id", "label"]):
    pos = chunk[chunk["label"] == "positive"][["user_id", "track_id"]].dropna()
    pos_user_ids.extend(pos["user_id"].astype(int).tolist())
    pos_track_ids.extend(pos["track_id"].astype(int).tolist())
print(f"Done. ({time.time()-t0:.1f}s)")

pos_pairs = set(zip(pos_user_ids, pos_track_ids))
n_u_als   = len({p[0] for p in pos_pairs})
n_t_als   = len({p[1] for p in pos_pairs})
density_pos = len(pos_pairs) / (n_u_als * n_t_als) if n_u_als and n_t_als else 0
mem_gb = n_u_als * n_t_als * 4 / 1e9

w(f"  Positive-only matrix:")
w(f"    Users:          {fmt(n_u_als)}")
w(f"    Tracks:         {fmt(n_t_als)}")
w(f"    Interactions:   {fmt(len(pos_pairs))}")
w(f"    Density:        {density_pos:.6%}")
w(f"    Float32 memory: {mem_gb:.2f} GB")

love_pairs = set(zip(love_df["user_id"].astype(int), love_df["track_id"].astype(int)))
combined   = pos_pairs | love_pairs
n_u_comb   = len({p[0] for p in combined})
n_t_comb   = len({p[1] for p in combined})
density_comb = len(combined) / (n_u_comb * n_t_comb) if n_u_comb and n_t_comb else 0
w(f"\n  Positive + Love matrix:")
w(f"    Users:          {fmt(n_u_comb)}")
w(f"    Tracks:         {fmt(n_t_comb)}")
w(f"    Interactions:   {fmt(len(combined))}")
w(f"    Density:        {density_comb:.6%}")

# ── 5-2 co-occurrence signal ──────────────────────────────────────────────────
subsection("5.2 Co-occurrence signal (adjacent positive/neutral pairs)")
print("Computing co-occurrence signal from session_tracks.csv...")
t0 = time.time()
cooc_count = 0
prev_tail  = {}   # session_id -> last (position, label)

for chunk in iter_session_tracks(usecols=["session_id", "position", "label"]):
    valid = chunk[chunk["label"].isin(["positive", "neutral"])].copy()
    valid_sorted = valid.sort_values(["session_id", "position"])

    for sid, grp in valid_sorted.groupby("session_id", sort=False):
        pos_arr = grp["position"].values
        diffs   = np.diff(pos_arr)
        cooc_count += int((diffs == 1).sum())
        # cross-chunk boundary
        if sid in prev_tail:
            prev_pos, _ = prev_tail[sid]
            if len(pos_arr) > 0 and pos_arr[0] - prev_pos == 1:
                cooc_count += 1
        last_idx = grp["position"].idxmax()
        prev_tail[sid] = (grp.loc[last_idx, "position"], grp.loc[last_idx, "label"])

print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  Adjacent (positive/neutral) track pairs: {fmt(cooc_count)}")

# ── 5-3 ranker training size ───────────────────────────────────────────────────
subsection("5.3 Ranker training data estimate")
n_sess_ge2 = sum(1 for v in session_len_map.values() if v >= 2)
total_ctx  = sum(v - 1 for v in session_len_map.values() if v >= 2)
CANDS      = 6
w(f"  Usable sessions (length >= 2):         {fmt(n_sess_ge2)}")
w(f"  Total training contexts:               {fmt(total_ctx)}")
w(f"  Estimated training rows (x{CANDS} cands): {fmt(total_ctx * CANDS)}")

# ── 5-4 tag SVD quality ────────────────────────────────────────────────────────
subsection("5.4 Tag SVD quality pre-check")
MIN_TRACKS = 50
valid_tags = {tid for tid, cnt in global_tag_freq.items() if cnt >= MIN_TRACKS}
w(f"  Tags with >= {MIN_TRACKS} tracks: {fmt(len(valid_tags))}")
covered = tags_parsed.apply(lambda lst: any(t in valid_tags for t in lst)).sum()
coverage = covered / len(tracks_df)
w(f"  Tracks with >= 1 valid tag: {fmt(covered)} ({coverage:.1%})")
if coverage < 0.5:
    w("  [WARNING] Tag coverage < 50% — SVD quality may be poor")

# ─────────────────────────────────────────────────────────────────────────────
# PART 6 — Data quality anomalies
# ─────────────────────────────────────────────────────────────────────────────
section("PART 6 — DATA QUALITY ANOMALY DETECTION")

# 6-1 playratio > 5
subsection("6.1 playratio > 5.0")
w(f"  Rows with playratio > 5.0: {fmt(pr_gt5)}")

# 6-2 duplicate (session_id, position)
subsection("6.2 Duplicate (session_id, position) pairs")
print("Checking duplicate (session_id, position)...")
t0 = time.time()
# accumulate pair counts
pair_cnt = Counter()
for chunk in iter_session_tracks(usecols=["session_id", "position"]):
    pairs = list(zip(chunk["session_id"].tolist(), chunk["position"].tolist()))
    pair_cnt.update(pairs)
dup_pairs = sum(1 for v in pair_cnt.values() if v > 1)
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  Duplicate (session_id, position) pairs: {fmt(dup_pairs)}")

# 6-3 playcount = 0
subsection("6.3 tracks.playcount = 0")
pc0 = (tracks_df["playcount"] == 0).sum()
w(f"  Tracks with playcount=0: {fmt(pc0)}")

# 6-4 track repeats >3 within session
subsection("6.4 Tracks appearing > 3 times in a single session")
print("Counting track repeats within sessions...")
t0 = time.time()
# Use groupby count approach — accumulate per (session_id, track_id)
stc = Counter()
for chunk in iter_session_tracks(usecols=["session_id", "track_id"]):
    for (sid, tid), g in chunk.groupby(["session_id", "track_id"]):
        stc[(sid, tid)] += len(g)
repeat_cases = sum(1 for v in stc.values() if v > 3)
print(f"Done. ({time.time()-t0:.1f}s)")
w(f"  (session, track) pairs appearing > 3 times: {fmt(repeat_cases)}")

# 6-5 session_meta.num_tracks consistency
subsection("6.5 session_meta.num_tracks vs actual track count")
sl_series = pd.Series(session_len_map, name="actual")
sm_series = session_meta.set_index("session_id")["num_tracks"]
merged    = pd.concat([sm_series, sl_series], axis=1)
merged.columns = ["meta_num", "actual"]
diff = (merged["meta_num"] - merged["actual"]).dropna()
w("  Difference (meta_num - actual) describe:")
for stat, val in diff.describe().items():
    w(f"    {stat:<8} {val:.2f}")
n_mismatch = (diff != 0).sum()
w(f"  Sessions with mismatch: {fmt(int(n_mismatch))} / {fmt(len(diff))} "
  f"({pct(n_mismatch, len(diff))})")

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
section("SUMMARY")

w("\n[Dataset Scale — Key Numbers]")
w(f"  Tracks:               {fmt(len(tracks_df))}")
w(f"  Users:                {fmt(len(users_df))}")
w(f"  Sessions:             {fmt(len(session_meta))}")
w(f"  Session events:       {fmt(st_nrows)}")
w(f"  Love events:          {fmt(len(love_df))}")
w(f"  Playlists:            {fmt(len(playlist_meta))}")
w(f"  Tags (unique):        {fmt(len(tags_df))}")

w("\n[Data Quality Issues]")
if orphan_tids:
    w(f"  [WARNING] {fmt(len(orphan_tids))} orphan track_ids in session_tracks")
else:
    w(f"  [INFO]    No orphan track_ids in session_tracks")
if orphan_uids:
    w(f"  [WARNING] {fmt(len(orphan_uids))} orphan user_ids in session_tracks")
else:
    w(f"  [INFO]    No orphan user_ids in session_tracks")
if orphan_plids:
    w(f"  [WARNING] {fmt(len(orphan_plids))} orphan playlist_ids in playlist_tracks")
else:
    w(f"  [INFO]    No orphan playlist_ids in playlist_tracks")
w(f"  [INFO]    playratio null rows: {fmt(pr_buckets.get('null', 0))} ({pct(pr_buckets.get('null',0), st_nrows)})")
if pr_gt5 > 0:
    w(f"  [WARNING] {fmt(pr_gt5)} rows with playratio > 5.0 (likely errors)")
if dup_pairs > 0:
    w(f"  [WARNING] {fmt(dup_pairs)} duplicate (session_id, position) pairs")
else:
    w(f"  [INFO]    No duplicate (session_id, position) pairs")
if pc0 > 0:
    w(f"  [WARNING] {fmt(pc0)} tracks with playcount=0")
if repeat_cases > 0:
    w(f"  [INFO]    {fmt(repeat_cases)} (session,track) pairs with >3 appearances in same session")
if n_mismatch > 0:
    w(f"  [WARNING] {fmt(int(n_mismatch))} sessions with session_meta.num_tracks != actual count "
      f"({pct(n_mismatch, len(diff))})")
if coverage < 0.5:
    w(f"  [WARNING] Tag SVD coverage only {coverage:.1%} — below 50% threshold")

w("\n[Model-Design Key Conclusions]")
w(f"  User activity tiers (n={fmt(total_active)} active users in session_tracks):")
w(f"    cold  (<10 events):   {fmt(cold):>10}  {pct(cold,  total_active)}")
w(f"    light (10-49 events): {fmt(light):>10}  {pct(light, total_active)}")
w(f"    warm  (>=50 events):  {fmt(warm):>10}  {pct(warm,  total_active)}")
w(f"  ALS matrix density (positive-only): {density_pos:.6%}")
w(f"  ALS matrix density (positive+love): {density_comb:.6%}")
w(f"  Full float32 matrix memory:         {mem_gb:.2f} GB")
w(f"  Ranker: usable sessions (len>=2):   {fmt(n_sess_ge2)}")
w(f"  Ranker: training contexts:          {fmt(total_ctx)}")
w(f"  Ranker: estimated training rows:    {fmt(total_ctx * CANDS)}")
w(f"  Tag SVD: valid tags (>={MIN_TRACKS} tracks): {fmt(len(valid_tags))}")
w(f"  Tag SVD: track coverage:            {coverage:.1%}")

rep.close()
print(f"\nReport written to {OUT_TXT}")
print(f"Figures saved to  {FIG_DIR}/")
