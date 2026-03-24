import os
import re
import time
import random
import asyncio
import shutil
from typing import Iterable
import json

# IDK WHY ANTIGRAVITY DO THIS BUT I FIX IT
if "SSLKEYLOGFILE" in os.environ:
    del os.environ["SSLKEYLOGFILE"]

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
        except DownloadError as e:
            err_msg = str(e).lower()
            if "requested format is not available" in err_msg or "http error 404" in err_msg or "http error 403" in err_msg:
                # Instantly skip this format candidate - it's permanently unavailable
                return False
            pass

        # exponential backoff + jitter
        time.sleep(min(2 ** k, 10) + random.random())
    return False

def download_asset_sync(url: str, title: str, entry_name: str) -> bool:
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
            # final file naming prediction via safe_filename
            "paths": {"temp": tmp_dir},
            "outtmpl": os.path.join("music", f"{safe_filename(entry_name)}.%(ext)s"),

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

async def download_asset(url: str, title: str, entry_name: str) -> bool:
    # run blocking yt-dlp in a worker thread
    return await asyncio.to_thread(download_asset_sync, url, title, entry_name)

async def download_all(assets: Iterable[tuple[str, str, str]], limit: int = 10) -> list[bool]:
    sem = asyncio.Semaphore(limit)

    async def worker(url: str, title: str, entry_name: str) -> bool:
        async with sem:
            return await download_asset(url, title, entry_name)

    tasks = [asyncio.create_task(worker(url, title, entry_name)) for url, title, entry_name in assets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # turn exceptions into False
    out = []
    for r in results:
        out.append(False if isinstance(r, Exception) else bool(r))
    return out

import glob

def download_music(url,db,playlist_start=0,playlist_end=10,enable_playlist=False,save_playlist=True):
    # Use extract_flat to avoid fully downloading all metadata for every video sequentially
    ydl_opts = {"noplaylist":not enable_playlist,"playliststart":playlist_start,"playlistend":playlist_end, "extract_flat": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False,)
        info = ydl.sanitize_info(info)
        titles = []
        if enable_playlist and "entries" in info.keys():
            for entry in info["entries"]:
                entry_url = entry.get("url") or entry.get("webpage_url")
                if entry_url:
                    titles.append({"title":entry["title"],"url":entry_url})
        else:
            entry_url = info.get("url") or info.get("webpage_url")
            titles.append({"title":info["title"],"url":entry_url})
    
    if save_playlist and enable_playlist:
        music = []
        for music_entry in info.get("entries", []):
            uploader = music_entry.get("uploader") or music_entry.get("channel") or "Unknown"
            music.append(f"{music_entry['title']} - {uploader}")
            
        if "playlist" not in db:
            db["playlist"] = {}
        db["playlist"][info["title"]] = music
        save_db(db)
    
    download_list = []
    # If not a playlist, `info` itself represents the single track, so we wrap it in a list
    entries_to_iter = info["entries"] if (enable_playlist and "entries" in info) else [info]
    
    for title_info, track_info in zip(titles, entries_to_iter):
        uploader = track_info.get("uploader") or track_info.get("channel") or "Unknown"
        entry_name = f"{title_info['title']} - {uploader}"
        if entry_name in db["music"]:
            continue
        else:
            download_list.append((title_info["url"], title_info["title"], entry_name))
            
    if download_list:
        results = asyncio.run(download_all(
            download_list, limit=20
        ))
        saved = 0
        failed_or_missing = []
        
        for (url, title, entry_name), ok in zip(download_list, results):
            # Check if file was actually successfully saved to the disk
            escaped_name = glob.escape(safe_filename(entry_name))
            pattern = os.path.join("music", f"{escaped_name}.*")
            matches = glob.glob(pattern)
            
            if ok and matches:
                db["music"].append(entry_name)
                saved += 1
            else:
                print(f"Download failed or file missing on disk: {entry_name}")
                failed_or_missing.append(entry_name)
                
        # Clean up missing music from the playlist we saved earlier
        if failed_or_missing and enable_playlist and save_playlist and "playlist" in db:
            playlist_name = info["title"]
            if playlist_name in db["playlist"]:
                for bad_entry in failed_or_missing:
                    if bad_entry in db["playlist"][playlist_name]:
                        db["playlist"][playlist_name].remove(bad_entry)
                    
        if saved or failed_or_missing:
            save_db(db)
            print(f"Downloaded {saved} music")
        else:
            print("All downloads failed")
    else:
        print("No new music to download")
    return titles

def remove_playlist(db,playlist_name):
    if "playlist" not in db:
        db["playlist"] = {}
    if playlist_name in db["playlist"]:
        del db["playlist"][playlist_name]
    save_db(db)

def add_playlist(db,playlist_name):
    if "playlist" not in db:
        db["playlist"] = {}
    if playlist_name not in db["playlist"]:
        db["playlist"][playlist_name] = []
    save_db(db)

def add_music_to_love_playlist(db,music,username):
    add_music_to_playlist(db,f"{username}_love",music)

def remove_music_from_playlist(db,playlist_name,music):
    if "playlist" not in db:
        db["playlist"] = {}
    if playlist_name in db["playlist"]:
        if isinstance(music, list):
            for music_entry in music:
                if music_entry in db["playlist"][playlist_name]:
                    db["playlist"][playlist_name].remove(music_entry)
        else:
            if music in db["playlist"][playlist_name]:
                db["playlist"][playlist_name].remove(music)
    save_db(db)

def add_music_to_playlist(db,playlist_name,music):
    if "playlist" not in db:
        db["playlist"] = {}
        
    if playlist_name in db["playlist"]:
        if isinstance(music, list):
            db["playlist"][playlist_name].extend(music)
            # Remove duplicates while preserving order
            db["playlist"][playlist_name] = list(dict.fromkeys(db["playlist"][playlist_name]))
        else:
            if music not in db["playlist"][playlist_name]:
                db["playlist"][playlist_name].append(music)
    else:
        db["playlist"][playlist_name] = music if isinstance(music, list) else [music]
    save_db(db)

URL = "https://music.youtube.com/watch?v=5FHMUKeT1HQ&list=RDAMVM5FHMUKeT1HQ"

db = load_db()

download_music(URL,db,playlist_start=0,playlist_end=25,enable_playlist=True,save_playlist=True)
add_music_to_playlist(db,"music",["test","test"])
