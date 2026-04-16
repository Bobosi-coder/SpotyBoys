"""
Music Preview Downloader
========================
Downloads 30s preview MP3s from Deezer for the top-N tracks by popularity.
Files are saved as /output/{track_id}.mp3.

Resumable: restarts pick up where they left off by scanning /output/.
Runs until TARGET successful downloads, or all catalog tracks are exhausted.

Environment variables:
  AWS_ACCESS_KEY_ID       S3 credentials
  AWS_SECRET_ACCESS_KEY
  S3_ENDPOINT             e.g. https://chi.tacc.chameleoncloud.org:7480
  S3_BUCKET               default: proj23-mlflow-artifacts
  TARGET                  default: 130000
  WORKERS                 default: 5
  OUTPUT_DIR              default: /output
"""

import asyncio
import csv
import io
import logging
import os
import time
from pathlib import Path

import aiofiles
import aiohttp
import boto3
import pandas as pd
from tqdm import tqdm

# ── Config ────────────────────────────────────────────────────────────────────
S3_ENDPOINT  = os.environ["S3_ENDPOINT"]
S3_BUCKET    = os.environ.get("S3_BUCKET", "proj23-mlflow-artifacts")
TARGET       = int(os.environ.get("TARGET", 130_000))
WORKERS      = int(os.environ.get("WORKERS", 5))
OUTPUT_DIR   = Path(os.environ.get("OUTPUT_DIR", "/output"))
MANIFEST     = OUTPUT_DIR / "manifest.csv"

DEEZER_SEARCH = "https://api.deezer.com/search"
TIMEOUT       = aiohttp.ClientTimeout(total=15)
MAX_RETRIES   = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("downloader")


# ── S3 helpers ────────────────────────────────────────────────────────────────

def s3_download_csv(key: str) -> pd.DataFrame:
    log.info(f"Downloading s3://{S3_BUCKET}/{key} ...")
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        verify=False,
    )
    buf = io.BytesIO()
    s3.download_fileobj(S3_BUCKET, key, buf)
    buf.seek(0)
    return pd.read_csv(buf)


# ── Build ordered track list ──────────────────────────────────────────────────

def build_track_list() -> list[dict]:
    catalog   = s3_download_csv("downloader/item2vec_catalog.csv")
    pop       = s3_download_csv("downloader/pop_scores.csv")

    # catalog: track_id, artist_hint, title
    # pop:     track_id, pop_score  (or log_pop_score — use whatever column exists)
    pop_col = [c for c in pop.columns if "score" in c.lower() or "pop" in c.lower()][-1]
    log.info(f"  catalog rows: {len(catalog):,}  pop rows: {len(pop):,}  score col: {pop_col}")

    merged = catalog.merge(pop[["track_id", pop_col]], on="track_id", how="left")
    merged = merged.fillna({pop_col: 0})
    merged = merged.sort_values(pop_col, ascending=False).reset_index(drop=True)

    return merged[["track_id", "artist_hint", "title"]].to_dict("records")


# ── Deezer search + download ──────────────────────────────────────────────────

async def fetch_preview_url(
    session: aiohttp.ClientSession,
    artist: str,
    title: str,
) -> str | None:
    """Return Deezer 30s preview URL, or None if not found."""
    query = f'artist:"{artist}" track:"{title}"'
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(
                DEEZER_SEARCH,
                params={"q": query, "limit": 1},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status != 200:
                    return None
                data = await resp.json()
                items = data.get("data", [])
                if items and items[0].get("preview"):
                    return items[0]["preview"]
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await asyncio.sleep(2 ** attempt)
    return None


async def download_mp3(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
) -> bool:
    """Download MP3 to dest via a .tmp file; return True on success."""
    tmp = dest.with_suffix(".tmp")
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return False
            async with aiofiles.open(tmp, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    await f.write(chunk)
        tmp.rename(dest)
        return True
    except Exception:
        if tmp.exists():
            tmp.unlink()
        return False


# ── Manifest ──────────────────────────────────────────────────────────────────

class Manifest:
    def __init__(self, path: Path):
        self._path = path
        self._lock = asyncio.Lock()
        if not path.exists():
            path.write_text("track_id,artist,title,status\n")

    async def write(self, track_id: int, artist: str, title: str, status: str):
        async with self._lock:
            async with aiofiles.open(self._path, "a", newline="") as f:
                row = f"{track_id},{csv.writer(io.StringIO()).writerow([artist, title]) or ''},{status}\n"
                # simple approach: escape commas
                safe_artist = artist.replace(",", " ")
                safe_title  = title.replace(",", " ")
                await f.write(f"{track_id},{safe_artist},{safe_title},{status}\n")


# ── Worker ────────────────────────────────────────────────────────────────────

async def worker(
    name: str,
    queue: asyncio.Queue,
    session: aiohttp.ClientSession,
    manifest: Manifest,
    counter: dict,
    pbar: tqdm,
):
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break

        track_id = item["track_id"]
        artist   = str(item.get("artist_hint") or "")
        title    = str(item.get("title") or "")
        dest     = OUTPUT_DIR / f"{track_id}.mp3"

        # Already downloaded (resuming)
        if dest.exists() and dest.stat().st_size > 0:
            async with asyncio.Lock():
                counter["success"] += 1
            pbar.update(1)
            queue.task_done()
            continue

        status = "fail_no_preview"
        preview_url = await fetch_preview_url(session, artist, title)
        if preview_url:
            ok = await download_mp3(session, preview_url, dest)
            if ok:
                counter["success"] += 1
                status = "success"
                pbar.update(1)
            else:
                counter["fail"] += 1
                status = "fail_download"
        else:
            counter["fail"] += 1

        await manifest.write(track_id, artist, title, status)
        queue.task_done()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Count already downloaded files
    existing = {int(p.stem) for p in OUTPUT_DIR.glob("*.mp3") if p.stem.isdigit()}
    log.info(f"Already downloaded: {len(existing):,} files")

    tracks = build_track_list()
    log.info(f"Catalog size: {len(tracks):,} tracks  |  Target: {TARGET:,}")

    manifest = Manifest(MANIFEST)
    counter  = {"success": len(existing), "fail": 0}

    # Build queue: only tracks not yet downloaded, stop queuing when target is met
    queue: asyncio.Queue = asyncio.Queue(maxsize=WORKERS * 4)

    pbar = tqdm(
        total=TARGET,
        initial=len(existing),
        desc="Downloaded",
        unit="track",
        dynamic_ncols=True,
    )

    async with aiohttp.ClientSession() as session:
        # Start workers
        tasks = [
            asyncio.create_task(worker(f"w{i}", queue, session, manifest, counter, pbar))
            for i in range(WORKERS)
        ]

        # Feed queue
        iterated = 0
        for track in tracks:
            if counter["success"] >= TARGET:
                break
            if int(track["track_id"]) in existing:
                continue
            await queue.put(track)
            iterated += 1

            if iterated % 500 == 0:
                log.info(
                    f"  iterated={iterated:,}  success={counter['success']:,}  "
                    f"fail={counter['fail']:,}"
                )

        # Signal workers to stop
        for _ in range(WORKERS):
            await queue.put(None)

        await asyncio.gather(*tasks)

    pbar.close()
    log.info("=" * 60)
    log.info(f"Done.  success={counter['success']:,}  fail={counter['fail']:,}  "
             f"iterated={iterated:,} / {len(tracks):,}")
    if counter["success"] < TARGET:
        log.warning(
            f"Only {counter['success']:,} previews found after exhausting "
            f"{iterated:,} catalog tracks (target was {TARGET:,})"
        )


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    asyncio.run(main())
