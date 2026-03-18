"""
30Music idomaar format parser and cleaner.

Handles:
  - 4-column format (no relations JSON): users.idomaar
  - 5-column format (with relations JSON): tracks, albums, persons,
    tags, playlist, events, sessions
  - URL-encoded + /_/ delimited track names
  - Session sequence unpacking into flat rows
  - Outputs one CSV per entity/relation type
"""

import json
import csv
import re
from pathlib import Path
from urllib.parse import unquote_plus


# ─────────────────────────────────────────────
# 1.  Core idomaar line parser
# ─────────────────────────────────────────────

def _split_two_json(raw: str) -> tuple[str, str | None]:
    """
    Given a string that is either:
      (a) a single JSON object:   '{"k":1}'
      (b) two JSON objects separated by a single space:
          '{"k":1} {"subjects":[...]}'   ← sessions / events format

    Returns (first_json_str, second_json_str_or_None).

    Strategy: find the closing brace of the FIRST top-level object by
    counting brace depth, then treat everything after the first space
    that follows as the second JSON.  This avoids regex on arbitrary JSON.
    """
    raw = raw.strip()
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(raw):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                first  = raw[: i + 1]
                rest   = raw[i + 1 :].lstrip()
                second = rest if rest else None
                return first, second

    return raw, None


def parse_idomaar_line(line: str) -> dict | None:
    """
    Parse one raw idomaar line.  Three real format variants exist:

    Variant A - 2 tabs (love.idomaar only):
        type TAB id TAB timestamp<SP>json_attrs<SP>json_links
        parts = [type, id, "timestamp {attrs} {links}"]

    Variant B - 3 tabs (sessions.idomaar, users.idomaar):
        type TAB id TAB timestamp TAB json_attrs<SP>json_links  <- sessions
        type TAB id TAB timestamp TAB json_attrs                <- users (no links)

    Variant C - 4 tabs (everything else):
        type TAB id TAB timestamp TAB json_attrs TAB json_links

    Returns dict(record_type, record_id, timestamp, attrs, links)
    or None on parse failure.
    """
    line = line.rstrip("\n")
    if not line:
        return None

    parts = line.split("\t", maxsplit=4)
    n = len(parts)
    if n < 3:
        return None

    record_type = parts[0]
    record_id   = parts[1]

    if n == 3:
        col2 = parts[2].strip()
        brace_pos = col2.find("{")
        if brace_pos == -1:
            return None
        ts_str    = col2[:brace_pos].strip()
        remainder = col2[brace_pos:]
        raw_attrs, raw_links_inline = _split_two_json(remainder)
    else:
        ts_str = parts[2].strip()
        col3 = parts[3].strip() if n >= 4 else ""
        raw_attrs, raw_links_inline = _split_two_json(col3)
        if n == 5 and parts[4].strip():
            raw_links_inline = parts[4].strip()

    ts_clean = ts_str.lstrip("-")
    timestamp = int(ts_str) if ts_clean.isdigit() else None

    try:
        attrs = json.loads(raw_attrs) if raw_attrs else {}
    except json.JSONDecodeError:
        attrs = {}

    links = {}
    if raw_links_inline:
        try:
            links = json.loads(raw_links_inline)
        except json.JSONDecodeError:
            links = {}

    return {
        "record_type": record_type,
        "record_id":   int(record_id) if record_id.lstrip("-").isdigit() else record_id,
        "timestamp":   timestamp,
        "attrs":       attrs,
        "links":       links,
    }


# ─────────────────────────────────────────────
# 2.  Track-name cleaner
# ─────────────────────────────────────────────

# Pattern: optional_prefix/_/actual_title  OR  artist/_/title
_SLASH_SEP = re.compile(r"^(.*?)/_/(.+)$")

def clean_track_name(raw: str) -> tuple[str, str]:
    """
    Decode URL encoding, replace + with space, split on /_/.

    Returns (artist_hint, title).
    artist_hint may be empty string if the separator is absent.

    Examples
    --------
    "Music+Instructor/_/Dj%27s+Rock" -> ("Music Instructor", "Dj's Rock")
    "%D0%A2%D0%B5%D0%BA%D1%81%D1%82" -> ("", "Текст")
    "Overkill/_/Overkill"            -> ("Overkill", "Overkill")
    """
    decoded = unquote_plus(raw)           # %27 -> '   and   + -> space
    m = _SLASH_SEP.match(decoded)
    if m:
        artist_hint = m.group(1).strip()
        title       = m.group(2).strip()
    else:
        artist_hint = ""
        title       = decoded.strip()
    return artist_hint, title


def _as_link_dict(node) -> dict:
    """
    Normalize heterogeneous relation payloads to a dict.
    Some dumps contain nested list wrappers around relation objects.
    """
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                return item
    return {}


