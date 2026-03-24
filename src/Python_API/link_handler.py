import os
import re
import time
import random
import asyncio
import shutil
from typing import Iterable

from json_loader import load_db,save_db

import yt_dlp
from yt_dlp.utils import DownloadError

def safe_filename(name: str, max_len: int = 160) -> str:
    # remove path separators and other problematic characters
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", name).strip()
    return name[:max_len] if len(name) > max_len else name

def _rank_format(f: dict) -> tuple:
    w = f.get("width") or 0
    h = f.get("height") or 0
    pixels = w * h  # primary: biggest resolution overall
    tbr = f.get("tbr") or 0
    pref = f.get("preference") or 0
    return (pixels, h, tbr, pref)

def _passes_rule(f: dict, min_edge: int = 1080) -> bool:
    w = f.get("width")
    h = f.get("height")
    if not (w and h):
        return True  # keep unknown dims (or return False if you want strict)

    if w == h:
        return True  # 1:1 => whatever
    return min(w, h) >= min_edge  # non-1:1 => shortest edge must be >= 720

def _build_candidates(info: dict, max_candidates: int = 12) -> list[str]:
    formats = info.get("formats") or []
    videos = [f for f in formats if f.get("vcodec") not in (None, "none")]
    videos = [v for v in videos if _passes_rule(v, 720)]
    audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]

    videos.sort(key=_rank_format, reverse=True)
    audios.sort(key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0, f.get("preference") or 0), reverse=True)

    candidates = []

    # merged combos: v_id + a_id
    for v in videos[:max_candidates]:
        for a in audios[:3]:
            if v.get("format_id") and a.get("format_id"):
                candidates.append(f"{v['format_id']}+{a['format_id']}")

    # single-file fallbacks (video that already has audio)
    singles = [f for f in videos if f.get("acodec") not in (None, "none")]
    singles.sort(key=_rank_format, reverse=True)
    for s in singles[:max_candidates]:
        if s.get("format_id"):
            candidates.append(s["format_id"])

    # de-dup
    out, seen = [], set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:max_candidates]

def _download_with_retries_sync(url: str, ydl_opts: dict, attempts: int = 8) -> bool:
    for k in range(attempts):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                rc = ydl.download([url])
            if rc == 0:
                return True
        except DownloadError:
            pass

        # exponential backoff + jitter
        time.sleep(min(2 ** k, 10) + random.random())
    return False

def download_asset_sync(url: str, title: str) -> bool:
    os.makedirs("music", exist_ok=True)

    # unique TEMP dir per download (does not affect final filename)
    tmp_root = os.path.join("music", ".tmp")
    os.makedirs(tmp_root, exist_ok=True)
    tmp_dir = os.path.join(tmp_root, f"job_{random.randrange(1<<32):08x}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        candidates = _build_candidates(info, max_candidates=12)
        if not candidates:
            return False

        base_opts = {
            # final file naming (title + author/uploader)
            "paths": {"home": "music", "temp": tmp_dir},  # <— intermediates go here
            "outtmpl": "%(title)s - %(uploader)s.%(ext)s",

            "skip_unavailable_fragments": False,

            "retries": 15,
            "fragment_retries": 200,
            "extractor_retries": 10,
            "socket_timeout": 30,

            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,

            "nopart": True,
            "continuedl": False,

            "concurrent_fragment_downloads": 1,
            "sleep_interval_requests": 0.2,
        }

        for fmt in candidates:
            ydl_opts = dict(base_opts)
            ydl_opts["format"] = fmt
            if _download_with_retries_sync(url, ydl_opts, attempts=4):
                return True

        return False

    finally:
        # always delete temp workspace (fragments, .temp.* etc.)
        shutil.rmtree(tmp_dir, ignore_errors=True)

async def download_asset(url: str, title: str) -> bool:
    # run blocking yt-dlp in a worker thread
    return await asyncio.to_thread(download_asset_sync, url, title)

async def download_all(assets: Iterable[tuple[str, str]], limit: int = 10) -> list[bool]:
    sem = asyncio.Semaphore(limit)

    async def worker(url: str, title: str) -> bool:
        async with sem:
            return await download_asset(url, title)

    tasks = [asyncio.create_task(worker(url, title)) for url, title in assets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # turn exceptions into False
    out = []
    for r in results:
        out.append(False if isinstance(r, Exception) else bool(r))
    return out

def download_music(url,db,playlist_start=0,playlist_end=10,enable_playlist=False,save_playlist=True):
    ydl_opts = {"noplaylist":not enable_playlist,"playliststart":playlist_start,"playlistend":playlist_end}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False,)
        info =  ydl.sanitize_info(info)
        titles = []
        if enable_playlist and "entries" in info.keys():
            for entry in info["entries"]:
                titles.append({"title":entry["title"],"url":entry["webpage_url"]})
        else:
            titles.append({"title":info["title"],"url":info["webpage_url"]})
    
    if save_playlist and enable_playlist:
        music = []
        for music in info["entries"]:
            music.append(music["title"])
        db["playlist"] = {"playlist_name":info["title"],"music": music}
        save_db(db)
    download_list = []
    for title in titles:
        entry_name = f"{title['title']} - {info['uploader']}"
        if entry_name in db["music"]:
            continue
        else:
            download_list.append((title["url"], title["title"], entry_name))
    if download_list:
        results = asyncio.run(download_all(
            [(url, t) for url, t, _ in download_list], limit=20
        ))
        saved = 0
        for (_, _, entry_name), ok in zip(download_list, results):
            if ok:
                db["music"].append(entry_name)
                saved += 1
            else:
                print(f"Download failed: {entry_name}")
        if saved:
            save_db(db)
            print(f"Downloaded {saved} music")
        else:
            print("All downloads failed")
    else:
        print("No new music to download")
    return titles

URL = 'https://fr.pornhub.com/view_video.php?viewkey=69886e3ca8bc0'

db = load_db()

add_music(URL,db,enable_playlist=False)
