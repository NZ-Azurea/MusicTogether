import os
import re
import time
import random
import asyncio
import shutil
import glob
from typing import Iterable

# IDK WHY ANTIGRAVITY DO THIS BUT I FIX IT
if "SSLKEYLOGFILE" in os.environ:
    del os.environ["SSLKEYLOGFILE"]

from json_loader import load_db, save_db

import yt_dlp
from yt_dlp.utils import DownloadError


_ENV_CACHE = None


def _project_root() -> str:
    """Return the project root path."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _ensure_env_file() -> str:
    """Create the project .env with downloader defaults when it does not exist."""
    env_path = os.path.join(_project_root(), ".env")
    if os.path.isfile(env_path):
        return env_path

    default_cookie_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cookies.txt"))
    lines = [
        "YTDLP_YOUTUBE_CONCURRENCY=5",
        "YTDLP_GENERIC_CONCURRENCY=6",
        f"YTDLP_COOKIE_FILE={default_cookie_path}",
        "YTDLP_REQUEST_DELAY=1.0",
        "YTDLP_MAX_VIDEO_CANDIDATES=12",
    ]
    with open(env_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return env_path


def _load_env_config() -> dict:
    """Load downloader settings from the project .env file."""
    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE

    env = {}
    env_path = _ensure_env_file()
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass

    _ENV_CACHE = env
    return env


def _env_int(key: str, default: int) -> int:
    """Read an integer setting from the project environment file."""
    try:
        return int(_load_env_config().get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    """Read a float setting from the project environment file."""
    try:
        return float(_load_env_config().get(key, default))
    except (TypeError, ValueError):
        return default


def _cookie_file_path() -> str:
    """Return the optional project cookie file path."""
    configured = _load_env_config().get("YTDLP_COOKIE_FILE")
    if configured:
        return os.path.abspath(os.path.expandvars(configured))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cookies.txt"))


def _load_cookie_file_source():
    """Return a cookie source descriptor for the project cookie file when available."""
    cookie_file = _cookie_file_path()
    if not os.path.isfile(cookie_file):
        return None

    try:
        with open(cookie_file, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError:
        return None

    if not content:
        return None

    first_line = content.splitlines()[0].strip()
    if first_line.startswith("# Netscape HTTP Cookie File"):
        return ("file", cookie_file)
    if "=" in content and ";" in content and "\t" not in first_line:
        return ("header", content)
    return None


def _request_delay() -> float:
    """Return the configured inter-request delay."""
    return max(_env_float("YTDLP_REQUEST_DELAY", 1.0), 0.0)


def _youtube_parallel_limit() -> int:
    """Return the YouTube download concurrency."""
    return max(_env_int("YTDLP_YOUTUBE_CONCURRENCY", 5), 1)


def _generic_parallel_limit() -> int:
    """Return the default non-YouTube download concurrency."""
    return max(_env_int("YTDLP_GENERIC_CONCURRENCY", 6), 1)


def _max_video_candidates() -> int:
    """Return the number of ranked yt-dlp format candidates to try."""
    return max(_env_int("YTDLP_MAX_VIDEO_CANDIDATES", 12), 1)


def _is_youtube_url(url: str) -> bool:
    """Return True when the URL targets YouTube."""
    value = (url or "").lower()
    return "youtube.com" in value or "youtu.be" in value or "music.youtube.com" in value


def _cookie_browser_variants(url: str):
    """Return yt-dlp cookie sources to try for the given URL."""
    if not _is_youtube_url(url):
        return [None]
    variants = []
    cookie_source = _load_cookie_file_source()
    if cookie_source is not None:
        variants.append(cookie_source)
    variants.extend([None, ("edge",), ("chrome",), ("firefox",)])
    return variants


def _with_cookie_source(base_opts: dict, cookie_source):
    """Clone yt-dlp options and attach an optional browser cookie source."""
    opts = dict(base_opts)
    opts.pop("cookiesfrombrowser", None)
    opts.pop("cookiefile", None)
    headers = dict(opts.get("http_headers") or {})
    headers.pop("Cookie", None)
    if headers:
        opts["http_headers"] = headers
    else:
        opts.pop("http_headers", None)
    if cookie_source is None:
        opts.pop("cookiesfrombrowser", None)
    elif isinstance(cookie_source, tuple) and len(cookie_source) == 2 and cookie_source[0] == "file":
        opts["cookiefile"] = cookie_source[1]
    elif isinstance(cookie_source, tuple) and len(cookie_source) == 2 and cookie_source[0] == "header":
        headers = dict(opts.get("http_headers") or {})
        headers["Cookie"] = cookie_source[1]
        opts["http_headers"] = headers
    else:
        opts["cookiesfrombrowser"] = cookie_source
    return opts


def _is_bot_check_error(error: Exception) -> bool:
    """Detect YouTube anti-bot/authentication failures."""
    text = str(error).lower()
    return (
        "not a bot" in text
        or "cookies-from-browser" in text
        or "use --cookies" in text
    )


def _extract_info_with_fallback(url: str, base_opts: dict) -> dict:
    """Extract media info, retrying with browser cookies when YouTube requires auth."""
    last_error = None
    for cookie_source in _cookie_browser_variants(url):
        opts = _with_cookie_source(base_opts, cookie_source)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except DownloadError as exc:
            last_error = exc
            if not _is_bot_check_error(exc):
                raise
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Could not extract media info")


def safe_filename(name: str, max_len: int = 160) -> str:
    """Return a filesystem-safe filename."""
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", name).strip()
    return name[:max_len] if len(name) > max_len else name


def _rank_format(f: dict) -> tuple:
    """Rank media formats by effective visual quality."""
    w = f.get("width") or 0
    h = f.get("height") or 0
    pixels = w * h
    tbr = f.get("tbr") or 0
    pref = f.get("preference") or 0
    return (pixels, h, tbr, pref)


def _passes_rule(f: dict, min_edge: int = 1080) -> bool:
    """Keep square media and wide/tall media above the minimum short edge."""
    w = f.get("width")
    h = f.get("height")
    if not (w and h):
        return True
    if w == h:
        return True
    return min(w, h) >= min_edge


def _audio_compat_rank(f: dict) -> tuple:
    """Prefer browser-safe audio formats before raw bitrate."""
    acodec = (f.get("acodec") or "").lower()
    ext = (f.get("ext") or "").lower()
    abr = f.get("abr") or 0
    tbr = f.get("tbr") or 0

    preferred_codec = acodec.startswith(("mp4a", "aac"))
    acceptable_codec = acodec.startswith(("mp3",)) or acodec in {"mp4a.40.2", "mp4a.40.5"}
    preferred_ext = ext in {"m4a", "mp4"}
    acceptable_ext = ext in {"mp3"}
    incompatible_codec = acodec.startswith(("opus", "vorbis"))

    return (
        0 if incompatible_codec else 1,
        2 if preferred_codec else (1 if acceptable_codec else 0),
        2 if preferred_ext else (1 if acceptable_ext else 0),
        abr,
        tbr,
    )


def _video_compat_rank(f: dict) -> tuple:
    """Prefer browser-safe video formats before raw resolution."""
    ext = (f.get("ext") or "").lower()
    vcodec = (f.get("vcodec") or "").lower()
    acodec = (f.get("acodec") or "").lower()

    preferred_ext = ext in {"mp4", "m4v"}
    preferred_vcodec = vcodec.startswith(("avc1", "h264"))
    preferred_acodec = acodec in {"none", ""} or acodec.startswith(("mp4a", "aac"))
    incompatible_audio = acodec.startswith(("opus", "vorbis"))

    return (
        0 if incompatible_audio else 1,
        1 if preferred_ext else 0,
        1 if preferred_vcodec else 0,
        1 if preferred_acodec else 0,
        *_rank_format(f),
    )


def _build_candidates(info: dict, max_candidates: int = 12) -> list[str]:
    """Build yt-dlp format expressions ordered by browser compatibility first."""
    formats = info.get("formats") or []
    videos = [f for f in formats if f.get("vcodec") not in (None, "none")]
    videos = [v for v in videos if _passes_rule(v, 720)]
    audios = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]

    videos.sort(key=_video_compat_rank, reverse=True)
    audios.sort(key=_audio_compat_rank, reverse=True)

    candidates = []

    for v in videos[:max_candidates]:
        for a in audios[:5]:
            if v.get("format_id") and a.get("format_id"):
                candidates.append(f"{v['format_id']}+{a['format_id']}")

    singles = [f for f in videos if f.get("acodec") not in (None, "none")]
    singles.sort(key=_video_compat_rank, reverse=True)
    for s in singles[:max_candidates]:
        if s.get("format_id"):
            candidates.append(s["format_id"])

    out = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    out.extend([
        "bv*+ba/b",
        "bestvideo*+bestaudio/best",
        "best",
    ])

    unique_out = []
    seen = set()
    for candidate in out:
        if candidate not in seen:
            seen.add(candidate)
            unique_out.append(candidate)
    return unique_out[: max_candidates + 3]


def _find_downloaded_files(entry_name: str) -> list[str]:
    """Return files created for the given entry name."""
    escaped_name = glob.escape(safe_filename(entry_name))
    pattern = os.path.join("music", f"{escaped_name}.*")
    return [
        path for path in glob.glob(pattern)
        if os.path.isfile(path) and not path.endswith((".part", ".ytdl"))
    ]


def _iter_media_entries(info: dict, enable_playlist: bool) -> list[dict]:
    """Return a normalized ordered list of one or more media entries."""
    if enable_playlist:
        entries = info.get("entries") or []
        return [entry for entry in entries if isinstance(entry, dict)]
    return [info]


def is_track_downloaded(entry_name: str, db: dict | None = None) -> bool:
    """Return True when the track is already present in the DB and on disk."""
    if db is not None and entry_name not in db.get("music", []):
        return False
    return bool(_find_downloaded_files(entry_name))


def _download_with_retries_sync(url: str, ydl_opts: dict, attempts: int = 8) -> bool:
    """Download an asset with backoff and browser-cookie fallback for YouTube."""
    cookie_variants = _cookie_browser_variants(url)
    for cookie_source in cookie_variants:
        opts = _with_cookie_source(ydl_opts, cookie_source)
        for k in range(attempts):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    rc = ydl.download([url])
                if rc == 0:
                    return True
            except DownloadError as exc:
                err_msg = str(exc).lower()
                if "http error 404" in err_msg or "http error 403" in err_msg:
                    return False
                if _is_bot_check_error(exc) and cookie_source is None:
                    break
            time.sleep(min(2 ** k, 10) + random.random())
    return False


def download_asset_sync(url: str, title: str, entry_name: str, progress_callback=None) -> bool:
    """Download one asset synchronously using a temporary workspace."""
    os.makedirs("music", exist_ok=True)
    tmp_root = os.path.join("music", ".tmp")
    os.makedirs(tmp_root, exist_ok=True)
    tmp_dir = os.path.join(tmp_root, f"job_{random.randrange(1 << 32):08x}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        info = _extract_info_with_fallback(url, {
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "sleep_interval_requests": _request_delay(),
        })

        candidates = _build_candidates(info, max_candidates=_max_video_candidates())
        if not candidates:
            return False

        def yt_dlp_progress_hook(d):
            if d["status"] == "downloading":
                try:
                    pct = d.get("_percent_str", "").strip()
                    pct = re.sub(r"\x1b\[[0-9;]*m", "", pct)
                    if progress_callback:
                        progress_callback(entry_name, f"progress:{pct}")
                except Exception:
                    pass

        base_opts = {
            "paths": {"temp": tmp_dir},
            "outtmpl": os.path.join("music", f"{safe_filename(entry_name)}.%(ext)s"),
            "skip_unavailable_fragments": False,
            "writethumbnail": False,
            "progress_hooks": [yt_dlp_progress_hook],
            "retries": 15,
            "fragment_retries": 200,
            "extractor_retries": 10,
            "socket_timeout": 30,
            "merge_output_format": "mp4",
            "format_sort_force": True,
            "quiet": True,
            "no_warnings": True,
            "sleep_interval_requests": _request_delay(),
            "sleep_interval": _request_delay(),
            "max_sleep_interval": 3.0,
            "nopart": True,
            "continuedl": False,
            "concurrent_fragment_downloads": 1,
            "http_chunk_size": 10485760,
        }

        for fmt in candidates:
            ydl_opts = dict(base_opts)
            ydl_opts["format"] = fmt
            if _download_with_retries_sync(url, ydl_opts, attempts=4):
                return True

        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def download_asset(url: str, title: str, entry_name: str, progress_callback=None) -> bool:
    """Run one blocking asset download in a worker thread."""
    return await asyncio.to_thread(download_asset_sync, url, title, entry_name, progress_callback)


async def download_all(assets: Iterable[tuple[str, str, str]], limit: int = 10, progress_callback=None) -> list[bool]:
    """Download assets concurrently up to the provided limit."""
    sem = asyncio.Semaphore(limit)

    async def worker(url: str, title: str, entry_name: str) -> bool:
        async with sem:
            if progress_callback:
                progress_callback(entry_name, "start")
            try:
                res = await download_asset(url, title, entry_name, progress_callback)
            except Exception as exc:
                print(f"Asset worker thread crash ({entry_name}): {exc}")
                res = False
            if progress_callback:
                progress_callback(entry_name, "done" if res else "error")
            return res

    tasks = [asyncio.create_task(worker(url, title, entry_name)) for url, title, entry_name in assets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out = []
    for result in results:
        out.append(False if isinstance(result, Exception) else bool(result))
    return out


def download_music(url,db,playlist_start=0,playlist_end=100,enable_playlist=False,save_playlist=True, progress_callback=None):
    """Download a single track or playlist and register successful files in the DB."""
    ydl_opts = {
        "noplaylist": not enable_playlist,
        "playliststart": playlist_start,
        "playlistend": playlist_end,
        "extract_flat": enable_playlist,
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "sleep_interval_requests": _request_delay(),
    }

    info = _extract_info_with_fallback(url, ydl_opts)
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.sanitize_info(info)

    raw_entries = _iter_media_entries(info, enable_playlist)
    titles = []
    for entry in raw_entries:
        entry_url = entry.get("url") or entry.get("webpage_url")
        if entry_url:
            titles.append({"title": entry.get("title") or "Unknown Title", "url": entry_url})

    if save_playlist and enable_playlist:
        music = []
        for music_entry in raw_entries:
            uploader = music_entry.get("uploader") or music_entry.get("channel") or "Unknown"
            music.append(f"{music_entry['title']} - {uploader}")

        if not db.get("playlist"):
            db["playlist"] = {}
        db["playlist"][info["title"]] = music
        save_db(db)

    download_list = []
    entries_to_iter = raw_entries

    for title_info, track_info in zip(titles, entries_to_iter):
        uploader = track_info.get("uploader") or track_info.get("channel") or "Unknown"
        entry_name = f"{title_info['title']} - {uploader}"
        if entry_name not in db["music"]:
            download_list.append((title_info["url"], title_info["title"], entry_name))

    failed_or_missing = []
    if download_list:
        parallel_limit = _youtube_parallel_limit() if _is_youtube_url(url) else _generic_parallel_limit()
        results = asyncio.run(download_all(
            download_list, limit=parallel_limit, progress_callback=progress_callback
        ))
        saved = 0

        for (_, _, entry_name), ok in zip(download_list, results):
            matches = _find_downloaded_files(entry_name)

            if ok and matches:
                db["music"].append(entry_name)
                saved += 1
            else:
                print(f"Download failed or file missing on disk: {entry_name}")
                failed_or_missing.append(entry_name)

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

    valid_entry_names = []
    for title_info, track_info in zip(titles, entries_to_iter):
        uploader = track_info.get("uploader") or track_info.get("channel") or "Unknown"
        entry_name = f"{title_info['title']} - {uploader}"
        if entry_name not in failed_or_missing:
            valid_entry_names.append(entry_name)

    return valid_entry_names


def resolve_download_entries(url, db, playlist_start=0, playlist_end=100, enable_playlist=False, save_playlist=True):
    """Resolve playlist metadata into ordered entries without downloading media."""
    ydl_opts = {
        "noplaylist": not enable_playlist,
        "playliststart": playlist_start,
        "playlistend": playlist_end,
        "extract_flat": enable_playlist,
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "sleep_interval_requests": _request_delay(),
    }

    info = _extract_info_with_fallback(url, ydl_opts)
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.sanitize_info(info)

    raw_entries = _iter_media_entries(info, enable_playlist)
    entries = []
    playlist_tracks = []

    for entry in raw_entries:
        entry_url = entry.get("url") or entry.get("webpage_url")
        if not entry_url:
            continue
        title = entry.get("title") or "Unknown Title"
        uploader = entry.get("uploader") or entry.get("channel") or "Unknown"
        entry_name = f"{title} - {uploader}"
        entries.append({
            "title": title,
            "url": entry_url,
            "entry_name": entry_name,
            "already_downloaded": is_track_downloaded(entry_name, db),
        })
        playlist_tracks.append(entry_name)

    if save_playlist and enable_playlist:
        if not db.get("playlist"):
            db["playlist"] = {}
        db["playlist"][info["title"]] = playlist_tracks
        save_db(db)

    return {
        "playlist_title": info.get("title") or "Playlist",
        "entries": entries,
    }


def remove_playlist(db,playlist_name):
    """Remove a playlist from the DB."""
    if "playlist" not in db:
        db["playlist"] = {}
    if playlist_name in db["playlist"]:
        del db["playlist"][playlist_name]
    save_db(db)


def add_playlist(db,playlist_name):
    """Create an empty playlist if it does not exist."""
    if "playlist" not in db:
        db["playlist"] = {}
    if playlist_name not in db["playlist"]:
        db["playlist"][playlist_name] = []
    save_db(db)


def add_music_to_love_playlist(db,music,username):
    """Add one track to the user's love playlist."""
    add_music_to_playlist(db, f"{username}_love", music)


def remove_music_from_playlist(db,playlist_name,music):
    """Remove one or more tracks from a playlist."""
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
    """Add one or more tracks to a playlist without duplicating entries."""
    if "playlist" not in db:
        db["playlist"] = {}

    if playlist_name in db["playlist"]:
        if isinstance(music, list):
            db["playlist"][playlist_name].extend(music)
            db["playlist"][playlist_name] = list(dict.fromkeys(db["playlist"][playlist_name]))
        else:
            if music not in db["playlist"][playlist_name]:
                db["playlist"][playlist_name].append(music)
    else:
        db["playlist"][playlist_name] = music if isinstance(music, list) else [music]
    save_db(db)