def _extract_link_id(node):
    """
    Safely extract `id` from relation nodes that may be dict/list/scalar.
    """
    if isinstance(node, dict):
        return node.get("id")
    if isinstance(node, list):
        for item in node:
            found = _extract_link_id(item)
            if found is not None:
                return found
        return None
    if isinstance(node, (int, str)):
        return node
    return None


# ─────────────────────────────────────────────
# 3.  Per-file parsers  →  list[dict]
# ─────────────────────────────────────────────

def parse_tracks(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a = r["attrs"]
            raw_name = a.get("name", "")
            artist_hint, title = clean_track_name(raw_name)

            # extract linked entity ids
            lnk      = r["links"]
            artists  = [x["id"] for x in lnk.get("artists") or []]
            albums   = [x["id"] for x in lnk.get("albums") or []]
            tags     = [x["id"] for x in lnk.get("tags") or []]

            rows.append({
                "track_id":    r["record_id"],
                "mbid":        a.get("MBID"),
                "duration":    a.get("duration"),
                "playcount":   a.get("playcount"),
                "raw_name":    raw_name,
                "artist_hint": artist_hint,   # extracted from name field
                "title":       title,
                "artist_ids":  json.dumps(artists),
                "album_ids":   json.dumps(albums),
                "tag_ids":     json.dumps(tags),
            })
    return rows


def parse_persons(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a = r["attrs"]
            rows.append({
                "person_id": r["record_id"],
                "mbid":      a.get("MBID"),
                "name":      unquote_plus(a.get("name", "")),
            })
    return rows


def parse_albums(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a = r["attrs"]
            rows.append({
                "album_id": r["record_id"],
                "mbid":     a.get("MBID"),
                "title":    a.get("title", ""),
            })
    return rows


def parse_tags(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a = r["attrs"]
            rows.append({
                "tag_id": r["record_id"],
                "value":  a.get("value", ""),
                "url":    a.get("url",   ""),
            })
    return rows


def parse_users(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a = r["attrs"]
            rows.append({
                "user_id":        r["record_id"],
                "registered_ts":  r["timestamp"],
                "lastfm_username": a.get("lastfm_username", ""),
                "gender":         a.get("gender", ""),
                "age":            a.get("age"),
                "country":        a.get("country", ""),
                "playcount":      a.get("playcount"),
                "num_playlists":  a.get("playlists"),
                "subscriber_type": a.get("subscribertype", ""),
            })
    return rows


def parse_love(path: Path) -> list[dict]:
    """love.idomaar  ->  (user_id, track_id) explicit preference pairs."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            lnk      = r["links"]
            subjects = lnk.get("subjects") or []
            objects  = lnk.get("objects") or []
            user_id  = _extract_link_id(subjects[0]) if subjects else None
            track_id = _extract_link_id(objects[0])  if objects  else None
            rows.append({
                "pref_id":   r["record_id"],
                "timestamp": r["timestamp"],
                "user_id":   user_id,
                "track_id":  track_id,
                "value":     r["attrs"].get("value", "love"),
            })
    return rows


def parse_playlists(path: Path) -> list[dict]:
    """
    playlist.idomaar  ->  two tables:
        playlist_meta : one row per playlist
        playlist_tracks : one row per (playlist, position, track)
    """
    meta   = []
    tracks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            a        = r["attrs"]
            lnk      = r["links"]
            subjects = lnk.get("subjects") or []
            objects  = lnk.get("objects") or []

            user_id     = _extract_link_id(subjects[0]) if subjects else None
            playlist_id = r["record_id"]

            meta.append({
                "playlist_id":  playlist_id,
                "lastfm_id":    a.get("ID"),
                "title":        a.get("Title", ""),
                "num_tracks":   a.get("numtracks"),
                "duration":     a.get("duration"),
                "timestamp":    r["timestamp"],
                "user_id":      user_id,
            })

            for pos, obj in enumerate(objects):
                track_id = _extract_link_id(obj)
                if track_id is None:
                    continue
                tracks.append({
                    "playlist_id": playlist_id,
                    "user_id":     user_id,
                    "position":    pos,
                    "track_id":    track_id,
                })

    return meta, tracks


def parse_events(path: Path) -> list[dict]:
    """events.idomaar  ->  flat play events."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue
            lnk      = r["links"]
            subjects = lnk.get("subjects") or []
            objects  = lnk.get("objects") or []
            user_id  = _extract_link_id(subjects[0]) if subjects else None
            track_id = _extract_link_id(objects[0])  if objects  else None
            rows.append({
                "event_id":  r["record_id"],
                "timestamp": r["timestamp"],
                "user_id":   user_id,
                "track_id":  track_id,
                "playtime":  r["attrs"].get("playtime"),
            })
    return rows


def parse_sessions(path: Path) -> tuple[list[dict], list[dict]]:
    """
    sessions.idomaar  ->  two tables:

    session_meta
        session_id | user_id | session_ts | num_tracks | total_playtime

    session_tracks  (one row per track inside a session)
        session_id | user_id | position | track_id
        | playstart | playtime | playratio | action
        | label     (derived: 'positive' / 'skip' / 'neutral' / 'unknown')
    """
    meta_rows  = []
    track_rows = []

    with open(path, encoding="utf-8") as f:
        for line in f:
            r = parse_idomaar_line(line)
            if r is None:
                continue

            a        = r["attrs"]
            lnk      = r["links"]
            subjects = lnk.get("subjects") or []
            objects  = lnk.get("objects") or []   # ordered track list

            user_id    = _extract_link_id(subjects[0]) if subjects else None
            session_id = r["record_id"]

            meta_rows.append({
                "session_id":     session_id,
                "user_id":        user_id,
                "session_ts":     r["timestamp"],
                "num_tracks":     a.get("numtracks"),
                "total_playtime": a.get("playtime"),
            })

            for pos, obj in enumerate(objects):
                obj_dict = _as_link_dict(obj)
                track_id = _extract_link_id(obj)
                if track_id is None:
                    continue

                playratio = obj_dict.get("playratio")
                playtime  = obj_dict.get("playtime", -1)
                action    = obj_dict.get("action")

                # ── Label construction ──────────────────────────────
                # Mirrors your Slide 3.4 definition:
                #   positive  : ratio >= 0.8  OR  action == 'play' + ratio > 0.8
                #   skip      : ratio < 0.3   AND playtime < 30s
                #   neutral   : everything in between
                #   unknown   : ratio is null (can't determine)
                if playratio is None:
                    label = "unknown"
                elif playratio >= 0.8:
                    label = "positive"
                elif playratio < 0.3 and 0 < playtime < 30:
                    label = "skip"
                else:
                    label = "neutral"

                track_rows.append({
                    "session_id": session_id,
                    "user_id":    user_id,
                    "position":   pos,
                    "track_id":   track_id,
                    "playstart":  obj_dict.get("playstart"),
                    "playtime":   playtime,
                    "playratio":  playratio,
                    "action":     action,
                    "label":      label,
                })

    return meta_rows, track_rows


# ─────────────────────────────────────────────
# 4.  CSV writer helper
# ─────────────────────────────────────────────

def write_csv(rows: list[dict], out_path: Path) -> None:
    if not rows:
        print(f"  [skip] {out_path.name}  (0 rows)")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  [ok]   {out_path.name}  ({len(rows):,} rows)")


# ─────────────────────────────────────────────
# 5.  Main pipeline
# ─────────────────────────────────────────────

def main(data_root: str = "../../data/30music",
         out_root:  str = "../../data/30music_parsed") -> None:

    data_dir = Path(data_root)
    out_dir  = Path(out_root)

    print("=== 30Music idomaar parser ===\n")

    # ── entities ──────────────────────────────
    ent = data_dir / "entities"

    print("Tracks:")
    write_csv(
        parse_tracks(ent / "tracks.idomaar"),
        out_dir / "tracks.csv"
    )

    print("Persons (artists):")
    write_csv(
        parse_persons(ent / "persons.idomaar"),
        out_dir / "persons.csv"
    )

    print("Albums:")
    write_csv(
        parse_albums(ent / "albums.idomaar"),
        out_dir / "albums.csv"
    )

    print("Tags:")
    write_csv(
        parse_tags(ent / "tags.idomaar"),
        out_dir / "tags.csv"
    )

    print("Users:")
    write_csv(
        parse_users(ent / "users.idomaar"),
        out_dir / "users.csv"
    )

    # ── playlists ─────────────────────────────
    print("Playlists:")
    pl_meta, pl_tracks = parse_playlists(ent / "playlist.idomaar")
    write_csv(pl_meta,   out_dir / "playlist_meta.csv")
    write_csv(pl_tracks, out_dir / "playlist_tracks.csv")

    # ── relations ─────────────────────────────
    rel = data_dir / "relations"

    print("Love (explicit preferences):")
    write_csv(
        parse_love(rel / "love.idomaar"),
        out_dir / "love.csv"
    )

    print("Events (raw play events):")
    write_csv(
        parse_events(rel / "events.idomaar"),
        out_dir / "events.csv"
    )

    print("Sessions:")
    sess_meta, sess_tracks = parse_sessions(rel / "sessions.idomaar")
    write_csv(sess_meta,   out_dir / "session_meta.csv")
    write_csv(sess_tracks, out_dir / "session_tracks.csv")

    print("\nDone. Output →", out_dir.resolve())


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        main()
