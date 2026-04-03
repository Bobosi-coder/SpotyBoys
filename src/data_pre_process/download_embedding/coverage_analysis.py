"""
Coverage Analysis — Stage 4 完成后运行

1. catalog 中成功 embedding 的比例
2. session_tracks / playlist_tracks 中 track_id 被覆盖的比例
3. 缺失 embedding 对数据集的影响评估
"""
import json
import os

import numpy as np
import pandas as pd

PROCESSED   = "data/processed"
RAW_DIR     = "data/raw/content/30music_parsed"
EMBED_DIR   = "/Volumes/T7/MLOps_music_embedding"


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def main():
    # ── 1. Catalog 覆盖率 ──────────────────────────────────────────
    section("1. Catalog 覆盖率（Stage 4 成功率）")

    catalog   = pd.read_csv(os.path.join(PROCESSED, "catalog.csv"))
    manifest  = pd.read_csv(os.path.join(PROCESSED, "download_manifest.csv"))
    manifest["track_id"] = manifest["track_id"].astype(int)

    n_catalog = len(catalog)

    dl_ok   = (manifest["download_status"] == "ok").sum()
    dl_fail = (manifest["download_status"] == "failed").sum()
    dl_pend = (manifest["download_status"] == "pending").sum()

    emb_ok   = (manifest["embed_status"] == "ok").sum()
    emb_fail = (manifest["embed_status"] == "failed").sum()

    print(f"catalog 总条目：        {n_catalog:>10,}")
    print(f"下载成功：              {dl_ok:>10,}  ({dl_ok/n_catalog:.1%})")
    print(f"下载失败：              {dl_fail:>10,}  ({dl_fail/n_catalog:.1%})")
    print(f"仍 pending：            {dl_pend:>10,}  ({dl_pend/n_catalog:.1%})")
    print(f"Embedding 成功：        {emb_ok:>10,}  ({emb_ok/n_catalog:.1%})")
    print(f"Embedding 失败（已dl）：{emb_fail:>10,}  ({emb_fail/n_catalog:.1%})")

    audio_ids = set(manifest[manifest["embed_status"] == "ok"]["track_id"])

    # ── 2. Session_tracks 覆盖率 ───────────────────────────────────
    section("2. Session_tracks 覆盖率")

    st = pd.read_csv(
        os.path.join(RAW_DIR, "session_tracks.csv"),
        usecols=["session_id", "track_id", "label"],
        low_memory=False,
    )
    # 基础去重（主键 session_id, position 不在这里，但 track 级统计够用）
    st["track_id"] = pd.to_numeric(st["track_id"], errors="coerce")
    st = st.dropna(subset=["track_id"])
    st["track_id"] = st["track_id"].astype(int)
    # 排除 unknown label（与 stage5 保持一致）
    st_known = st[st["label"] != "unknown"]

    n_st_rows      = len(st_known)
    n_st_covered   = st_known["track_id"].isin(audio_ids).sum()
    n_st_uncovered = n_st_rows - n_st_covered

    # session 级：有多少 session 全部 track 都有 embedding
    st_known = st_known.copy()
    st_known["has_emb"] = st_known["track_id"].isin(audio_ids)
    session_coverage = st_known.groupby("session_id")["has_emb"].mean()
    full_sessions    = (session_coverage == 1.0).sum()
    partial_sessions = ((session_coverage > 0) & (session_coverage < 1.0)).sum()
    zero_sessions    = (session_coverage == 0.0).sum()
    total_sessions   = len(session_coverage)

    # 至少有 2 个 covered track 的 session（可用于序列模型）
    st_known_emb = st_known[st_known["has_emb"]]
    usable_sessions = st_known_emb.groupby("session_id").size()
    usable_sessions = (usable_sessions >= 2).sum()

    print(f"session_tracks 行数（known label）：{n_st_rows:>10,}")
    print(f"  有 embedding：                    {n_st_covered:>10,}  ({n_st_covered/n_st_rows:.1%})")
    print(f"  无 embedding：                    {n_st_uncovered:>10,}  ({n_st_uncovered/n_st_rows:.1%})")
    print()
    print(f"Session 数量：                      {total_sessions:>10,}")
    print(f"  全覆盖 session（100% tracks有emb）：{full_sessions:>10,}  ({full_sessions/total_sessions:.1%})")
    print(f"  部分覆盖 session：                {partial_sessions:>10,}  ({partial_sessions/total_sessions:.1%})")
    print(f"  零覆盖 session：                  {zero_sessions:>10,}  ({zero_sessions/total_sessions:.1%})")
    print(f"  ≥2 个有embedding的session（可用）：{usable_sessions:>10,}  ({usable_sessions/total_sessions:.1%})")

    # ── 3. Playlist_tracks 覆盖率 ──────────────────────────────────
    section("3. Playlist_tracks 覆盖率")

    pt = pd.read_csv(
        os.path.join(RAW_DIR, "playlist_tracks.csv"),
        usecols=["playlist_id", "track_id"],
        low_memory=False,
    )
    pt["track_id"] = pd.to_numeric(pt["track_id"], errors="coerce")
    pt = pt.dropna(subset=["track_id"])
    pt["track_id"] = pt["track_id"].astype(int)

    n_pt_rows      = len(pt)
    n_pt_covered   = pt["track_id"].isin(audio_ids).sum()
    n_pt_uncovered = n_pt_rows - n_pt_covered

    pt["has_emb"] = pt["track_id"].isin(audio_ids)
    pl_coverage   = pt.groupby("playlist_id")["has_emb"].mean()
    full_pl       = (pl_coverage == 1.0).sum()
    partial_pl    = ((pl_coverage > 0) & (pl_coverage < 1.0)).sum()
    zero_pl       = (pl_coverage == 0.0).sum()
    total_pl      = len(pl_coverage)

    pt_emb = pt[pt["has_emb"]]
    usable_pl = pt_emb.groupby("playlist_id").size()
    usable_pl = (usable_pl >= 2).sum()

    print(f"playlist_tracks 行数：              {n_pt_rows:>10,}")
    print(f"  有 embedding：                    {n_pt_covered:>10,}  ({n_pt_covered/n_pt_rows:.1%})")
    print(f"  无 embedding：                    {n_pt_uncovered:>10,}  ({n_pt_uncovered/n_pt_rows:.1%})")
    print()
    print(f"Playlist 数量：                     {total_pl:>10,}")
    print(f"  全覆盖 playlist：                 {full_pl:>10,}  ({full_pl/total_pl:.1%})")
    print(f"  部分覆盖 playlist：               {partial_pl:>10,}  ({partial_pl/total_pl:.1%})")
    print(f"  零覆盖 playlist：                 {zero_pl:>10,}  ({zero_pl/total_pl:.1%})")
    print(f"  ≥2 个有embedding的playlist（可用）：{usable_pl:>10,}  ({usable_pl/total_pl:.1%})")

    # ── 4. 覆盖率分布直方图（文字版）──────────────────────────────
    section("4. Session/Playlist 覆盖率分布")

    for name, cov in [("Session", session_coverage), ("Playlist", pl_coverage)]:
        bins  = [0, 0.25, 0.5, 0.75, 1.0]
        labels = ["0-25%", "25-50%", "50-75%", "75-100%", "100%"]
        counts = [
            (cov < 0.25).sum(),
            ((cov >= 0.25) & (cov < 0.5)).sum(),
            ((cov >= 0.5)  & (cov < 0.75)).sum(),
            ((cov >= 0.75) & (cov < 1.0)).sum(),
            (cov == 1.0).sum(),
        ]
        print(f"\n{name} 覆盖率分布：")
        for label, cnt in zip(labels, counts):
            bar = "█" * int(cnt / len(cov) * 40)
            print(f"  {label:>8}：{cnt:>8,} ({cnt/len(cov):>5.1%})  {bar}")

    # ── 5. 总结与建议 ──────────────────────────────────────────────
    section("5. 总结")

    print(f"""
Embedding 覆盖率：   {emb_ok/n_catalog:.1%}  ({emb_ok:,} / {n_catalog:,} tracks)

Session 可用性：
  - 行级覆盖：       {n_st_covered/n_st_rows:.1%}  的交互记录有 embedding
  - ≥2条可用session：{usable_sessions:,}  ({usable_sessions/total_sessions:.1%})

Playlist 可用性：
  - 行级覆盖：       {n_pt_covered/n_pt_rows:.1%}  的 playlist 条目有 embedding
  - ≥2条可用playlist：{usable_pl:,}  ({usable_pl/total_pl:.1%})

建议：
  - 若 embedding 覆盖率 >= 40%：可作为内容特征辅助 CF，效果值得期待
  - 若 embedding 覆盖率 20-40%：建议仅作为补充特征，主力用协同过滤
  - 若 embedding 覆盖率 < 20%：覆盖率过低，建议放弃 audio embedding 路线
    """)


if __name__ == "__main__":
    main()
