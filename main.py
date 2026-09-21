"""
Audiobookshelf (ABS) Snippet Sidecar
Backend app for the bookmarks you create on ABS Mobile app.
Extracts audio clips via ffmpeg, transcribes speech with faster-whisper,
and manages per-user bookmarks and snippets under {username}/bookmarks.
"""

import os
import sys

# Limit OpenBLAS / OpenMP thread pools so ML libraries don't monopolize all CPU cores on Raspberry Pi
if "OMP_NUM_THREADS" not in os.environ:
    os.environ["OMP_NUM_THREADS"] = "2"
if "OPENBLAS_NUM_THREADS" not in os.environ:
    os.environ["OPENBLAS_NUM_THREADS"] = "2"
if "MKL_NUM_THREADS" not in os.environ:
    os.environ["MKL_NUM_THREADS"] = "2"

import re
import json
import shutil
import asyncio
import threading
import subprocess
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote_plus

try:
    import httpx
except ImportError:
    httpx = None

try:
    import websockets
except ImportError:
    websockets = None

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Persistent HTTP session with connection pooling to eliminate socket thrashing against Audiobookshelf
_http_session = requests.Session()
_http_adapter = HTTPAdapter(
    pool_connections=15,
    pool_maxsize=30,
    max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[502, 503, 504])
)
_http_session.mount("http://", _http_adapter)
_http_session.mount("https://", _http_adapter)

from starlette.websockets import WebSocket, WebSocketDisconnect
from fastapi import FastAPI, Request, Header, HTTPException, Depends, Query, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging (stream to sys.stdout so standard INFO logs are routed to pm2 out.log)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("abs-sidecar")

# Base directory of the repository (resolves safely regardless of execution directory)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Automatically load settings from .env file if present in base directory
env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    try:
        with open(env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip('"').strip("'")
                    if _k not in os.environ:
                        os.environ[_k] = _v
    except Exception as _e:
        logger.warning(f"Could not parse .env file: {_e}")

# Configuration from Environment
ABS_TARGET_SERVER = (
    os.environ.get("ABS_TARGET_SERVER")
    or os.environ.get("ABS_INTERNAL_URL")
    or os.environ.get("ABS_SERVER_URL")
    or "http://localhost:13378"
).rstrip("/")
ABS_SERVER_URL = ABS_TARGET_SERVER  # Maintained for backwards compatibility

# Primary VOLUME_DIR with fallback to Pi and Docker default locations
VOLUME_DIR = (
    os.environ.get("VOLUME_DIR")
    or os.environ.get("SNIPPETS_DIR")
    or ("/srv/ssd/Appdata/local/advplyr-bookshelf/bookmarks" if os.path.isdir("/srv/ssd/Appdata/local/advplyr-bookshelf/bookmarks") else "/data")
).rstrip("/")
SNIPPETS_DIR = VOLUME_DIR  # Kept for backward compatibility
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
# Restrict Whisper inference CPU threads so it does not starve other host services on Raspberry Pi
WHISPER_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", os.environ.get("WHISPER_THREADS", "2")))
# Control whether Whisper prewarms immediately on startup (default: false to maintain 0% CPU at idle)
PREWARM_WHISPER = os.environ.get("PREWARM_WHISPER", "false").lower() in ("true", "1", "yes")

SNIPPET_DURATION = int(os.environ.get("SNIPPET_DURATION", "60"))
SNIPPET_PRE_ROLL = float(os.environ.get("SNIPPET_PRE_ROLL", "30.0"))

# Intercepted Bookmarks Default Timing Configuration (from ecosystem.config.cjs or env)
# Configures default duration and pre-roll ONLY for bookmarks intercepted from mobile/web apps.
INTERCEPT_SNIPPET_DURATION = int(os.environ.get("INTERCEPT_SNIPPET_DURATION", os.environ.get("INTERCEPT_DURATION", str(SNIPPET_DURATION))))
INTERCEPT_PRE_ROLL = float(os.environ.get("INTERCEPT_PRE_ROLL", str(SNIPPET_PRE_ROLL)))
DEFAULT_ABS_URL = os.environ.get("DEFAULT_ABS_URL", os.environ.get("ABS_PUBLIC_URL", "")).strip()

# ==============================================================================
# Automated Background Bookmark Sync Daemon Configuration
# Enables 24/7 autonomous extraction of bookmarks created on Android/iOS/Web
# without requiring any reverse proxy interception (no routing to port 13380 required).
# ==============================================================================
AUTO_SYNC_BOOKMARKS = os.environ.get("AUTO_SYNC_BOOKMARKS", "true").lower() in ("true", "1", "yes")
# Default sync interval is 120s (2 minutes) to prevent constant SSD I/O and CPU churn
BOOKMARK_SYNC_INTERVAL = int(os.environ.get("BOOKMARK_SYNC_INTERVAL", os.environ.get("SYNC_INTERVAL", "120")))
TOKEN_CACHE_TTL = int(os.environ.get("TOKEN_CACHE_TTL", "60"))
ABS_API_TOKEN = os.environ.get("ABS_API_TOKEN", os.environ.get("ABS_TOKEN", "")).strip()

# ==============================================================================
# Immutable Installation Date & Bookmark Sync Cutoff Configuration
# Dynamically created at first install, after successful installation and before
# application start. Values are populated based on the system date and time at the
# moment of first installation.
# Preserves existing config files across application updates and restarts.
# ==============================================================================
def get_installation_config_paths() -> List[str]:
    """Candidate file locations for the persistent installation date config."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(app_dir, "installation_date.json"),
        os.path.join(VOLUME_DIR, "installation_date.json"),
        os.path.join(app_dir, ".installation_date.json"),
        os.path.join(VOLUME_DIR, ".installation_date.json")
    ]
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result

def init_or_load_installation_config() -> Dict[str, Any]:
    """
    Checks if an installation date configuration file exists in the installation folder or volume.
    If both file exists and date exists in it: DOES NOT overwrite with a new file.
    If it doesn't exist: dynamically creates a new file populated from current system date and time.
    Supports configurable cutoff modes:
    - 'from_start': Extract all bookmarks from the beginning of the server (cutoff effectively 0).
    - 'custom_date': Extract bookmarks created on or after YYYY-MM-DD.
    - 'from_now': Extract bookmarks created on or after installation/activation date.
    """
    candidate_paths = get_installation_config_paths()

    # 1. Check if file exists in any candidate location and has date (preserves across updates)
    for p in candidate_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    date_val = data.get("cutoff_datetime") or data.get("installation_date") or data.get("installed_at")
                    if date_val:
                        if "cutoff_mode" not in data:
                            data["cutoff_mode"] = "from_now"
                        logger.info(f"[Installation Config] Found existing installation config at '{p}': mode={data.get('cutoff_mode')}, cutoff={data.get('cutoff_datetime') or date_val}")
                        return data
            except Exception as e:
                logger.warning(f"[Installation Config] Error reading {p}: {e}")

    # 2. File doesn't exist: dynamically compute values from current system date and time
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    date_str = now_local.strftime("%Y-%m-%d")
    cutoff_datetime = f"{date_str}T00:00:00"
    cutoff_dt = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0)
    cutoff_ts = cutoff_dt.timestamp()

    config_data = {
        "cutoff_mode": "from_now",
        "custom_date": None,
        "installation_date": date_str,
        "cutoff_datetime": cutoff_datetime,
        "cutoff_timestamp": cutoff_ts,
        "installed_at": now_local.isoformat(),
        "installed_at_utc": now_utc.isoformat(),
        "note": f"Configurable bookmark cutoff period. Default mode: 'from_now' (bookmarks created prior to {cutoff_datetime} excluded)."
    }

    # Save to candidate locations (application folder & volume directory)
    for p in candidate_paths[:2]:
        try:
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            logger.info(f"[Installation Config] Created new installation date file at '{p}' with system date {date_str}")
        except Exception as e:
            logger.warning(f"[Installation Config] Failed to create {p}: {e}")

    return config_data

INSTALLATION_CONFIG = init_or_load_installation_config()

def save_installation_config(new_config: Dict[str, Any]) -> None:
    """Persists updated cutoff configuration to candidate file paths and refreshes runtime globals."""
    global INSTALLATION_CONFIG, _sync_state
    INSTALLATION_CONFIG.update(new_config)
    candidate_paths = get_installation_config_paths()
    for p in candidate_paths[:2]:
        try:
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(INSTALLATION_CONFIG, f, indent=2)
            logger.info(f"[Installation Config] Updated cutoff config saved to '{p}'")
        except Exception as e:
            logger.warning(f"[Installation Config] Failed writing updated config to '{p}': {e}")

    # Synchronize _sync_state
    _sync_state["installation_date"] = INSTALLATION_CONFIG.get("installation_date")
    _sync_state["cutoff_datetime"] = INSTALLATION_CONFIG.get("cutoff_datetime")
    _sync_state["cutoff_mode"] = INSTALLATION_CONFIG.get("cutoff_mode", "from_now")
    _sync_state["custom_cutoff_date"] = INSTALLATION_CONFIG.get("custom_date")

def is_bookmark_after_installation_cutoff(created_at_raw: Any) -> bool:
    """
    Checks if a bookmark was created on or after the configured cutoff date/mode:
    - 'from_start': Always returns True (all historical bookmarks included).
    - 'from_now': Bookmarks on or after the installation date at 00:00:00.
    - 'custom_date': Bookmarks on or after the specified custom YYYY-MM-DD at 00:00:00.
    Returns False if created before the cutoff or if creation date cannot be verified.
    """
    mode = INSTALLATION_CONFIG.get("cutoff_mode", "from_now")
    if mode == "from_start":
        return True

    if created_at_raw is None or created_at_raw == "":
        return False

    cutoff_ts = float(INSTALLATION_CONFIG.get("cutoff_timestamp", 0.0))
    cutoff_dt_str = INSTALLATION_CONFIG.get("cutoff_datetime") or INSTALLATION_CONFIG.get("installation_date") or ""

    # Parse cutoff year/month/day from the config
    c_year, c_month, c_day = None, None, None
    try:
        clean_cutoff = cutoff_dt_str[:10]
        parts = [int(p) for p in clean_cutoff.split("-")]
        if len(parts) == 3:
            c_year, c_month, c_day = parts[0], parts[1], parts[2]
    except Exception:
        pass

    # 1. Numeric epoch timestamp (ms or s)
    try:
        val = float(created_at_raw)
        if val > 1e11:
            val = val / 1000.0
        # Check against cutoff timestamp (giving a 60s tolerance for slight clock skew)
        if cutoff_ts > 0 and val >= (cutoff_ts - 60.0):
            return True
        # Check calendar date in UTC
        if c_year and c_month and c_day:
            dt_utc = datetime.fromtimestamp(val, tz=timezone.utc)
            if (dt_utc.year, dt_utc.month, dt_utc.day) >= (c_year, c_month, c_day):
                return True
        return False
    except (ValueError, TypeError):
        pass

    # 2. String representation (ISO 8601, formatted date, etc.)
    if isinstance(created_at_raw, str):
        if c_year and c_month and c_day:
            try:
                clean = created_at_raw.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean)
                if (dt.year, dt.month, dt.day) >= (c_year, c_month, c_day):
                    return True
                return False
            except Exception:
                pass

            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(created_at_raw.strip()[:10], fmt)
                    if (dt.year, dt.month, dt.day) >= (c_year, c_month, c_day):
                        return True
                    return False
                except Exception:
                    pass

    return False

# Tombstone tracking for explicitly deleted bookmarks
def get_tombstone_file_paths() -> List[str]:
    """Candidate file locations for persistent tombstone records across updates and re-installs."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(VOLUME_DIR, ".deleted_tombstones.json"),
        os.path.join(app_dir, ".deleted_tombstones.json"),
        os.path.join(VOLUME_DIR, "deleted_tombstones.json"),
        os.path.join(app_dir, "deleted_tombstones.json"),
    ]
    try:
        for c_root in get_candidate_volume_dirs():
            if c_root:
                candidates.append(os.path.join(c_root, ".deleted_tombstones.json"))
                candidates.append(os.path.join(c_root, "deleted_tombstones.json"))
    except Exception:
        pass
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result

def load_deleted_tombstones() -> Dict[str, Any]:
    """Loads and merges tombstones from all candidate locations."""
    merged: Dict[str, Dict[str, Any]] = {}
    for p in get_tombstone_file_paths():
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("tombstones", []):
                        # Construct a unique key so we merge without duplicating
                        k = (
                            item.get("snippet_id") or
                            f"{item.get('library_item_id')}_{item.get('time')}_{item.get('created_at')}"
                        )
                        if k in merged:
                            merged[k].update({field: val for field, val in item.items() if val is not None})
                        else:
                            merged[k] = item
            except Exception:
                pass
    return {"tombstones": list(merged.values())}

def record_deleted_tombstone(
    snippet_id: str,
    lib_id: Optional[str] = None,
    book_time: Optional[float] = None,
    start_time: Optional[float] = None,
    current_time: Optional[float] = None,
    book_title: Optional[str] = None,
    title: Optional[str] = None,
    created_at: Optional[Any] = None,
    timestamp: Optional[str] = None,
    bookmark_id: Optional[str] = None
):
    """
    Persistently records a tombstone entry across all volume and application directories
    so that manual deletions in the web UI are never resurrected on server restarts,
    re-installs, updates, or background bookmark sync cycles.
    """
    try:
        data = load_deleted_tombstones()
        clean_snip = snippet_id.strip().strip("/") if snippet_id else ""
        resolved_time = current_time if current_time is not None else (book_time if book_time is not None else start_time)

        entry: Dict[str, Any] = {
            "snippet_id": clean_snip,
            "bookmark_id": str(bookmark_id).strip() if bookmark_id else None,
            "library_item_id": str(lib_id).strip() if lib_id and str(lib_id).strip() not in ("N/A", "unknown", "") else None,
            "time": float(resolved_time) if resolved_time is not None else None,
            "start_time": float(start_time) if start_time is not None else None,
            "current_time": float(current_time) if current_time is not None else (float(book_time) if book_time is not None else None),
            "book_title": str(book_title).strip() if book_title else None,
            "title": str(title).strip() if title else None,
            "created_at": created_at,
            "timestamp": str(timestamp).strip() if timestamp else None,
            "deleted_at": datetime.now().isoformat()
        }

        # Check if already present in tombstones
        existing_idx = None
        for i, itm in enumerate(data["tombstones"]):
            if clean_snip and itm.get("snippet_id") == clean_snip:
                existing_idx = i
                break
            if lib_id and itm.get("library_item_id") == str(lib_id):
                if resolved_time is not None and itm.get("time") is not None:
                    if abs(float(itm["time"]) - float(resolved_time)) <= 5.0:
                        existing_idx = i
                        break

        if existing_idx is not None:
            data["tombstones"][existing_idx].update({k: v for k, v in entry.items() if v is not None})
        else:
            data["tombstones"].append(entry)

        # Write to all candidate paths to guarantee survivability across updates
        for p in get_tombstone_file_paths():
            try:
                p_dir = os.path.dirname(p)
                if p_dir and not os.path.exists(p_dir):
                    os.makedirs(p_dir, exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as write_err:
                logger.debug(f"Notice writing tombstone to {p}: {write_err}")
    except Exception as e:
        logger.warning(f"Could not record tombstone: {e}")

def is_bookmark_tombstoned(
    lib_id: Optional[str] = None,
    book_time: Optional[float] = None,
    snippet_id: Optional[str] = None,
    title: Optional[str] = None,
    created_at: Optional[Any] = None,
    book_title: Optional[str] = None
) -> bool:
    """
    Checks if a candidate bookmark was previously deleted by the user.
    Matches across multiple identifiers: ABS bookmark id, libraryItemId + time offset (within 35s),
    createdAt timestamps, or titles for unavailable/moved books.
    """
    data = load_deleted_tombstones()
    clean_snip = str(snippet_id).strip().strip("/") if snippet_id else ""
    str_lib_id = str(lib_id).strip() if lib_id and str(lib_id).strip() not in ("N/A", "unknown", "") else ""

    for item in data.get("tombstones", []):
        t_snip = str(item.get("snippet_id") or "").strip().strip("/")
        t_bm_id = str(item.get("bookmark_id") or "").strip()
        t_lib = str(item.get("library_item_id") or "").strip()

        # 1. Direct snippet ID or bookmark ID match
        if clean_snip:
            if t_snip and (clean_snip == t_snip or clean_snip in t_snip or t_snip in clean_snip):
                return True
            if t_bm_id and (clean_snip == t_bm_id or clean_snip in t_bm_id):
                return True

        # 2. Match by library_item_id and time offset
        if str_lib_id and t_lib and str_lib_id == t_lib:
            if book_time is not None:
                for cand_t in (item.get("time"), item.get("current_time"), item.get("start_time")):
                    if cand_t is not None:
                        # Allow up to 35s difference to cover pre-roll window offsets
                        if abs(float(cand_t) - float(book_time)) <= 35.0:
                            return True

        # 3. Match by unique createdAt timestamp
        if created_at and item.get("created_at"):
            try:
                c1 = float(created_at)
                c2 = float(item["created_at"])
                if c1 > 1e11: c1 /= 1000.0
                if c2 > 1e11: c2 /= 1000.0
                if abs(c1 - c2) <= 3.0:
                    return True
            except Exception:
                pass
            if str(created_at).strip() == str(item.get("created_at")).strip():
                return True

        # 4. Fallback match for unextractable/unavailable books (where lib_id might be missing)
        if not str_lib_id or not t_lib:
            if book_time is not None and item.get("time") is not None:
                if abs(float(item["time"]) - float(book_time)) <= 15.0:
                    if title and item.get("title") and title.strip().lower() == str(item.get("title")).strip().lower():
                        return True
                    if book_title and item.get("book_title") and book_title.strip().lower() in str(item.get("book_title")).strip().lower():
                        return True

    return False

_sync_state: Dict[str, Any] = {
    "is_syncing": False,
    "last_synced_at": None,
    "total_synced": 0,
    "current_item": None,
    "last_error": None,
    "installation_date": INSTALLATION_CONFIG.get("installation_date"),
    "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
    "cutoff_mode": INSTALLATION_CONFIG.get("cutoff_mode", "from_now"),
    "custom_cutoff_date": INSTALLATION_CONFIG.get("custom_date"),
    "installed_at": INSTALLATION_CONFIG.get("installed_at"),
    "skipped_before_cutoff": 0,
    "skipped_tombstoned": 0
}
_sync_lock = threading.Lock()
_last_saved_session_hash: Optional[str] = None

def save_sync_session(token: str, server_url: str, user_info: Dict[str, Any]):
    """Persists authenticated credentials so background sync runs 24/7 across server reboots."""
    global _last_saved_session_hash
    session_hash = f"{token}:{server_url}:{user_info.get('id', '')}"
    if _last_saved_session_hash == session_hash:
        return  # Avoid redundant SSD writes

    try:
        session_file = os.path.join(VOLUME_DIR, ".abs_sync_session.json")
        data = {
            "token": token,
            "server_url": server_url,
            "user": {
                "id": str(user_info.get("id", "")),
                "username": str(user_info.get("username", ""))
            },
            "saved_at": datetime.now().isoformat()
        }
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _last_saved_session_hash = session_hash
        if "_socket_listener" in globals() and _socket_listener is not None:
            _socket_listener.wake()
    except Exception as e:
        logger.warning(f"Could not persist sync session: {e}")

def load_sync_session() -> Optional[Dict[str, Any]]:
    """Loads previously saved session credentials for autonomous sync."""
    try:
        session_file = os.path.join(VOLUME_DIR, ".abs_sync_session.json")
        if os.path.exists(session_file):
            with open(session_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load sync session: {e}")
    return None

# In-memory tracking of recent extraction completions for real-time frontend notifications
_recent_extractions: List[Dict[str, Any]] = []
_extractions_lock = threading.Lock()

AUDIOBOOKS_PATH = os.environ.get("AUDIOBOOKS_PATH", "").strip().rstrip("/")
PATH_MAPPINGS = os.environ.get("PATH_MAPPINGS", "").strip()

# Ensure main volume directory exists
try:
    os.makedirs(VOLUME_DIR, exist_ok=True)
except Exception as _e:
    logger.warning(f"Could not create VOLUME_DIR {VOLUME_DIR}: {_e}")


def get_candidate_volume_dirs() -> List[str]:
    """
    Returns an ordered list of candidate directories where user bookmarks may reside.
    Ensures seamless discovery across Raspberry Pi, Docker, and customized mount paths.
    Note: Media library paths (e.g. /srv/ssd/Bookshelf) are intentionally excluded to prevent
    unnecessary SSD I/O and disk heating.
    """
    dirs = []
    if VOLUME_DIR and VOLUME_DIR not in dirs and os.path.isdir(VOLUME_DIR):
        dirs.append(VOLUME_DIR)
    for p in [
        "/srv/ssd/Appdata/local/advplyr-bookshelf/bookmarks",
        "/srv/ssd/Bookshelf/advplyr-bookshelf/bookmarks",
        "/srv/ssd/Appdata/local/audiobookshelf-bookmarks-manager/bookmarks",
        "/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Manager/bookmarks",
        "/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Extractor/bookmarks",
        "/srv/ssd/Appdata/local/advplyr-bookshelf",
        "/data",
        "/bookmarks"
    ]:
        if p not in dirs and os.path.isdir(p):
            dirs.append(p)
    return dirs


def map_container_path_to_host(container_path: str, book_title: Optional[str] = None) -> str:
    """
    Translates an Audiobookshelf Docker container path (e.g. /audiobooks/... or /summaries/...)
    to the real host filesystem path when running the sidecar outside Docker (e.g. via PM2 or systemd).

    Resolves paths in order:
    1. Direct host path existence check.
    2. Explicit PATH_MAPPINGS (comma-separated 'container:host', e.g. '/audiobooks:/srv/ssd/Bookshelf/Audiobooks,/summaries:/srv/ssd/Bookshelf/Summaries').
    3. AUDIOBOOKS_PATH environment variable (e.g. '/srv/ssd/Bookshelf/Audiobooks').
    4. Auto-discovery from VOLUME_DIR ancestors and common media/storage root folders.
    5. Filename match under candidate libraries.
    """
    if not container_path:
        return container_path

    # 1. Direct host existence
    if os.path.exists(container_path):
        return container_path

    # 2. Build mapping table from PATH_MAPPINGS & AUDIOBOOKS_PATH
    mappings: Dict[str, str] = {}
    if PATH_MAPPINGS:
        for pair in PATH_MAPPINGS.split(","):
            if ":" in pair:
                c_p, h_p = pair.split(":", 1)
                mappings[c_p.strip().rstrip("/")] = h_p.strip().rstrip("/")

    if AUDIOBOOKS_PATH and "/audiobooks" not in mappings:
        mappings["/audiobooks"] = AUDIOBOOKS_PATH

    for c_prefix, h_prefix in mappings.items():
        if container_path == c_prefix or container_path.startswith(c_prefix + "/"):
            mapped = h_prefix + container_path[len(c_prefix):]
            if os.path.exists(mapped):
                logger.info(f"Mapped container path '{container_path}' -> '{mapped}' (via PATH_MAPPINGS)")
                return mapped

    # 3. Intelligent auto-discovery from VOLUME_DIR and host directory structure
    candidate_roots = []
    if AUDIOBOOKS_PATH:
        candidate_roots.append(AUDIOBOOKS_PATH)

    # Derive ancestor directories from VOLUME_DIR (e.g. /srv/ssd/Bookshelf/advplyr-bookshelf/bookmarks -> /srv/ssd/Bookshelf)
    v_dir = os.path.abspath(VOLUME_DIR)
    curr = v_dir
    for _ in range(4):
        curr = os.path.dirname(curr)
        if curr and curr != "/":
            candidate_roots.append(curr)

    candidate_roots.extend([
        "/srv/ssd/Bookshelf",
        "/srv/ssd/Bookshelf/Audiobooks",
        "/srv/ssd/Bookshelf/Summaries",
        "/srv/ssd",
        "/srv",
        "/mnt",
        "/media",
        "/volume1",
        "/data"
    ])

    clean_subpath = container_path.lstrip("/")
    parts = clean_subpath.split("/", 1)
    first_part = parts[0] if parts else ""
    remaining_subpath = parts[1] if len(parts) > 1 else clean_subpath

    container_prefixes = ["audiobooks", "summaries", "podcasts", "books", "calibre", "ebooks", "media"]

    for root in candidate_roots:
        if not os.path.isdir(root):
            continue

        # Option A: root + container subpath (e.g. /srv/ssd/Bookshelf + Audiobooks/...)
        test_a = os.path.join(root, clean_subpath)
        if os.path.exists(test_a):
            logger.info(f"Auto-discovered audio file at '{test_a}' (matched clean subpath)")
            return test_a

        # Option B: root + capitalized/varied prefix (e.g. /srv/ssd/Bookshelf + /Audiobooks/The Spike/...)
        if first_part.lower() in container_prefixes:
            for variant in [first_part, first_part.capitalize(), first_part.lower(), "Audiobooks", "Summaries"]:
                test_b = os.path.join(root, variant, remaining_subpath)
                if os.path.exists(test_b):
                    logger.info(f"Auto-discovered audio file at '{test_b}' (matched folder '{variant}')")
                    return test_b

        # Option C: root + remaining_subpath directly (if root is already the audiobooks folder)
        test_c = os.path.join(root, remaining_subpath)
        if os.path.exists(test_c):
            logger.info(f"Auto-discovered audio file at '{test_c}'")
            return test_c

    # 4. Search by filename inside candidate library roots (bounded depth to protect SSD and CPU)
    filename = os.path.basename(container_path)
    if filename:
        for search_base in [AUDIOBOOKS_PATH, "/srv/ssd/Bookshelf/Audiobooks", "/srv/ssd/Bookshelf/Summaries"]:
            if search_base and os.path.isdir(search_base):
                base_depth = search_base.rstrip(os.sep).count(os.sep)
                for dirpath, _, filenames in os.walk(search_base):
                    # Do not recurse more than 3 directory levels deep
                    if dirpath.count(os.sep) - base_depth > 3:
                        continue
                    if filename in filenames:
                        found = os.path.join(dirpath, filename)
                        logger.info(f"Found audio file by filename search: '{found}'")
                        return found

    # Fallback: if AUDIOBOOKS_PATH is set and container path starts with /audiobooks/, return mapped path
    if AUDIOBOOKS_PATH and container_path.startswith("/audiobooks/"):
        return AUDIOBOOKS_PATH + container_path[len("/audiobooks"):]

    return container_path


# Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# FastAPI App
app = FastAPI(
    title="Audiobookshelf Bookmarks Manager",
    description="Autonomous manager, real-time listener, and automated bookmark audio clipper & transcriber for Audiobookshelf.",
    version="2.0.0"
)

# Global session cache so background workers and bookmark extractors have access to authenticated credentials
_last_authenticated_session: Dict[str, Any] = {
    "token": None,
    "user": None,
    "time": None
}

@app.on_event("startup")
async def startup_event():
    """Start autonomous background bookmark sync daemon and handle Whisper model initialization."""
    if PREWARM_WHISPER:
        def _warmup():
            try:
                get_whisper_model()
            except Exception as e:
                logger.warning(f"Whisper background pre-warm encountered: {e}")
        asyncio.create_task(asyncio.to_thread(_warmup))
    else:
        logger.info(f"Whisper lazy-loading active (max threads: {WHISPER_CPU_THREADS}) - idle CPU stays near 0%.")
    asyncio.create_task(background_bookmark_sync_daemon())
    asyncio.create_task(_socket_listener.start())

# Enable CORS so native mobile apps (iOS / Android), WebViews, and external clients can call endpoints directly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global faster-whisper model cache (lazy-loaded)
_whisper_model = None

# Global Vosk model cache (lazy-loaded backup engine)
VOSK_MODEL_PATH = os.environ.get("VOSK_MODEL_PATH", "/app/vosk-model")
VOSK_MODEL_NAME = os.environ.get("VOSK_MODEL_NAME", "vosk-model-small-en-us-0.15")
_vosk_model = None


def get_whisper_model():
    """Lazy-load the faster-whisper model to optimize startup time, CPU threads, and memory."""
    global _whisper_model
    if _whisper_model is None:
        logger.info(f"Loading faster-whisper model '{WHISPER_MODEL_NAME}' on {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE}, threads={WHISPER_CPU_THREADS})...")
        try:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(
                WHISPER_MODEL_NAME,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                cpu_threads=WHISPER_CPU_THREADS
            )
            logger.info("faster-whisper model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper model: {e}")
            raise e
    return _whisper_model


def get_vosk_model():
    """Lazy-load the Vosk speech recognition model as a backup engine."""
    global _vosk_model
    if _vosk_model is None:
        try:
            from vosk import Model
            if os.path.exists(VOSK_MODEL_PATH):
                logger.info(f"Loading Vosk model from directory: {VOSK_MODEL_PATH}")
                _vosk_model = Model(VOSK_MODEL_PATH)
            else:
                logger.info(f"Loading Vosk model by name: {VOSK_MODEL_NAME}")
                _vosk_model = Model(model_name=VOSK_MODEL_NAME)
            logger.info("Vosk backup model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load Vosk model: {e}")
            raise e
    return _vosk_model


def get_ffmpeg_bin() -> str:
    """
    Finds the ffmpeg executable across system PATH, standard Unix paths,
    or optional portable Python binary (handles restricted PM2/service PATH).
    """
    # 1. System PATH
    bin_path = shutil.which("ffmpeg")
    if bin_path:
        return bin_path

    # 2. Standard Linux/macOS binary paths
    common_paths = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/snap/bin/ffmpeg",
        os.path.join(BASE_DIR, "bin", "ffmpeg"),
        os.path.join(os.path.expanduser("~"), "bin", "ffmpeg"),
    ]
    for p in common_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    # 3. Check if imageio_ffmpeg is installed
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    raise RuntimeError(
        "ffmpeg is not installed or not found on the host system PATH. "
        "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
    )


def run_low_priority_ffmpeg(cmd: List[str], suppress_output: bool = False) -> subprocess.CompletedProcess:
    """
    Executes ffmpeg with low scheduling priority (nice -n 15 on Linux/Unix) and limited threads.
    Protects Raspberry Pi / low-power servers from CPU exhaustion and audio stuttering.
    """
    final_cmd = list(cmd)
    if "-threads" not in final_cmd:
        final_cmd.insert(1, "-threads")
        final_cmd.insert(2, "2")

    preexec = None
    if os.name == "posix":
        def _nice_preexec():
            try:
                os.nice(15)
            except Exception:
                pass
        preexec = _nice_preexec

    stdout_dest = subprocess.DEVNULL if suppress_output else subprocess.PIPE
    stderr_dest = subprocess.DEVNULL if suppress_output else subprocess.PIPE

    return subprocess.run(
        final_cmd,
        stdout=stdout_dest,
        stderr=stderr_dest,
        text=True,
        check=False,
        preexec_fn=preexec
    )


def transcribe_with_vosk(audio_file_path: str) -> str:
    """
    Transcribe audio with Vosk backup engine.
    Uses ffmpeg to create a temporary 16kHz mono PCM WAV and feeds to KaldiRecognizer.
    """
    import wave
    from vosk import KaldiRecognizer

    vosk_model = get_vosk_model()
    wav_path = audio_file_path + ".vosk_temp.wav"
    try:
        ffmpeg_bin = get_ffmpeg_bin()
        cmd = [
            ffmpeg_bin, "-y", "-i", audio_file_path,
            "-ar", "16000", "-ac", "1", "-f", "wav", wav_path
        ]
        run_low_priority_ffmpeg(cmd, suppress_output=True)

        rec = KaldiRecognizer(vosk_model, 16000)
        rec.SetWords(True)

        results = []
        with wave.open(wav_path, "rb") as wf:
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    part = json.loads(rec.Result())
                    if part.get("text"):
                        results.append(part["text"])
            final = json.loads(rec.FinalResult())
            if final.get("text"):
                results.append(final["text"])

        return " ".join(results).strip()
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def sanitize_filename(name: str) -> str:
    """Sanitize strings for filesystem directory and file names."""
    if not name:
        return "untitled"
    clean = re.sub(r'[\\/*?:"<>|]', "_", name)
    clean = clean.strip(" .")
    return clean or "untitled"


_last_known_abs_server: Optional[str] = None

def resolve_abs_server_url(
    req_url: Optional[str] = None,
    header_url: Optional[str] = None,
    query_url: Optional[str] = None
) -> str:
    """
    Dynamically resolve Audiobookshelf server URL.
    The configured ABS_TARGET_SERVER (internal address like http://127.0.0.1:13378)
    is the authoritative upstream target for the sidecar proxy.
    This prevents recursive loops when external clients provide their public URL (e.g. https://abs.example.com).
    """
    # 1. Authoritative internal configuration:
    # If ABS_TARGET_SERVER is configured on this host (e.g. 127.0.0.1, localhost, LAN IP, or Docker service),
    # ALWAYS use it as the upstream target. This ensures the sidecar connects directly to Audiobookshelf
    # on the internal network and never loops back through the external reverse proxy.
    configured = (ABS_TARGET_SERVER or ABS_SERVER_URL or "http://127.0.0.1:13378").strip().rstrip("/")
    if configured and not (configured.startswith("http://") or configured.startswith("https://")):
        configured = f"http://{configured}"

    is_internal = any(
        k in configured.lower()
        for k in ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.", "audiobookshelf", "host.docker.internal"]
    )
    if is_internal:
        return configured

    # 2. If ABS_TARGET_SERVER was not an internal address, check explicit client parameters
    explicit = req_url or header_url or query_url
    if explicit:
        clean_exp = str(explicit).strip().rstrip("/")
        if clean_exp and not (clean_exp.startswith("http://") or clean_exp.startswith("https://")):
            clean_exp = f"http://{clean_exp}"
        return clean_exp

    return configured or "http://127.0.0.1:13378"


def extract_authors(meta: Any, fallback: str = "Unknown Author") -> str:
    """
    Safely extract author names from Audiobookshelf metadata.
    Handles arrays of author dicts [{'name': '...'}], arrays of strings, single strings, or dicts.
    Prevents '[object Object]' or Python dict dumps in metadata and API responses.
    """
    if not meta:
        return fallback

    if isinstance(meta, str) and meta.strip():
        return meta.strip()

    if isinstance(meta, list):
        names = []
        for item in meta:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                n = item.get("name") or item.get("author") or item.get("displayName") or item.get("authorName")
                if n and str(n).strip():
                    names.append(str(n).strip())
        if names:
            return ", ".join(names)

    if isinstance(meta, dict):
        an = meta.get("authorName")
        if isinstance(an, str) and an.strip():
            return an.strip()

        authors_field = meta.get("authors")
        if authors_field:
            res = extract_authors(authors_field, fallback="")
            if res:
                return res

        author_field = meta.get("author")
        if author_field:
            res = extract_authors(author_field, fallback="")
            if res:
                return res

        disp = meta.get("displayAuthor")
        if isinstance(disp, str) and disp.strip():
            return disp.strip()

    return fallback


_token_validation_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_token_cache_lock = threading.Lock()


def validate_abs_token(token: str, server_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate the Bearer token with Audiobookshelf via GET {target_server}/api/me.
    Uses the dynamically provided server_url if passed, falling back to ABS_SERVER_URL.
    Returns user dict with id and username.
    Caches token validation results for TOKEN_CACHE_TTL seconds to avoid overwhelming Audiobookshelf.
    """
    global _token_validation_cache
    target_server = resolve_abs_server_url(req_url=server_url)

    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization token")

    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    cache_key = f"{target_server}:{token}"
    now = time.time()
    with _token_cache_lock:
        if cache_key in _token_validation_cache:
            cached_time, cached_user = _token_validation_cache[cache_key]
            if now - cached_time < TOKEN_CACHE_TTL:
                return cached_user

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        url = f"{target_server}/api/me"
        resp = _http_session.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            with _token_cache_lock:
                _token_validation_cache.pop(cache_key, None)
            logger.warning(f"ABS token validation failed with status {resp.status_code} at {target_server}")
            raise HTTPException(
                status_code=401,
                detail=f"Invalid or expired Audiobookshelf Bearer token (HTTP {resp.status_code} from {target_server})"
            )

        data = resp.json()
        user_info = data.get("user") if isinstance(data.get("user"), dict) else data

        user_id = user_info.get("id")
        username = user_info.get("username") or user_info.get("name") or "abs_user"

        if not user_id:
            raise HTTPException(status_code=401, detail="Failed to retrieve user ID from Audiobookshelf response")

        res_user = {
            "id": str(user_id),
            "username": str(username),
            "raw_token": token,
            "mediaProgress": user_info.get("mediaProgress") or [],
            "server_url": target_server
        }

        with _token_cache_lock:
            _token_validation_cache[cache_key] = (now, res_user)
            if len(_token_validation_cache) > 50:
                _token_validation_cache = {
                    k: v for k, v in _token_validation_cache.items() if now - v[0] < TOKEN_CACHE_TTL * 2
                }

        # Cache session globally so background workers can resolve metadata even if headers are absent
        global _last_authenticated_session
        _last_authenticated_session = {
            "token": token,
            "user": res_user,
            "time": datetime.now()
        }

        # Persist session to disk for 24/7 background sync daemon (deduped)
        save_sync_session(token, target_server, res_user)

        return res_user
    except requests.exceptions.RequestException as e:
        logger.error(f"Error communicating with Audiobookshelf at {target_server}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Audiobookshelf server at {target_server}: {str(e)}"
        )


def extract_token_from_request(request: Request, body_bytes: bytes = b"") -> Optional[str]:
    """
    Extracts authentication token from any possible location:
    Headers, Cookies, Query Params, or Request Body.
    """
    # 1. Authorization header (Bearer or raw token)
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()
        else:
            return auth.strip()

    # 2. Other common custom headers
    for h in ["x-abs-token", "x-token", "x-api-key", "token", "apikey"]:
        val = request.headers.get(h)
        if val:
            return val.strip()

    # 3. Cookies
    for c in ["abs_token", "token", "session", "connect.sid"]:
        val = request.cookies.get(c)
        if val:
            return val.strip()

    # 4. Query params
    for q in ["token", "apiKey", "api_key"]:
        val = request.query_params.get(q)
        if val:
            return val.strip()

    # 5. Body payload
    if body_bytes:
        try:
            body_json = json.loads(body_bytes.decode("utf-8"))
            if isinstance(body_json, dict):
                for k in ["token", "abs_token", "apiKey", "api_key"]:
                    if body_json.get(k):
                        return str(body_json[k]).strip()
        except Exception:
            pass

    return None


async def extract_token_flexible(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_abs_token: Optional[str] = Header(None, alias="X-ABS-Token"),
    token: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None, alias="apiKey")
) -> str:
    """
    Flexible token extractor callable by:
    - Mobile apps and API clients (Authorization: Bearer <TOKEN> or X-ABS-Token header)
    - Browser query parameter (?token=<TOKEN> or ?apiKey=<TOKEN>)
    - Request body / cookies
    """
    # 1. Authorization header
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()

    # 2. X-ABS-Token header
    if x_abs_token:
        return x_abs_token.strip()

    # 3. Query params
    if token:
        return token.strip()
    if api_key:
        return api_key.strip()

    # 4. JSON body if available
    try:
        body = await request.json()
        if isinstance(body, dict):
            body_token = body.get("token") or body.get("abs_token") or body.get("apiKey")
            if body_token:
                return str(body_token).strip()
    except Exception:
        pass

    # 5. Cookie
    cookie_token = request.cookies.get("abs_token")
    if cookie_token:
        return cookie_token.strip()

    raise HTTPException(status_code=401, detail="Authorization token required (via Bearer header, X-ABS-Token, or ?token= query parameter)")


class SnippetRequest(BaseModel):
    """
    Flexible request payload for audio extraction and transcription.
    Accepts snake_case and camelCase parameters for compatibility with mobile, scripts, and Web clients.
    """
    duration: Optional[int] = 60
    start_time: Optional[float] = None
    startTime: Optional[float] = None
    offset: Optional[float] = None
    bookmark_id: Optional[str] = None
    bookmarkId: Optional[str] = None
    library_item_id: Optional[str] = None
    libraryItemId: Optional[str] = None
    title: Optional[str] = None
    token: Optional[str] = None
    server_url: Optional[str] = None
    serverUrl: Optional[str] = None
    abs_server_url: Optional[str] = None
    absServerUrl: Optional[str] = None


class SnippetExpandRequest(BaseModel):
    """
    Payload for adjusting, expanding, or re-extracting an existing snippet.
    Re-clips audio using new pre-roll and post-roll durations and updates in-place.
    """
    timestamp: str
    current_time: Optional[float] = None
    currentTime: Optional[float] = None
    pre_roll: Optional[float] = 30.0
    preRoll: Optional[float] = None
    post_roll: Optional[float] = 60.0
    postRoll: Optional[float] = None
    library_item_id: Optional[str] = None
    libraryItemId: Optional[str] = None
    book_title: Optional[str] = None
    bookTitle: Optional[str] = None
    token: Optional[str] = None
    server_url: Optional[str] = None
    serverUrl: Optional[str] = None


class CutoffConfigRequest(BaseModel):
    """
    Payload for configuring the bookmark extraction cutoff period.
    Options:
    - 'from_start': Extracts all bookmarks from server history
    - 'custom_date': Extracts bookmarks created on or after custom_date (YYYY-MM-DD or YYYY/MM/DD)
    - 'from_now': Extracts bookmarks created on or after installation/activation date
    """
    cutoff_mode: str  # 'from_start' | 'custom_date' | 'from_now'
    custom_date: Optional[str] = None


def format_bookmarked_duration(seconds: float) -> str:
    """Format duration into HH:MM:SS (SSSS seconds) format."""
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d} ({total_sec} seconds)"


def resolve_audio_target(
    token: str,
    req: Optional[SnippetRequest] = None,
    user_info: Optional[Dict[str, Any]] = None,
    server_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Resolve the target audio file, timestamp, and book metadata.
    Handles:
    1. Direct bookmark extraction (if bookmark_id or bookmarkId is provided)
    2. Explicit offset or libraryItemId
    3. Active listening sessions from GET /api/me/listening-sessions
    4. Fallback to latest mediaProgress from GET /api/me if listening session has timed out
    """
    target_server = resolve_abs_server_url(
        req_url=server_url or (req.server_url if req else None) or (req.serverUrl if req else None) or (req.abs_server_url if req else None) or (req.absServerUrl if req else None)
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    target_lib_item_id = req.library_item_id or req.libraryItemId if req else None
    target_offset = req.start_time or req.startTime or req.offset if req else None
    target_bookmark_id = req.bookmark_id or req.bookmarkId if req else None

    # Option A: Bookmark ID specified (e.g. created on ABS Mobile App)
    if target_bookmark_id:
        try:
            b_url = f"{target_server}/api/me/bookmarks"
            b_resp = _http_session.get(b_url, headers=headers, timeout=10)
            if b_resp.status_code == 200:
                b_data = b_resp.json()
                b_list = b_data.get("bookmarks") if isinstance(b_data, dict) else (b_data if isinstance(b_data, list) else [])
                found_b = next((b for b in b_list if b.get("id") == target_bookmark_id or str(b.get("time")) == str(target_bookmark_id)), None)
                if found_b:
                    target_lib_item_id = found_b.get("libraryItemId") or target_lib_item_id
                    target_offset = float(found_b.get("time") or 0.0)
        except Exception as b_err:
            logger.warning(f"Error querying bookmarks for target_bookmark_id: {b_err}")

    # Option B: Check active listening sessions
    current_time = None
    library_item_id = target_lib_item_id
    file_path = None
    book_title = "Unknown Book"
    subtitle = ""
    author = "Unknown Author"
    chapter_name = "Unknown Chapter"

    try:
        sessions_url = f"{target_server}/api/me/listening-sessions"
        resp = _http_session.get(sessions_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            sessions_data = resp.json()
            sessions = []
            if isinstance(sessions_data, dict):
                sessions = sessions_data.get("sessions") or sessions_data.get("items") or []
            elif isinstance(sessions_data, list):
                sessions = sessions_data

            if sessions:
                active_session = sessions[0]
                if not library_item_id:
                    library_item_id = active_session.get("libraryItemId")
                if target_offset is None:
                    current_time = float(active_session.get("currentTime") or 0.0)

                media_meta = (
                    active_session.get("mediaMetadata")
                    or active_session.get("media", {}).get("metadata", {})
                    or {}
                )
                display_title = active_session.get("displayTitle")
                book_title = media_meta.get("title") or display_title or book_title
                subtitle = media_meta.get("subtitle") or ""
                author = extract_authors(media_meta, author)

                # Chapters
                chapters = active_session.get("chapters") or active_session.get("media", {}).get("chapters") or []
                c_time = target_offset if target_offset is not None else (current_time or 0.0)
                for ch in chapters:
                    start = float(ch.get("start") or 0.0)
                    end = float(ch.get("end") or 0.0)
                    if start <= c_time <= end:
                        chapter_name = ch.get("title") or ch.get("name") or chapter_name
                        break

                # File path from session
                audio_track = active_session.get("audioTrack") or {}
                if isinstance(audio_track, dict):
                    file_path = audio_track.get("metadata", {}).get("path") or audio_track.get("path")
                if not file_path:
                    file_path = active_session.get("filePath") or active_session.get("path")
    except Exception as e:
        logger.warning(f"Failed to query active listening sessions: {e}")

    # Option C: Fallback to mediaProgress if no active session
    if not library_item_id or (target_offset is None and current_time is None):
        progress_list = user_info.get("mediaProgress", []) if user_info else []
        if not progress_list:
            try:
                me_res = _http_session.get(f"{target_server}/api/me", headers=headers, timeout=10)
                if me_res.status_code == 200:
                    me_data = me_res.json()
                    user_d = me_data.get("user", {}) if isinstance(me_data.get("user"), dict) else me_data
                    progress_list = user_d.get("mediaProgress", [])
            except Exception:
                pass

        if progress_list:
            progress_list.sort(key=lambda x: x.get("lastUpdate") or 0, reverse=True)
            latest = progress_list[0]
            if not library_item_id:
                library_item_id = latest.get("libraryItemId")
            if target_offset is None and current_time is None:
                current_time = float(latest.get("currentTime") or 0.0)

    # Use explicit offset if provided
    if target_offset is not None:
        current_time = float(target_offset)

    if current_time is None:
        current_time = 0.0

    if not library_item_id:
        raise HTTPException(
            status_code=404,
            detail="No active listening session or recent audiobook found on Audiobookshelf. Start playing an audiobook first or specify library_item_id."
        )

    # Resolve book item details and audio file path
    item_url = f"{target_server}/api/items/{library_item_id}?expanded=1"
    try:
        item_resp = _http_session.get(item_url, headers=headers, timeout=10)
        if item_resp.status_code == 200:
            item_data = item_resp.json()
            media = item_data.get("media", {})
            meta = media.get("metadata", {})

            if book_title == "Unknown Book":
                book_title = meta.get("title") or item_data.get("title") or book_title
            if not subtitle:
                subtitle = meta.get("subtitle") or ""
            if author == "Unknown Author":
                author = extract_authors(meta, author)

            # Match chapter if still unknown
            if chapter_name == "Unknown Chapter":
                chapters = media.get("chapters") or []
                for ch in chapters:
                    start = float(ch.get("start") or 0.0)
                    end = float(ch.get("end") or 0.0)
                    if start <= current_time <= end:
                        chapter_name = ch.get("title") or ch.get("name") or chapter_name
                        break

            # Find matching audio file
            audio_files = media.get("audioFiles") or media.get("tracks") or []
            if audio_files:
                selected_file = audio_files[0]
                for af in audio_files:
                    af_meta = af.get("metadata") or {}
                    af_start = float(af.get("startOffset") or 0.0)
                    af_dur = float(af.get("duration") or af_meta.get("duration") or 0.0)
                    if af_dur > 0 and af_start <= current_time <= (af_start + af_dur):
                        selected_file = af
                        break

                meta_path = selected_file.get("metadata", {}).get("path")
                direct_path = selected_file.get("path")
                meta_fn = selected_file.get("metadata", {}).get("filename") or selected_file.get("filename")
                media_path = media.get("path") or item_data.get("path")

                resolved_file_path = meta_path or direct_path
                if not resolved_file_path and media_path and meta_fn:
                    resolved_file_path = f"{media_path.rstrip('/')}/{meta_fn}"
                if not resolved_file_path:
                    resolved_file_path = media_path or file_path

                file_path = resolved_file_path or file_path
    except Exception as e:
        logger.warning(f"Failed to fetch item details for {library_item_id}: {e}")

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail=f"Could not determine source audio file path for library item '{library_item_id}'. Ensure the audio library is mounted."
        )

    # Translate Docker container path (/audiobooks/...) to host system path
    host_file_path = map_container_path_to_host(file_path, book_title=book_title)

    # Derive direct HTTP stream URL as fallback if file is not accessible on local disk
    stream_url = None
    if library_item_id and target_server:
        file_ino = None
        if 'selected_file' in locals() and selected_file:
            file_ino = selected_file.get("ino") or selected_file.get("id")
        if file_ino:
            stream_url = f"{target_server}/api/items/{library_item_id}/file/{file_ino}"
        else:
            stream_url = f"{target_server}/api/items/{library_item_id}/download"

    return {
        "libraryItemId": library_item_id,
        "currentTime": current_time,
        "file_path": host_file_path,
        "raw_container_path": file_path,
        "stream_url": stream_url,
        "book_title": book_title,
        "subtitle": subtitle,
        "author": author,
        "chapter_name": chapter_name,
        "startOffset": af_start if 'af_start' in locals() else 0.0
    }


def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from a Markdown file string."""
    data = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_frontmatter = parts[1]
            body = parts[2].strip()
            for line in raw_frontmatter.strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    val = v.strip().strip('"').strip("'")
                    data[k.strip()] = val
    data["body"] = body
    return data


# --- Core Audio Extraction & Transcription Logic (Thread-Safe & Shared) ---

def create_unextractable_bookmark_snippet(
    library_item_id: Optional[str],
    bookmark_data: Dict[str, Any],
    auth_token: Optional[str],
    server_url: Optional[str],
    error_reason: str,
    user_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Creates a persistent snippet (.md and .json) for a bookmark found in Audiobookshelf
    whose audio cannot be extracted (e.g. deleted/moved books, unmounted media folders).
    Includes Date/Time, Book metadata, Bookmark metadata (with 'N/A' for unavailable fields),
    and a clear explanation in the transcription box informing the user that this bookmark
    was found in the library, but could not be extracted and transcribed.
    """
    if not user_info:
        user_info = _last_authenticated_session.get("user") or {
            "id": "default_user",
            "username": os.environ.get("DEFAULT_USERNAME", "user"),
            "raw_token": auth_token or ""
        }

    user_id = str(user_info.get("id") or "user_id")
    username = str(user_info.get("username") or "user")
    safe_username = sanitize_filename(username)

    # 1. Resolve bookmark timing
    t_raw = bookmark_data.get("time")
    if t_raw is None:
        t_raw = bookmark_data.get("start_time") or bookmark_data.get("startTime") or bookmark_data.get("offset")
    try:
        current_time = float(t_raw) if t_raw is not None else 0.0
    except (ValueError, TypeError):
        current_time = 0.0

    bookmarked_duration_formatted = format_bookmarked_duration(current_time) if t_raw is not None else "N/A"

    # 2. Resolve creation date/time
    created_at_raw = bookmark_data.get("createdAt") or bookmark_data.get("created_at")
    formatted_datetime = None
    timestamp = None

    if created_at_raw:
        try:
            c_float = float(created_at_raw)
            if c_float > 1e11:  # Millisecond epoch timestamp
                dt_obj = datetime.fromtimestamp(c_float / 1000.0)
            else:
                dt_obj = datetime.fromtimestamp(c_float)
            formatted_datetime = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
            timestamp = dt_obj.strftime("%Y%m%d_%H%M%S")
        except Exception:
            if isinstance(created_at_raw, str) and len(created_at_raw) > 5:
                formatted_datetime = created_at_raw

    if not timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not formatted_datetime:
        formatted_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    bm_title = bookmark_data.get("title") or "N/A"
    resolved_lib_id = library_item_id or bookmark_data.get("libraryItemId") or "N/A"
    bm_id = bookmark_data.get("id")

    if is_bookmark_tombstoned(
        lib_id=resolved_lib_id,
        book_time=current_time,
        snippet_id=bm_id,
        title=bm_title,
        created_at=created_at_raw,
        book_title=bookmark_data.get("book_title")
    ):
        logger.info(f"Skipping creation of unextractable bookmark because it was deleted by user: lib={resolved_lib_id}, time={current_time}")
        return {"status": "skipped", "message": "Bookmark was previously deleted by user"}

    # 3. Attempt to fetch book metadata from Audiobookshelf if accessible
    book_title = "N/A"
    author = "N/A"
    chapter_name = "N/A"

    if resolved_lib_id and resolved_lib_id != "N/A" and server_url and auth_token:
        try:
            item_url = f"{server_url.rstrip('/')}/api/items/{resolved_lib_id}?expanded=1"
            headers = {"Authorization": f"Bearer {auth_token}"}
            item_resp = _http_session.get(item_url, headers=headers, timeout=5)
            if item_resp.status_code == 200:
                item_data = item_resp.json()
                media = item_data.get("media", {})
                meta = media.get("metadata", {})
                bt = meta.get("title") or item_data.get("title")
                if bt:
                    book_title = bt
                aut = extract_authors(meta, "N/A")
                if aut and aut != "Unknown Author":
                    author = aut
                chapters = media.get("chapters") or []
                for ch in chapters:
                    start = float(ch.get("start") or 0.0)
                    end = float(ch.get("end") or 0.0)
                    if start <= current_time <= end:
                        ch_title = ch.get("title") or ch.get("name")
                        if ch_title:
                            chapter_name = ch_title
                        break
        except Exception as query_err:
            logger.debug(f"Could not query item metadata from ABS for unextractable bookmark: {query_err}")

    # Fallback title if book was moved or deleted
    if book_title == "N/A":
        if bm_title and bm_title != "N/A":
            book_title_display = f"{bm_title} (Book Unavailable)"
            safe_book_title = sanitize_filename(bm_title)
        else:
            book_title_display = f"Unavailable Book ({resolved_lib_id[:8]})" if resolved_lib_id != "N/A" else "Unavailable Book"
            safe_book_title = sanitize_filename(book_title_display)
    else:
        book_title_display = book_title
        safe_book_title = sanitize_filename(book_title)

    # 4. Format metadata header preceding transcription box
    meta_header = (
        f"- Date / Time: {formatted_datetime}\n"
        f"- Book Title: {book_title_display}\n"
        f"- Author(s): {author}\n"
        f"- Bookmarked Duration: {bookmarked_duration_formatted}\n"
        f"- Snippet Length: N/A\n"
        f"- Chapter: {chapter_name}\n"
        f"- Bookmark Title: {bm_title}"
    )

    clean_reason = error_reason.strip() if error_reason else "Audio source file could not be located"
    if "detail=" in clean_reason:
        clean_reason = clean_reason.split("detail=")[-1].strip("'\"")

    notice_body = (
        f"[Notice: This bookmark was found in your Audiobookshelf library, but could not be extracted and transcribed.\n"
        f"Reason: {clean_reason}\n"
        f"Note: The audiobook or audio file may have been moved, deleted, or unmounted from the server.]"
    )

    full_transcript = f"{meta_header}\n\n{notice_body}"

    # 5. Determine target folder under user's bookmarks
    target_base = VOLUME_DIR
    target_user_name = safe_username
    for cand in get_candidate_volume_dirs():
        for u in [safe_username.lower(), safe_username]:
            check_path = os.path.join(cand, u, "bookmarks")
            if os.path.isdir(check_path):
                target_base = cand
                target_user_name = u
                break

    output_dir = os.path.join(target_base, target_user_name, "bookmarks", safe_book_title)
    os.makedirs(output_dir, exist_ok=True)

    output_md = os.path.join(output_dir, f"{timestamp}.md")
    output_json = os.path.join(output_dir, f"{timestamp}.json")

    md_content = f"""---
title: "{book_title_display}"
author: "{author}"
chapter: "{chapter_name}"
timestamp: "{timestamp}"
date_time: "{formatted_datetime}"
current_time: {current_time}
bookmarked_duration: "{bookmarked_duration_formatted}"
start_time: {current_time}
duration: 0
snippet_length: "N/A"
library_item_id: "{resolved_lib_id}"
user_id: "{user_id}"
username: "{username}"
transcription_engine: "N/A"
extraction_status: "unavailable"
bookmark_title: "{bm_title}"
---

# {book_title_display}

{meta_header}

---

## Transcribed Text

{notice_body}
"""
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    meta_content = {
        "id": f"{safe_book_title}-{timestamp}",
        "book_title": book_title_display,
        "author": author,
        "chapter": chapter_name,
        "timestamp": timestamp,
        "date_time": formatted_datetime,
        "start_time": current_time,
        "current_time": current_time,
        "bookmarked_duration": bookmarked_duration_formatted,
        "duration": 0,
        "snippet_length": "N/A",
        "library_item_id": resolved_lib_id,
        "user_id": user_id,
        "username": username,
        "transcript": full_transcript,
        "raw_transcript": notice_body,
        "audio_url": None,
        "md_url": f"/bookmarks/{target_user_name}/{safe_book_title}/{timestamp}.md",
        "file_path": output_md,
        "mp3_path": None,
        "json_path": output_json,
        "extraction_method": "intercepted",
        "created_at": formatted_datetime,
        "transcription_engine": "N/A",
        "extraction_status": "unavailable",
        "bookmark_title": bm_title
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(meta_content, f, indent=2)

    with _extractions_lock:
        _recent_extractions.append({
            "id": f"{safe_book_title}-{timestamp}",
            "book_title": book_title_display,
            "author": author,
            "chapter": chapter_name,
            "timestamp": timestamp,
            "username": username,
            "completed_at": datetime.now().isoformat(),
            "extraction_method": "intercepted",
            "extraction_status": "unavailable"
        })
        if len(_recent_extractions) > 100:
            _recent_extractions.pop(0)

    logger.info(f"Recorded unextractable bookmark fallback entry for '{book_title_display}' [{timestamp}]")
    return {
        "status": "unextractable_saved",
        "message": "Bookmark recorded as unextractable snippet",
        "snippet": meta_content
    }


def process_bookmark_extraction(
    library_item_id: Optional[str] = None,
    bookmark_data: Optional[Dict[str, Any]] = None,
    auth_token: Optional[str] = None,
    server_url: Optional[str] = None,
    duration: Optional[int] = None,
    snippet_request: Optional[SnippetRequest] = None,
    user_info: Optional[Dict[str, Any]] = None,
    custom_start: Optional[float] = None,
    custom_pre_roll: Optional[float] = None,
    is_intercepted: bool = False,
    replace_timestamp: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Core extraction function that handles audio clipping (ffmpeg), speech transcription
    (faster-whisper with Vosk fallback), and snippet metadata generation/storage.

    Called synchronously by manual UI/API endpoints and asynchronously by the
    middleware bookmark interceptor background worker.
    """
    # 1. Resolve auth token and server URL if attached to bookmark data
    if bookmark_data and not auth_token:
        auth_token = bookmark_data.get("_auth_token") or bookmark_data.get("auth_token") or bookmark_data.get("token")
    if bookmark_data and not server_url:
        server_url = bookmark_data.get("_server_url") or bookmark_data.get("server_url")

    target_server = resolve_abs_server_url(req_url=server_url)

    # 2. Authenticate user against ABS server if not already provided
    user = user_info
    if not user and auth_token:
        try:
            user = validate_abs_token(auth_token, server_url=target_server)
        except Exception as e:
            logger.warning(f"Failed to authenticate token during bookmark extraction: {e}")

    # Fallback to cached authenticated session if available
    if not user and _last_authenticated_session.get("user"):
        user = _last_authenticated_session["user"]
        if not auth_token:
            auth_token = _last_authenticated_session.get("token")
        logger.info(f"Using cached authenticated user '{user.get('username')}' for bookmark extraction.")

    if not user:
        # Fallback to username from bookmark data if present
        fallback_username = (
            (bookmark_data.get("username") if bookmark_data else None)
            or (bookmark_data.get("user") if bookmark_data else None)
            or os.environ.get("DEFAULT_USERNAME", "user")
        )
        fallback_id = (bookmark_data.get("userId") if bookmark_data else None) or "default_user"
        user = {
            "id": str(fallback_id),
            "username": str(fallback_username),
            "raw_token": auth_token or ""
        }
        logger.warning(f"Defaulting user to '{fallback_username}' for bookmark extraction.")

    user_id = user["id"]
    username = user["username"]
    safe_username = sanitize_filename(username)

    if duration is not None:
        effective_duration = int(duration)
    elif is_intercepted:
        effective_duration = INTERCEPT_SNIPPET_DURATION
    elif snippet_request and snippet_request.duration:
        effective_duration = int(snippet_request.duration)
    else:
        effective_duration = SNIPPET_DURATION

    # 3. Build or normalize snippet request
    if snippet_request is None:
        b_id = bookmark_data.get("id") if bookmark_data else None
        b_time = None
        if bookmark_data:
            b_time = bookmark_data.get("time")
            if b_time is None:
                b_time = bookmark_data.get("start_time") or bookmark_data.get("startTime") or bookmark_data.get("offset")

        lib_id = library_item_id or (bookmark_data.get("libraryItemId") if bookmark_data else None)
        snippet_request = SnippetRequest(
            library_item_id=str(lib_id) if lib_id else None,
            start_time=float(b_time) if b_time is not None else None,
            bookmark_id=str(b_id) if b_id else None,
            duration=effective_duration,
            server_url=target_server
        )

    # Check if this bookmark was previously deleted by user (tombstone)
    cand_lib_id = library_item_id or (bookmark_data.get("libraryItemId") if bookmark_data else None)
    cand_time = snippet_request.start_time
    cand_snip_id = snippet_request.bookmark_id
    cand_title = bookmark_data.get("title") if bookmark_data else None
    cand_created_at = (bookmark_data.get("createdAt") or bookmark_data.get("created_at")) if bookmark_data else None

    if is_bookmark_tombstoned(
        lib_id=cand_lib_id,
        book_time=cand_time,
        snippet_id=cand_snip_id,
        title=cand_title,
        created_at=cand_created_at
    ):
        logger.info(f"Skipping bookmark extraction because it was deleted by user: lib={cand_lib_id}, time={cand_time}")
        return {"status": "skipped", "message": "Bookmark was previously deleted by user"}

    # 4. Resolve target audio file, book metadata, and timestamp
    try:
        session_state = resolve_audio_target(
            token=auth_token or user.get("raw_token") or "",
            req=snippet_request,
            user_info=user,
            server_url=target_server
        )
    except Exception as target_err:
        if bookmark_data or is_intercepted:
            logger.warning(f"Could not resolve audio target ({target_err}). Saving unextractable bookmark fallback...")
            return create_unextractable_bookmark_snippet(
                library_item_id=library_item_id or (bookmark_data.get("libraryItemId") if bookmark_data else None),
                bookmark_data=bookmark_data or {},
                auth_token=auth_token or user.get("raw_token"),
                server_url=target_server,
                error_reason=str(target_err),
                user_info=user
            )
        raise

    current_time = session_state["currentTime"]
    file_path = session_state["file_path"]
    stream_url = session_state.get("stream_url")
    book_title = session_state["book_title"]
    subtitle = session_state.get("subtitle") or ""
    author = session_state["author"]
    chapter_name = session_state["chapter_name"]
    resolved_lib_item_id = session_state["libraryItemId"]
    start_offset = float(session_state.get("startOffset") or 0.0)

    # Re-verify tombstone with fully resolved current_time and book title
    if is_bookmark_tombstoned(
        lib_id=resolved_lib_item_id,
        book_time=float(current_time),
        snippet_id=cand_snip_id,
        title=cand_title,
        created_at=cand_created_at,
        book_title=book_title
    ):
        logger.info(f"Skipping bookmark extraction for '{book_title}' at {current_time}s because it was deleted by user")
        return {"status": "skipped", "message": "Bookmark was previously deleted by user"}

    # Combine book title and subtitle if applicable
    if subtitle and subtitle.strip() and subtitle.strip().lower() not in book_title.lower():
        full_book_title = f"{book_title}: {subtitle.strip()}"
    else:
        full_book_title = book_title

    # 5. Compute time window
    # For bookmark events, window is centered around the bookmark timestamp (e.g. -30s to +30s)
    if custom_start is not None:
        c_val = float(custom_start)
        start_time = max(0.0, c_val - start_offset) if c_val >= start_offset else max(0.0, c_val)
    elif snippet_request and (snippet_request.start_time is not None or snippet_request.startTime is not None) and not bookmark_data:
        req_start = float(snippet_request.start_time or snippet_request.startTime)
        start_time = max(0.0, req_start - start_offset) if req_start >= start_offset else max(0.0, req_start)
    else:
        file_relative_offset = max(0.0, current_time - start_offset)
        pre_roll_val = custom_pre_roll if custom_pre_roll is not None else (INTERCEPT_PRE_ROLL if is_intercepted else SNIPPET_PRE_ROLL)
        start_time = max(0.0, file_relative_offset - pre_roll_val)

    timestamp = replace_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")

    # 6. User-Specific Volume Folder Structure:
    # {VOLUME_DIR}/{username}/bookmarks/{safe_book_title}/
    safe_book_title = sanitize_filename(book_title)

    # Check candidate volume dirs to find where user's bookmarks already live, or use primary VOLUME_DIR
    target_base = VOLUME_DIR
    target_user_name = safe_username
    found_existing_user_dir = False

    for cand in get_candidate_volume_dirs():
        for u in [safe_username.lower(), safe_username]:
            check_path = os.path.join(cand, u, "bookmarks")
            if os.path.isdir(check_path):
                target_base = cand
                target_user_name = u
                found_existing_user_dir = True
                break
        if found_existing_user_dir:
            break

    output_dir = os.path.join(target_base, target_user_name, "bookmarks", safe_book_title)
    os.makedirs(output_dir, exist_ok=True)

    output_mp3 = os.path.join(output_dir, f"{timestamp}.mp3")
    output_md = os.path.join(output_dir, f"{timestamp}.md")
    output_json = os.path.join(output_dir, f"{timestamp}.json")

    # When re-clipping or updating an existing snippet, delete the old files first
    # to guarantee clean replacement and ensure ffmpeg creates a fresh stream
    if replace_timestamp:
        for stale_file in [output_mp3, output_md, output_json]:
            if os.path.exists(stale_file):
                try:
                    os.remove(stale_file)
                    logger.info(f"Unlinked stale file to prepare for clean re-clipping: {stale_file}")
                except Exception as del_err:
                    logger.warning(f"Could not remove stale file {stale_file}: {del_err}")

    # 7. ffmpeg Subprocess Call
    ffmpeg_bin = get_ffmpeg_bin()
    use_stream = False
    input_target = file_path

    if not os.path.exists(file_path):
        logger.warning(f"Audio file '{file_path}' was not found on local host disk.")
        if stream_url:
            logger.info(f"Fallback: Slicing audio directly from Audiobookshelf HTTP stream: {stream_url}")
            input_target = stream_url
            use_stream = True
        else:
            missing_err = (
                f"Audio file '{file_path}' does not exist on host disk and no stream URL could be resolved. "
                f"The audiobook may have been moved, deleted, or unmounted."
            )
            if bookmark_data or is_intercepted:
                logger.warning(f"{missing_err}. Saving unextractable bookmark fallback...")
                return create_unextractable_bookmark_snippet(
                    library_item_id=resolved_lib_item_id,
                    bookmark_data=bookmark_data or {"title": full_book_title, "time": current_time},
                    auth_token=auth_token or user.get("raw_token"),
                    server_url=target_server,
                    error_reason=missing_err,
                    user_info=user
                )
            raise RuntimeError(missing_err)

    if not use_stream:
        is_mp3_source = file_path.lower().endswith(".mp3")
        need_encoding = not is_mp3_source

        if is_mp3_source:
            ffmpeg_cmd = [
                ffmpeg_bin,
                "-y",
                "-ss", str(start_time),
                "-i", file_path,
                "-t", str(effective_duration),
                "-c", "copy",
                output_mp3
            ]
            logger.info(f"Executing ffmpeg (mp3 stream copy): {' '.join(ffmpeg_cmd)}")
            try:
                proc = run_low_priority_ffmpeg(ffmpeg_cmd)
                if proc.returncode != 0 or not os.path.exists(output_mp3) or os.path.getsize(output_mp3) == 0:
                    logger.info(f"mp3 stream copy unviable (exit {proc.returncode}). Re-encoding with libmp3lame...")
                    need_encoding = True
            except FileNotFoundError:
                raise RuntimeError(
                    "ffmpeg is not installed or not found on the host system PATH. "
                    "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
                )

        if need_encoding:
            encode_cmd = [
                ffmpeg_bin,
                "-y",
                "-ss", str(start_time),
                "-i", file_path,
                "-t", str(effective_duration),
                "-vn",
                "-c:a", "libmp3lame",
                "-q:a", "2",
                output_mp3
            ]
            ext = os.path.splitext(file_path)[1]
            logger.info(f"Executing ffmpeg (converting {ext} to mp3): {' '.join(encode_cmd)}")
            try:
                proc2 = run_low_priority_ffmpeg(encode_cmd)
                if proc2.returncode != 0:
                    logger.error(f"ffmpeg encoding failed: {proc2.stderr}")
                    raise RuntimeError(
                        f"ffmpeg audio extraction failed: {proc2.stderr[-300:] if proc2.stderr else 'Unknown error'}"
                    )
            except FileNotFoundError:
                raise RuntimeError(
                    "ffmpeg is not installed or not found on the host system PATH. "
                    "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
                )
    else:
        # Slicing directly from Audiobookshelf HTTP API stream
        stream_cmd = [
            ffmpeg_bin,
            "-y",
            "-headers", f"Authorization: Bearer {auth_token}\r\n",
            "-ss", str(start_time),
            "-i", input_target,
            "-t", str(effective_duration),
            "-vn",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            output_mp3
        ]
        logger.info(f"Executing ffmpeg over HTTP stream: {' '.join(stream_cmd)}")
        try:
            proc_stream = run_low_priority_ffmpeg(stream_cmd)
            if proc_stream.returncode != 0 or not os.path.exists(output_mp3) or os.path.getsize(output_mp3) == 0:
                logger.error(f"ffmpeg HTTP stream extraction failed: {proc_stream.stderr}")
                raise RuntimeError(
                    f"Local file '{file_path}' was not found and HTTP streaming failed: {proc_stream.stderr[-300:] if proc_stream.stderr else 'Unknown error'}. "
                    f"Please verify AUDIOBOOKS_PATH in ecosystem.config.cjs."
                )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg is not installed or not found on the host system PATH. "
                "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
            )

    # 8. Transcription (faster-whisper primary with Vosk backup)
    transcript_body = ""
    engine_used = "faster-whisper"
    try:
        whisper = get_whisper_model()
        logger.info(f"Transcribing {output_mp3} with faster-whisper ({WHISPER_MODEL_NAME})...")
        segments, _ = whisper.transcribe(output_mp3, beam_size=5)
        text_segments = [segment.text.strip() for segment in segments]
        transcript_body = " ".join(text_segments).strip()
        logger.info(f"Transcription complete: {len(transcript_body)} characters")
    except Exception as whisper_err:
        logger.warning(f"faster-whisper transcription failed ({whisper_err}). Attempting Vosk backup...")
        try:
            transcript_body = transcribe_with_vosk(output_mp3)
            engine_used = "vosk"
            logger.info(f"Vosk backup transcription complete: {len(transcript_body)} characters")
        except Exception as vosk_err:
            logger.error(f"Transcription failed on both engines: Whisper ({whisper_err}), Vosk ({vosk_err})")
            transcript_body = f"[Transcription failed: Whisper ({str(whisper_err)}); Vosk ({str(vosk_err)})]"
            engine_used = "failed"

    # 9. Format Metadata Header & Markdown with frontmatter
    formatted_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bookmarked_duration_formatted = format_bookmarked_duration(current_time)
    snippet_length_formatted = f"{int(round(effective_duration))} seconds"
    chapter_display = chapter_name if (chapter_name and chapter_name != "Unknown Chapter") else "N/A"

    meta_header = (
        f"- Date / Time: {formatted_datetime}\n"
        f"- Book Title: {full_book_title}\n"
        f"- Author(s): {author}\n"
        f"- Bookmarked Duration: {bookmarked_duration_formatted}\n"
        f"- Snippet Length: {snippet_length_formatted}\n"
        f"- Chapter: {chapter_display}"
    )
    full_transcript = f"{meta_header}\n\n{transcript_body}"

    md_content = f"""---
title: "{full_book_title}"
author: "{author}"
chapter: "{chapter_display}"
timestamp: "{timestamp}"
date_time: "{formatted_datetime}"
current_time: {current_time}
bookmarked_duration: "{bookmarked_duration_formatted}"
start_time: {start_time}
duration: {effective_duration}
snippet_length: "{snippet_length_formatted}"
library_item_id: "{resolved_lib_item_id}"
user_id: "{user_id}"
username: "{username}"
transcription_engine: "{engine_used}"
---

# {full_book_title}

{meta_header}

---

## Transcribed Text

{transcript_body}
"""
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 10. Write JSON metadata file for indexing
    meta_content = {
        "id": f"{safe_book_title}-{timestamp}",
        "book_title": full_book_title,
        "author": author,
        "chapter": chapter_display,
        "timestamp": timestamp,
        "date_time": formatted_datetime,
        "start_time": start_time,
        "current_time": current_time,
        "bookmarked_duration": bookmarked_duration_formatted,
        "duration": effective_duration,
        "snippet_length": snippet_length_formatted,
        "library_item_id": resolved_lib_item_id,
        "user_id": user_id,
        "username": username,
        "transcript": full_transcript,
        "raw_transcript": transcript_body,
        "audio_url": f"/bookmarks/{target_user_name}/{safe_book_title}/{timestamp}.mp3?v={int(os.path.getmtime(output_mp3)) if os.path.exists(output_mp3) else int(time.time())}",
        "md_url": f"/bookmarks/{target_user_name}/{safe_book_title}/{timestamp}.md",
        "file_path": output_md,
        "mp3_path": output_mp3,
        "json_path": output_json,
        "extraction_method": "intercepted" if bookmark_data else "manual",
        "created_at": formatted_datetime,
        "transcription_engine": engine_used
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(meta_content, f, indent=2)

    # Record event in thread-safe recent list for real-time notification
    with _extractions_lock:
        _recent_extractions.append({
            "id": f"{safe_book_title}-{timestamp}",
            "book_title": full_book_title,
            "author": author,
            "chapter": chapter_display,
            "timestamp": timestamp,
            "username": username,
            "completed_at": datetime.now().isoformat(),
            "extraction_method": "intercepted" if (bookmark_data or is_intercepted) else "manual"
        })
        if len(_recent_extractions) > 100:
            _recent_extractions.pop(0)

    logger.info(f"Successfully processed bookmark extraction for '{full_book_title}' [{timestamp}] by user '{username}'")

    return {
        "status": "success",
        "message": "Bookmark audio extracted and transcribed successfully",
        "user": {
            "id": user_id,
            "username": username,
            "folder": f"{safe_username}/bookmarks"
        },
        "snippet": {
            "id": f"{safe_book_title}-{timestamp}",
            "book_title": full_book_title,
            "author": author,
            "chapter": chapter_display,
            "timestamp": timestamp,
            "date_time": formatted_datetime,
            "start_time": start_time,
            "current_time": current_time,
            "bookmarked_duration": bookmarked_duration_formatted,
            "duration": effective_duration,
            "snippet_length": snippet_length_formatted,
            "mp3_file": output_mp3,
            "md_file": output_md,
            "transcript": full_transcript,
            "raw_transcript": transcript_body,
            "audio_url": f"/bookmarks/{safe_username}/{safe_book_title}/{timestamp}.mp3?v={int(os.path.getmtime(output_mp3)) if os.path.exists(output_mp3) else int(time.time())}",
            "md_url": f"/bookmarks/{safe_username}/{safe_book_title}/{timestamp}.md"
        }
    }


# ==============================================================================
# Autonomous Background Bookmark Sync Engine
# Continuously queries Audiobookshelf for bookmarks created on Android/iOS/Web
# and extracts/transcribes any that do not yet exist on disk without requiring
# any reverse proxy modifications.
# ==============================================================================

_snippet_meta_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_snippet_cache_lock = threading.Lock()


def get_cached_existing_extractions(safe_username: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Returns existing extractions with mtime-based in-memory caching.
    Prevents repeated file opens and JSON parsing of hundreds of files on the SSD during every sync loop.
    """
    global _snippet_meta_cache
    existing_extractions = []

    for root in get_candidate_volume_dirs():
        for u in [safe_username, safe_username.lower(), str(user_id)]:
            for sub in [os.path.join(root, u, "bookmarks"), os.path.join(root, u)]:
                if not os.path.isdir(sub):
                    continue
                try:
                    book_dirs = os.listdir(sub)
                except Exception:
                    continue

                for book_dir in book_dirs:
                    full_b_dir = os.path.join(sub, book_dir)
                    if not os.path.isdir(full_b_dir) or book_dir.lower() in ("bookmarks", "snippets"):
                        continue
                    try:
                        filenames = os.listdir(full_b_dir)
                    except Exception:
                        continue

                    for f in filenames:
                        if f.endswith(".json") and not f.startswith("."):
                            full_path = os.path.join(full_b_dir, f)
                            try:
                                mtime = os.path.getmtime(full_path)
                                with _snippet_cache_lock:
                                    if full_path in _snippet_meta_cache and _snippet_meta_cache[full_path][0] == mtime:
                                        meta = _snippet_meta_cache[full_path][1]
                                    else:
                                        with open(full_path, "r", encoding="utf-8") as jf:
                                            meta = json.load(jf)
                                        _snippet_meta_cache[full_path] = (mtime, meta)

                                existing_extractions.append({
                                    "library_item_id": meta.get("library_item_id"),
                                    "current_time": float(meta.get("current_time", -999)),
                                    "start_time": float(meta.get("start_time", -999)),
                                    "duration": float(meta.get("duration", 60))
                                })
                            except Exception:
                                pass
    return existing_extractions


def run_bookmark_sync_cycle(force_token: Optional[str] = None, force_server: Optional[str] = None) -> Dict[str, Any]:
    """
    Scans Audiobookshelf for bookmarks created on mobile/web apps and automatically extracts
    and transcribes any that do not yet exist on disk in the user's bookmarks folder.
    Runs asynchronously in the background without needing reverse proxy interception on port 13380.
    """
    global _sync_state
    with _sync_lock:
        if _sync_state.get("is_syncing", False):
            return {
                "status": "in_progress",
                "message": "A sync cycle is already currently running.",
                "current_item": _sync_state.get("current_item")
            }
        _sync_state["is_syncing"] = True

    try:
        token = force_token or ABS_API_TOKEN or _last_authenticated_session.get("token")
        target_server = resolve_abs_server_url(req_url=force_server)

        if not token:
            persisted = load_sync_session()
            if persisted and persisted.get("token"):
                token = persisted["token"]
                if persisted.get("server_url") and not force_server:
                    target_server = resolve_abs_server_url(req_url=persisted["server_url"])

        if not token:
            _sync_state["is_syncing"] = False
            return {
                "status": "idle",
                "message": "No active authentication. Log into the Web Dashboard once or set ABS_API_TOKEN in ecosystem.config.cjs to enable autonomous 24/7 sync.",
                "unextracted_count": 0,
                "processed_count": 0
            }

        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Query user data from Audiobookshelf
        url = f"{target_server}/api/me"
        resp = _http_session.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            err_msg = f"Audiobookshelf server at {target_server} returned HTTP {resp.status_code}"
            logger.warning(f"[Auto-Sync] {err_msg}")
            _sync_state["last_error"] = err_msg
            _sync_state["is_syncing"] = False
            return {"status": "error", "message": err_msg}

        user_resp = resp.json()
        user_info = user_resp.get("user") if isinstance(user_resp.get("user"), dict) else user_resp
        username = user_info.get("username") or user_info.get("name") or "user"
        user_id = user_info.get("id") or "user_id"

        # Cache credentials for ongoing daemon use
        _last_authenticated_session["token"] = token
        _last_authenticated_session["user"] = {
            "id": str(user_id),
            "username": str(username),
            "raw_token": token,
            "server_url": target_server
        }
        save_sync_session(token, target_server, _last_authenticated_session["user"])

        # Collect bookmarks from user profile, media progress, and active listening sessions
        candidate_bookmarks = []
        seen_keys = set()
        skipped_prior_count = 0
        skipped_tombstone_count = 0

        def _add_candidate(bm_data: Dict[str, Any], default_lib_id: Optional[str] = None):
            nonlocal skipped_prior_count, skipped_tombstone_count
            if not isinstance(bm_data, dict):
                return
            t_raw = bm_data.get("time")
            if t_raw is None:
                t_raw = bm_data.get("start_time") or bm_data.get("startTime") or bm_data.get("offset")
            if t_raw is None:
                return
            try:
                bm_time = float(t_raw)
            except (ValueError, TypeError):
                return

            lib_id = bm_data.get("libraryItemId") or default_lib_id
            if not lib_id:
                return

            # Check immutable installation cutoff (bookmarks created prior to installation at 0:00 are skipped)
            created_at_raw = bm_data.get("createdAt") or bm_data.get("created_at") or bm_data.get("timestamp")
            if not is_bookmark_after_installation_cutoff(created_at_raw):
                skipped_prior_count += 1
                return

            # Check if user previously deleted this bookmark (tombstone)
            bm_id = bm_data.get("id")
            bm_title = bm_data.get("title") or ""
            if is_bookmark_tombstoned(
                lib_id=lib_id,
                book_time=bm_time,
                snippet_id=bm_id,
                title=bm_title,
                created_at=created_at_raw
            ):
                skipped_tombstone_count += 1
                return

            key = (str(lib_id), round(bm_time, 1))
            if key in seen_keys:
                return
            seen_keys.add(key)

            candidate_bookmarks.append({
                "id": bm_id,
                "libraryItemId": str(lib_id),
                "time": bm_time,
                "title": bm_data.get("title") or "",
                "createdAt": created_at_raw
            })

        # 1. User bookmarks
        for bm in (user_info.get("bookmarks") or []):
            _add_candidate(bm)

        # 2. Bookmarks in mediaProgress
        for prog in (user_info.get("mediaProgress") or []):
            lib_id = prog.get("libraryItemId")
            for bm in (prog.get("bookmarks") or []):
                _add_candidate(bm, default_lib_id=lib_id)

        # 3. Active listening sessions
        try:
            sess_resp = _http_session.get(f"{target_server}/api/me/listening-sessions", headers=headers, timeout=10)
            if sess_resp.status_code == 200:
                s_data = sess_resp.json()
                s_list = s_data if isinstance(s_data, list) else (s_data.get("sessions") or [])
                for s in s_list:
                    lib_id = s.get("libraryItemId") or s.get("id")
                    for bm in (s.get("bookmarks") or []):
                        _add_candidate(bm, default_lib_id=lib_id)
        except Exception:
            pass

        _sync_state["skipped_before_cutoff"] = skipped_prior_count
        _sync_state["skipped_tombstoned"] = skipped_tombstone_count
        _sync_state["installation_date"] = INSTALLATION_CONFIG.get("installation_date")
        _sync_state["cutoff_datetime"] = INSTALLATION_CONFIG.get("cutoff_datetime")
        _sync_state["cutoff_mode"] = INSTALLATION_CONFIG.get("cutoff_mode", "from_now")
        _sync_state["custom_cutoff_date"] = INSTALLATION_CONFIG.get("custom_date")
        _sync_state["installed_at"] = INSTALLATION_CONFIG.get("installed_at")

        if not candidate_bookmarks:
            _sync_state["last_synced_at"] = datetime.now().isoformat()
            _sync_state["is_syncing"] = False
            mode = INSTALLATION_CONFIG.get("cutoff_mode", "from_now")
            if mode == "from_start":
                cutoff_desc = "beginning of server"
            elif mode == "custom_date":
                cutoff_desc = f"{INSTALLATION_CONFIG.get('custom_date')} at 00:00"
            else:
                cutoff_desc = f"{INSTALLATION_CONFIG.get('installation_date', 'installation date')} at 00:00"

            msg = (
                f"No bookmarks found created on or after {cutoff_desc} "
                f"({skipped_prior_count} historical bookmarks skipped)."
                if skipped_prior_count > 0 else
                "No bookmarks found on Audiobookshelf server."
            )
            return {
                "status": "ok",
                "message": msg,
                "total_bookmarks": 0,
                "skipped_before_cutoff": skipped_prior_count,
                "skipped_tombstoned": skipped_tombstone_count,
                "cutoff_mode": mode,
                "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
                "installation_date": INSTALLATION_CONFIG.get("installation_date"),
                "custom_date": INSTALLATION_CONFIG.get("custom_date"),
                "unextracted_count": 0,
                "processed_count": 0
            }

        # Scan local disk for existing extractions using mtime-cached metadata
        safe_username = sanitize_filename(username)
        existing_extractions = get_cached_existing_extractions(safe_username, str(user_id))

        # Identify unextracted bookmarks
        unextracted = []
        for cand in candidate_bookmarks:
            c_lib_id = cand["libraryItemId"]
            c_time = cand["time"]

            is_already = False
            for ext in existing_extractions:
                if ext["library_item_id"] and ext["library_item_id"] == c_lib_id:
                    if abs(ext["current_time"] - c_time) <= 15.0:
                        is_already = True
                        break
                    if ext["start_time"] <= c_time <= (ext["start_time"] + ext["duration"]):
                        is_already = True
                        break
                elif abs(ext["current_time"] - c_time) <= 8.0:
                    is_already = True
                    break

            if not is_already:
                unextracted.append(cand)

        if not unextracted:
            _sync_state["last_synced_at"] = datetime.now().isoformat()
            _sync_state["is_syncing"] = False
            return {
                "status": "ok",
                "message": f"All {len(candidate_bookmarks)} Audiobookshelf bookmarks are already synchronized.",
                "total_bookmarks": len(candidate_bookmarks),
                "unextracted_count": 0,
                "processed_count": 0
            }

        logger.info(f"[Auto-Sync] Found {len(unextracted)} unextracted bookmark(s) on Audiobookshelf for '{username}'. Processing in background...")

        processed_count = 0
        for idx, bm in enumerate(unextracted):
            lib_id = bm["libraryItemId"]
            b_time = bm["time"]
            b_title = bm["title"] or f"Bookmark @ {format_bookmarked_duration(b_time)}"

            _sync_state["current_item"] = f"[{idx + 1}/{len(unextracted)}] {b_title}"

            try:
                res = process_bookmark_extraction(
                    library_item_id=lib_id,
                    bookmark_data=bm,
                    auth_token=token,
                    server_url=target_server,
                    duration=INTERCEPT_SNIPPET_DURATION,
                    custom_pre_roll=INTERCEPT_PRE_ROLL,
                    is_intercepted=True
                )
                processed_count += 1
                _sync_state["total_synced"] += 1
                logger.info(f"[Auto-Sync] [✓] Successfully extracted '{b_title}'")
            except Exception as ex:
                logger.error(f"[Auto-Sync] [✗] Failed extracting bookmark {bm}: {ex}")
                logger.info(f"[Auto-Sync] Creating unextractable fallback bookmark for '{b_title}'...")
                try:
                    res = create_unextractable_bookmark_snippet(
                        library_item_id=lib_id,
                        bookmark_data=bm,
                        auth_token=token,
                        server_url=target_server,
                        error_reason=str(ex),
                        user_info=user_info
                    )
                    processed_count += 1
                    _sync_state["total_synced"] += 1
                    logger.info(f"[Auto-Sync] [✓] Recorded unextractable bookmark fallback for '{b_title}'")
                except Exception as fb_err:
                    logger.error(f"[Auto-Sync] [✗] Could not write unextractable fallback: {fb_err}")

            # Sleep 1.5s between jobs to throttle server CPU/load
            time.sleep(1.5)

        _sync_state["last_synced_at"] = datetime.now().isoformat()
        _sync_state["current_item"] = None
        _sync_state["is_syncing"] = False

        return {
            "status": "success",
            "message": f"Auto-Sync completed. Processed {processed_count} of {len(unextracted)} bookmarks created on or after {INSTALLATION_CONFIG.get('installation_date')} ({skipped_prior_count} older bookmarks preserved/skipped).",
            "total_bookmarks": len(candidate_bookmarks),
            "skipped_before_cutoff": skipped_prior_count,
            "skipped_tombstoned": skipped_tombstone_count,
            "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
            "installation_date": INSTALLATION_CONFIG.get("installation_date"),
            "unextracted_count": len(unextracted),
            "processed_count": processed_count
        }

    except Exception as e:
        logger.error(f"[Auto-Sync] Sync cycle failed: {e}", exc_info=True)
        _sync_state["last_error"] = str(e)
        _sync_state["current_item"] = None
        _sync_state["is_syncing"] = False
        return {"status": "error", "message": str(e)}


async def background_bookmark_sync_daemon():
    """
    Continuous background loop that automatically checks for new Audiobookshelf bookmarks
    every BOOKMARK_SYNC_INTERVAL seconds. Runs autonomously without requiring reverse proxy changes.
    """
    logger.info(f"[Auto-Sync] Background daemon started (Interval: {BOOKMARK_SYNC_INTERVAL}s, Enabled: {AUTO_SYNC_BOOKMARKS})")
    await asyncio.sleep(5)  # Allow server initialization
    while True:
        try:
            if AUTO_SYNC_BOOKMARKS and not _sync_state.get("is_syncing", False):
                await asyncio.to_thread(run_bookmark_sync_cycle)
        except Exception as e:
            logger.warning(f"[Auto-Sync Daemon] Loop exception: {e}")
        await asyncio.sleep(BOOKMARK_SYNC_INTERVAL)


class AbsSocketIoListener:
    """
    Autonomous, non-intrusive background Socket.IO client for Audiobookshelf.
    Connects directly to Audiobookshelf's WebSocket server as a passive event listener.
    Zero reverse proxy overhead: operates in parallel without touching mobile or web client sockets.
    Listens for bookmark and item update events to trigger instant extraction with zero latency.
    """
    def __init__(self):
        self._running = False
        self._connected = False
        self._wake_event = asyncio.Event()
        self._debounce_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def wake(self):
        """Wakes up the listener if waiting for credentials or triggers an immediate sync."""
        self._wake_event.set()

    def _schedule_sync(self):
        """Debounces event triggers so multiple updates in 2 seconds only launch a single sync cycle."""
        async def _debounced():
            await asyncio.sleep(2.0)
            if not _sync_state.get("is_syncing", False):
                logger.info("[Socket.IO Listener] Received real-time bookmark/library event from Audiobookshelf. Triggering extraction...")
                await asyncio.to_thread(run_bookmark_sync_cycle)

        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(_debounced())

    async def start(self):
        """Main background loop handling connection, heartbeat, and real-time events with robust backoff."""
        self._running = True
        logger.info("[Socket.IO Listener] Background listener initialized.")
        await asyncio.sleep(6)  # Give Audiobookshelf and sidecar time to settle

        backoff_seconds = 5.0

        while self._running:
            session = load_sync_session()
            token = session.get("token") if session else None
            server_url = session.get("server_url") if session else None

            if not token or not server_url:
                # Check memory cache fallback
                token = _last_authenticated_session.get("token")
                server_url = ABS_TARGET_SERVER

            if not token or not server_url:
                # Wait until session credentials are saved
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass
                continue

            if websockets is None:
                logger.warning("[Socket.IO Listener] websockets package is not installed; falling back to periodic sync polling only.")
                await asyncio.sleep(60)
                continue

            ws_url = server_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
            # Audiobookshelf allows token in query params as well as auth/header
            socket_url = f"{ws_url}/socket.io/?EIO=4&transport=websocket&token={quote_plus(str(token))}"

            connect_kwargs = {
                "ping_interval": None,
                "ping_timeout": None,
                "max_size": 10 * 1024 * 1024,
            }

            headers_dict = {
                "Authorization": f"Bearer {token}",
                "Origin": server_url.rstrip("/"),
                "User-Agent": "Audiobookshelf-Bookmarks-Manager/2.0.0",
            }

            connected_at = 0.0
            disconnect_reason = "Connection closed"

            try:
                masked_url = socket_url.split("&token=")[0]
                logger.info(f"[Socket.IO Listener] Connecting to Audiobookshelf at {masked_url}...")
                try:
                    connect_cm = websockets.connect(socket_url, additional_headers=headers_dict, **connect_kwargs)
                except TypeError:
                    connect_cm = websockets.connect(socket_url, extra_headers=headers_dict, **connect_kwargs)

                async with connect_cm as ws:
                    self._connected = True
                    connected_at = time.time()
                    logger.info("[Socket.IO Listener] Connected to Audiobookshelf WebSocket. Awaiting Engine.IO handshake...")

                    # 1. First packet from server MUST be Engine.IO OPEN (starts with '0')
                    try:
                        first_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        if isinstance(first_msg, bytes):
                            first_msg = first_msg.decode("utf-8", errors="ignore")
                    except Exception as e:
                        disconnect_reason = f"Timeout or error awaiting Engine.IO open packet: {e}"
                        logger.warning(f"[Socket.IO Listener] {disconnect_reason}")
                        continue

                    if not first_msg.startswith("0"):
                        disconnect_reason = f"Unexpected initial packet from server: {first_msg[:50]}"
                        logger.warning(f"[Socket.IO Listener] {disconnect_reason}")
                        continue

                    logger.debug(f"[Socket.IO Listener] Engine.IO open received: {first_msg[:60]}")

                    # 2. Send Socket.IO CONNECT packet to '/' namespace (packet '40')
                    await ws.send("40")

                    # 3. Emit Audiobookshelf 'auth' event (packet '42["auth","<token>"]')
                    await ws.send(f'42["auth","{token}"]')
                    logger.info("[Socket.IO Listener] Handshake complete and auth event emitted.")

                    # Message listening loop
                    while self._running:
                        try:
                            msg = await ws.recv()
                        except Exception as recv_err:
                            close_code = getattr(ws, "close_code", None)
                            close_reason = getattr(ws, "close_reason", None)
                            disconnect_reason = f"Recv error ({recv_err}), code: {close_code}, reason: {close_reason}"
                            break

                        if isinstance(msg, bytes):
                            try:
                                msg = msg.decode("utf-8", errors="ignore")
                            except Exception:
                                continue

                        if not msg:
                            continue

                        # Engine.IO ping -> respond with pong '3'
                        if msg == "2":
                            await ws.send("3")
                            continue
                        elif msg.startswith("2"):
                            await ws.send("3" + msg[1:])
                            continue

                        # Socket.IO event: starts with '42'
                        if msg.startswith("42"):
                            try:
                                payload = json.loads(msg[2:])
                                if isinstance(payload, list) and len(payload) > 0:
                                    event_name = str(payload[0]).lower()

                                    # Check for auth rejection
                                    if event_name == "auth_failed":
                                        err_detail = payload[1] if len(payload) > 1 else "Invalid or expired token"
                                        logger.warning(f"[Socket.IO Listener] Authentication rejected by Audiobookshelf: {err_detail}. Re-authenticate via UI.")
                                        disconnect_reason = "Authentication rejected"
                                        break

                                    # Events of interest: user updates, bookmarks, progress, sessions, items
                                    if any(k in event_name for k in ["bookmark", "user_updated", "user_item", "item_updated", "session"]):
                                        logger.info(f"[Socket.IO Listener] Audiobookshelf real-time event received: {event_name}")
                                        self._schedule_sync()
                            except Exception as parse_err:
                                logger.debug(f"[Socket.IO Listener] Failed parsing payload: {parse_err}")

            except Exception as conn_err:
                disconnect_reason = f"Connection error: {conn_err}"
            finally:
                self._connected = False

            # Calculate how long the connection lasted
            duration = time.time() - connected_at if connected_at > 0 else 0
            if duration > 45:
                # Connection was healthy for over 45 seconds, reset backoff
                backoff_seconds = 5.0
            else:
                # Failed quickly, increase backoff up to 60s
                backoff_seconds = min(backoff_seconds * 1.8, 60.0)

            logger.info(f"[Socket.IO Listener] {disconnect_reason}. Reconnecting in {int(backoff_seconds)}s...")

            # GUARANTEED BACKOFF WAIT (Cannot be bypassed)
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=backoff_seconds)
            except asyncio.TimeoutError:
                pass


_socket_listener = AbsSocketIoListener()


@app.post("/api/user/sync-bookmarks")
@app.post("/api/sync-bookmarks")
async def trigger_bookmark_sync(
    request: Request,
    raw_token: Optional[str] = Depends(extract_token_flexible)
):
    """
    Triggers an immediate background sync check for the user's bookmarks across Audiobookshelf.
    Extracts and transcribes any newly detected bookmarks without requiring reverse proxy changes.
    """
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    if "_socket_listener" in globals() and _socket_listener is not None:
        _socket_listener.wake()
    result = await asyncio.to_thread(run_bookmark_sync_cycle, force_token=raw_token, force_server=server_url)
    return result


@app.get("/api/user/sync-status")
@app.get("/api/sync-status")
async def get_sync_status():
    """
    Returns the current status of the automated background bookmark sync daemon and real-time socket listener.
    """
    return {
        "status": "ok",
        "enabled": AUTO_SYNC_BOOKMARKS,
        "interval_seconds": BOOKMARK_SYNC_INTERVAL,
        "socket_connected": _socket_listener.is_connected if "_socket_listener" in globals() else False,
        "state": _sync_state
    }


# --- Manual Trigger & REST API Endpoints (Web App, Scripts, Automation) ---

@app.post("/api/snippet")
@app.post("/api/extract")
@app.post("/api/bookmark/extract")
async def create_snippet_or_bookmark(
    request: Request,
    payload: Optional[SnippetRequest] = None,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    Extracts an audio snippet and transcribes it synchronously upon explicit request.
    Can be called by:
    - Web Dashboard
    - Automation webhooks, scripts, or curl
    """
    server_url = resolve_abs_server_url(
        req_url=(payload.server_url or payload.serverUrl or payload.abs_server_url or payload.absServerUrl) if payload else None,
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    user = validate_abs_token(raw_token, server_url=server_url)

    custom_start = (payload.start_time or payload.startTime or payload.offset) if payload else None
    duration = payload.duration if payload and payload.duration else SNIPPET_DURATION

    try:
        result = process_bookmark_extraction(
            library_item_id=payload.library_item_id if payload else None,
            auth_token=raw_token,
            server_url=server_url,
            duration=duration,
            snippet_request=payload,
            user_info=user,
            custom_start=float(custom_start) if custom_start is not None else None
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during snippet extraction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/bookmarks")
@app.get("/api/snippets")
async def get_user_bookmarks(
    request: Request,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    JSON API endpoint callable by the Web UI, mobile players, and external scripts.
    Returns all bookmarks, clips, and transcripts belonging strictly to the authenticated user.
    Scans across all candidate volume directories and case variations (e.g. 'john' and 'John').
    """
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    user = validate_abs_token(raw_token, server_url=server_url)
    username = user["username"]
    safe_username = sanitize_filename(username)
    user_id = user["id"]

    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user_id]))
    bookmarks = []
    seen_ids = set()

    # Search each candidate volume directory for user folders
    for root in candidate_roots:
        for u in user_search_names:
            # Possible directory locations:
            # 1. {root}/{u}/bookmarks/{book_dir}
            # 2. {root}/{u}/{book_dir}
            possible_user_dirs = [
                os.path.join(root, u, "bookmarks"),
                os.path.join(root, u)
            ]

            for u_dir in possible_user_dirs:
                if not os.path.isdir(u_dir):
                    continue

                for book_dir in sorted(os.listdir(u_dir)):
                    # Avoid recursing into 'bookmarks' folder itself if scanning root/{u}
                    if book_dir.lower() in ("bookmarks", "snippets"):
                        continue
                    full_book_path = os.path.join(u_dir, book_dir)
                    if not os.path.isdir(full_book_path):
                        continue

                    for fname in sorted(os.listdir(full_book_path), reverse=True):
                        if fname.endswith(".md"):
                            base_name = fname[:-3]
                            item_unique_key = f"{book_dir.lower()}-{base_name}"
                            if item_unique_key in seen_ids:
                                continue

                            md_path = os.path.join(full_book_path, fname)
                            mp3_path = os.path.join(full_book_path, f"{base_name}.mp3")
                            json_path = os.path.join(full_book_path, f"{base_name}.json")

                            metadata = {}
                            if os.path.exists(json_path):
                                try:
                                    with open(json_path, "r", encoding="utf-8") as jf:
                                        metadata = json.load(jf)
                                except Exception:
                                    pass

                            if not metadata:
                                try:
                                    with open(md_path, "r", encoding="utf-8") as f:
                                        raw_md = f.read()
                                    parsed = parse_frontmatter(raw_md)
                                    metadata = {
                                        "book_title": parsed.get("title") or book_dir.replace("_", " "),
                                        "author": parsed.get("author") or "Unknown Author",
                                        "chapter": parsed.get("chapter") or "",
                                        "start_time": float(parsed.get("start_time") or 0.0),
                                        "duration": int(parsed.get("duration") or 60),
                                        "transcript": parsed.get("body", "").split("## Transcript", 1)[-1].strip()
                                    }
                                except Exception:
                                    metadata = {"book_title": book_dir, "transcript": ""}

                            has_mp3 = os.path.exists(mp3_path)
                            transcript_text = metadata.get("transcript", "")
                            if transcript_text and "- Date / Time:" not in transcript_text and "Date / Time:" not in transcript_text:
                                date_str = metadata.get("date_time") or metadata.get("created_at") or base_name
                                cur_t = metadata.get("current_time", metadata.get("start_time", 0.0))
                                b_dur = metadata.get("bookmarked_duration") or format_bookmarked_duration(cur_t)
                                s_len = metadata.get("snippet_length") or f"{metadata.get('duration', 60)} seconds"
                                ch_str = metadata.get("chapter") or "N/A"
                                header = (
                                    f"- Date / Time: {date_str}\n"
                                    f"- Book Title: {metadata.get('book_title') or book_dir}\n"
                                    f"- Author(s): {metadata.get('author') or 'Unknown Author'}\n"
                                    f"- Bookmarked Duration: {b_dur}\n"
                                    f"- Snippet Length: {s_len}\n"
                                    f"- Chapter: {ch_str}\n\n"
                                )
                                transcript_text = f"{header}{transcript_text}"

                            cur_time_val = float(metadata.get("current_time", float(metadata.get("start_time", 0.0)) + (float(metadata.get("duration", 60)) / 2.0)))
                            full_id = f"{book_dir}-{base_name}"
                            if is_bookmark_tombstoned(
                                lib_id=metadata.get("library_item_id"),
                                book_time=cur_time_val,
                                snippet_id=full_id,
                                title=metadata.get("title") or metadata.get("chapter"),
                                created_at=metadata.get("created_at") or metadata.get("date_time"),
                                book_title=metadata.get("book_title") or book_dir
                            ):
                                continue

                            seen_ids.add(item_unique_key)
                            mp3_mtime = int(os.path.getmtime(mp3_path)) if (has_mp3 and os.path.exists(mp3_path)) else int(time.time())
                            bookmarks.append({
                                "id": full_id,
                                "book_title": metadata.get("book_title") or book_dir,
                                "author": metadata.get("author") or "Unknown Author",
                                "chapter": metadata.get("chapter") or "",
                                "timestamp": base_name,
                                "start_time": float(metadata.get("start_time", 0.0)),
                                "current_time": cur_time_val,
                                "duration": int(metadata.get("duration", 60)),
                                "library_item_id": metadata.get("library_item_id") or "",
                                "transcript": transcript_text,
                                "audio_url": f"/bookmarks/{u}/{book_dir}/{base_name}.mp3?v={mp3_mtime}" if has_mp3 else None,
                                "md_url": f"/bookmarks/{u}/{book_dir}/{fname}",
                                "file_path": md_path,
                                "mp3_path": mp3_path if has_mp3 else None,
                                "username": username,
                                "extraction_method": metadata.get("extraction_method", "intercepted"),
                                "extraction_status": metadata.get("extraction_status") or ("success" if has_mp3 else "unavailable"),
                                "created_at": metadata.get("created_at") or base_name
                            })

    return {
        "status": "success",
        "username": username,
        "count": len(bookmarks),
        "bookmarks": bookmarks
    }


@app.get("/api/user/bookmarks/status")
@app.get("/api/snippets/status")
async def get_bookmarks_status(
    request: Request,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    Returns the real-time extraction completion status for the authenticated user.
    Used by the Web UI to automatically detect and notify completed bookmarks without requiring manual reload.
    """
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    user = validate_abs_token(raw_token, server_url=server_url)
    username = user["username"]

    with _extractions_lock:
        user_events = [e for e in _recent_extractions if e.get("username", "").lower() == username.lower()]

    return {
        "status": "ok",
        "username": username,
        "total_recent": len(user_events),
        "recent": user_events[-10:] if user_events else [],
        "sync_state": _sync_state
    }


@app.post("/api/snippet/expand")
@app.post("/api/snippet/update")
async def expand_or_update_snippet(
    request: Request,
    payload: SnippetExpandRequest,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    Adjusts and expands an existing snippet with new pre-roll and post-roll durations.
    Re-clips the audio and re-runs transcription, replacing the previous version in-place.
    """
    server_url = resolve_abs_server_url(
        req_url=payload.server_url or payload.serverUrl,
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    user = validate_abs_token(raw_token, server_url=server_url)
    username = user["username"]
    safe_username = sanitize_filename(username)

    target_ts = payload.timestamp.strip()
    pre_roll = float(payload.preRoll if payload.preRoll is not None else (payload.pre_roll or 30.0))
    post_roll = float(payload.postRoll if payload.postRoll is not None else (payload.post_roll or 60.0))
    total_duration = max(5, int(round(pre_roll + post_roll)))

    cur_time = payload.currentTime if payload.currentTime is not None else payload.current_time
    lib_id = payload.libraryItemId or payload.library_item_id

    # If anchor timestamp or library item id is missing, look up existing snippet JSON metadata
    if cur_time is None or not lib_id:
        for root in get_candidate_volume_dirs():
            for u in [safe_username, safe_username.lower(), user["id"]]:
                u_dir = os.path.join(root, u, "bookmarks")
                if not os.path.isdir(u_dir):
                    u_dir = os.path.join(root, u)
                if os.path.isdir(u_dir):
                    for b_dir in os.listdir(u_dir):
                        json_file = os.path.join(u_dir, b_dir, f"{target_ts}.json")
                        if os.path.isfile(json_file):
                            try:
                                with open(json_file, "r", encoding="utf-8") as jf:
                                    existing_meta = json.load(jf)
                                    if cur_time is None:
                                        cur_time = existing_meta.get("current_time", existing_meta.get("start_time", 0.0) + 30.0)
                                    if not lib_id:
                                        lib_id = existing_meta.get("library_item_id")
                            except Exception:
                                pass
                            break

    if cur_time is None:
        cur_time = 0.0

    new_start_time = max(0.0, float(cur_time) - pre_roll)

    logger.info(f"Expanding snippet [{target_ts}] for @{username}: anchor={cur_time}s, pre_roll={pre_roll}s, post_roll={post_roll}s (start={new_start_time}s, duration={total_duration}s)")

    # Execute extraction with replace_timestamp so old snippet is overwritten in-place
    result = process_bookmark_extraction(
        library_item_id=lib_id,
        auth_token=raw_token,
        server_url=server_url,
        duration=total_duration,
        user_info=user,
        custom_start=new_start_time,
        replace_timestamp=target_ts
    )
    return result


@app.get("/api/cutoff-config")
@app.get("/api/user/cutoff-config")
@app.get("/api/installation-date")
@app.get("/api/user/installation-date")
async def get_cutoff_configuration():
    """
    Returns the current bookmark cutoff configuration including mode:
    'from_start', 'custom_date', or 'from_now' (installation date).
    """
    return {
        "status": "success",
        "cutoff_mode": INSTALLATION_CONFIG.get("cutoff_mode", "from_now"),
        "custom_date": INSTALLATION_CONFIG.get("custom_date"),
        "installation_date": INSTALLATION_CONFIG.get("installation_date"),
        "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
        "cutoff_timestamp": INSTALLATION_CONFIG.get("cutoff_timestamp"),
        "installed_at": INSTALLATION_CONFIG.get("installed_at"),
        "config": INSTALLATION_CONFIG
    }


@app.post("/api/cutoff-config")
@app.post("/api/user/cutoff-config")
async def update_cutoff_configuration(
    payload: CutoffConfigRequest,
    request: Request,
    raw_token: Optional[str] = Depends(extract_token_flexible)
):
    """
    Allows the admin to configure the bookmark cutoff period.
    Three options:
    1. 'from_start': Processes all bookmarks from the beginning of server history.
    2. 'custom_date': Processes bookmarks on or after YYYY/MM/DD or YYYY-MM-DD.
    3. 'from_now': Processes bookmarks from current installation date (or resets to now).
    """
    mode = (payload.cutoff_mode or "from_now").strip().lower()
    if mode not in ("from_start", "custom_date", "from_now"):
        raise HTTPException(
            status_code=400,
            detail="Invalid cutoff_mode. Must be 'from_start', 'custom_date', or 'from_now'."
        )

    updated_config: Dict[str, Any] = {
        "cutoff_mode": mode
    }

    if mode == "from_start":
        updated_config["cutoff_timestamp"] = 0.0
        updated_config["cutoff_datetime"] = "1970-01-01T00:00:00"
        updated_config["custom_date"] = None
        updated_config["note"] = "Extracting all bookmarks from the beginning of the server."

    elif mode == "custom_date":
        raw_d = (payload.custom_date or "").strip()
        if not raw_d:
            raise HTTPException(status_code=400, detail="custom_date is required when cutoff_mode is 'custom_date'.")
        
        # Standardize date separators (supports YYYY/MM/DD, YYYY.MM.DD, or YYYY-MM-DD)
        clean_d = re.sub(r'[/.]', '-', raw_d)[:10]
        try:
            dt_obj = datetime.strptime(clean_d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid custom_date format. Please use YYYY-MM-DD or YYYY/MM/DD.")

        cutoff_dt = datetime(dt_obj.year, dt_obj.month, dt_obj.day, 0, 0, 0)
        updated_config["custom_date"] = clean_d
        updated_config["cutoff_datetime"] = f"{clean_d}T00:00:00"
        updated_config["cutoff_timestamp"] = cutoff_dt.timestamp()
        updated_config["note"] = f"Extracting bookmarks created on or after {clean_d} at 00:00."

    elif mode == "from_now":
        # Uses installation date or resets to system date
        inst_date = INSTALLATION_CONFIG.get("installation_date")
        if not inst_date:
            inst_date = datetime.now().strftime("%Y-%m-%d")
            updated_config["installation_date"] = inst_date
        clean_d = inst_date[:10]
        try:
            parts = [int(p) for p in clean_d.split("-")]
            cutoff_dt = datetime(parts[0], parts[1], parts[2], 0, 0, 0)
            cutoff_ts = cutoff_dt.timestamp()
        except Exception:
            cutoff_dt = datetime.now()
            cutoff_ts = cutoff_dt.timestamp()

        updated_config["custom_date"] = None
        updated_config["cutoff_datetime"] = f"{clean_d}T00:00:00"
        updated_config["cutoff_timestamp"] = cutoff_ts
        updated_config["note"] = f"Extracting bookmarks created on or after installation date ({clean_d})."

    # Save to disk and update globals
    save_installation_config(updated_config)
    logger.info(f"[Cutoff Config] Successfully updated cutoff period mode='{mode}', cutoff='{updated_config.get('cutoff_datetime')}'")

    return {
        "status": "success",
        "message": f"Cutoff period updated to mode '{mode}' ({updated_config.get('cutoff_datetime')})",
        "config": INSTALLATION_CONFIG
    }


@app.get("/api/export-book")
@app.get("/api/user/bookmarks/export-book")
@app.get("/api/snippets/export-book")
@app.get("/api/book/export")
async def export_book_snippets(
    book_title: str = Query(..., description="Title of the book to export"),
    format: str = Query("zip", description="Export format: 'zip' or 'markdown'"),
    token: Optional[str] = Query(None, description="Audiobookshelf Bearer token via query parameter"),
    request: Request = None,
    raw_token: Optional[str] = Depends(extract_token_flexible)
):
    """
    Exports all snippets and bookmarks from the specified book at once.
    Provides either a complete ZIP archive (MP3 clips + Markdown notes + JSON) or a single combined Markdown document.
    """
    import io
    import zipfile

    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL") if request else None,
        query_url=(request.query_params.get("server_url") or request.query_params.get("serverUrl")) if request else None
    )
    eff_token = raw_token or token or _last_authenticated_session.get("token")
    user = None
    if eff_token:
        try:
            user = validate_abs_token(eff_token, server_url=server_url)
        except Exception as auth_err:
            logger.warning(f"Export auth token validation failed: {auth_err}. Falling back to active session.")

    if not user:
        user = _last_authenticated_session.get("user") or {
            "id": "default_user",
            "username": os.environ.get("DEFAULT_USERNAME", "user"),
            "raw_token": eff_token or ""
        }

    username = user["username"]
    safe_username = sanitize_filename(username)
    safe_book_title = sanitize_filename(book_title)

    # Resilient book directory resolution across candidate storage roots
    book_dir_path = None
    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user.get("id", ""), "default_user"]))
    clean_target = re.sub(r'[^a-zA-Z0-9]+', '', book_title).lower()

    for root in candidate_roots:
        possible_user_dirs = []
        for u in user_search_names:
            possible_user_dirs.extend([
                os.path.join(root, u, "bookmarks"),
                os.path.join(root, u)
            ])
        possible_user_dirs.append(root)

        for p_dir in possible_user_dirs:
            if not os.path.isdir(p_dir):
                continue

            # 1. Direct path matches
            cand1 = os.path.join(p_dir, safe_book_title)
            if os.path.isdir(cand1):
                book_dir_path = cand1
                break
            cand2 = os.path.join(p_dir, book_title)
            if os.path.isdir(cand2):
                book_dir_path = cand2
                break

            # 2. Case-insensitive and cleaned token matching
            for b_entry in os.listdir(p_dir):
                full_entry_path = os.path.join(p_dir, b_entry)
                if not os.path.isdir(full_entry_path):
                    continue
                if b_entry.lower() == safe_book_title.lower() or b_entry.lower() == book_title.lower():
                    book_dir_path = full_entry_path
                    break
                clean_entry = re.sub(r'[^a-zA-Z0-9]+', '', b_entry).lower()
                if clean_entry and (clean_entry == clean_target or clean_target.startswith(clean_entry) or clean_entry.startswith(clean_target[:20])):
                    book_dir_path = full_entry_path
                    break

            if book_dir_path:
                break
        if book_dir_path:
            break

    # 3. Deep walk fallback if folder structure differs
    if not book_dir_path:
        for root in candidate_roots:
            for dirpath, dirnames, filenames in os.walk(root):
                folder_name = os.path.basename(dirpath)
                clean_folder = re.sub(r'[^a-zA-Z0-9]+', '', folder_name).lower()
                if clean_target and clean_folder and (clean_folder in clean_target or clean_target in clean_folder):
                    if any(f.endswith(".md") or f.endswith(".mp3") for f in filenames):
                        book_dir_path = dirpath
                        break
            if book_dir_path:
                break

    if not book_dir_path or not os.path.isdir(book_dir_path):
        raise HTTPException(status_code=404, detail=f"No snippets found for book '{book_title}'")

    if format.lower() in ("markdown", "md"):
        md_files = sorted([f for f in os.listdir(book_dir_path) if f.endswith(".md")])
        if not md_files:
            raise HTTPException(status_code=404, detail="No markdown notes found for this book")

        combined_lines = [
            f"# {book_title} - All Bookmarks & Transcripts\n",
            f"*Exported on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} for @{username}*\n\n---\n"
        ]
        for idx, md_f in enumerate(md_files, 1):
            with open(os.path.join(book_dir_path, md_f), "r", encoding="utf-8") as f:
                content = f.read().strip()
            combined_lines.append(f"## Bookmark {idx} ({md_f[:-3]})\n\n{content}\n\n---\n")

        combined_text = "\n".join(combined_lines)
        filename = f"{safe_book_title}_All_Snippets.md"
        return Response(
            content=combined_text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    # Default: ZIP archive containing all audio and notes
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        md_files = []
        for fname in sorted(os.listdir(book_dir_path)):
            fpath = os.path.join(book_dir_path, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, arcname=f"{safe_book_title}/{fname}")
                if fname.endswith(".md"):
                    md_files.append(fname)

        if md_files:
            summary_lines = [
                f"# {book_title} - All Notes Summary\n",
                f"*Exported on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} for @{username}*\n\n"
            ]
            for idx, md_f in enumerate(md_files, 1):
                with open(os.path.join(book_dir_path, md_f), "r", encoding="utf-8") as f:
                    summary_lines.append(f"### {idx}. {md_f[:-3]}\n\n{f.read().strip()}\n\n---\n")
            zf.writestr(f"{safe_book_title}/ALL_NOTES_COMBINED.md", "\n".join(summary_lines))

    zip_bytes = zip_buffer.getvalue()
    filename = f"{safe_book_title}_All_Snippets.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.delete("/api/user/bookmarks/{snippet_id:path}")
@app.delete("/api/snippets/{snippet_id:path}")
async def delete_user_bookmark(
    snippet_id: str,
    request: Request,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    Permanently deletes a snippet/bookmark and its associated .mp3, .md, and .json files from the server.
    Scans all candidate volume roots and user folder variants.
    Persistently records a tombstone so the bookmark is never recreated on server sync or update.
    """
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    user = validate_abs_token(raw_token, server_url=server_url)
    username = user["username"]
    safe_username = sanitize_filename(username)
    user_id = user["id"]

    # Extract metadata sent from the web frontend
    req_lib_id = request.query_params.get("library_item_id")
    req_time_str = request.query_params.get("time")
    req_start_str = request.query_params.get("start_time")
    req_book_title = request.query_params.get("book_title")
    req_created_at = request.query_params.get("created_at")
    req_timestamp = request.query_params.get("timestamp")

    resolved_time = None
    if req_time_str:
        try:
            resolved_time = float(req_time_str)
        except Exception:
            pass
    resolved_start = None
    if req_start_str:
        try:
            resolved_start = float(req_start_str)
        except Exception:
            pass

    deleted_count = 0
    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user_id]))

    clean_id = snippet_id.strip().strip("/")
    target_pattern = clean_id[2:] if clean_id.startswith("b-") else clean_id

    # Normalized variants for robust matching against filenames & folder names
    id_variants = {
        clean_id.lower(),
        clean_id.lower().replace(" ", "_"),
        clean_id.lower().replace("_", " "),
        target_pattern.lower(),
        target_pattern.lower().replace(" ", "_"),
        target_pattern.lower().replace("_", " "),
    }
    if req_timestamp:
        id_variants.add(req_timestamp.lower())

    found_metadata: Dict[str, Any] = {}

    for root in candidate_roots:
        for u in user_search_names:
            search_dirs = [
                os.path.join(root, u, "bookmarks"),
                os.path.join(root, u)
            ]
            for base_dir in search_dirs:
                if not os.path.isdir(base_dir):
                    continue
                for book_dir in os.listdir(base_dir):
                    full_book_path = os.path.join(base_dir, book_dir)
                    if not os.path.isdir(full_book_path):
                        continue

                    # First check any companion .json files in this folder to extract metadata before deleting
                    for fname in os.listdir(full_book_path):
                        base_name = os.path.splitext(fname)[0]
                        full_id = f"{book_dir}-{base_name}"
                        
                        is_match = (
                            clean_id in (full_id, base_name, fname) or
                            target_pattern in (base_name, fname) or
                            full_id.lower() in id_variants or
                            base_name.lower() in id_variants or
                            (req_timestamp and req_timestamp in base_name)
                        )

                        if is_match and fname.endswith(".json") and not found_metadata:
                            json_candidate = os.path.join(full_book_path, fname)
                            try:
                                with open(json_candidate, "r", encoding="utf-8") as jf:
                                    found_metadata = json.load(jf)
                            except Exception:
                                pass

                    # Now remove all matching companion files (.md, .mp3, .json)
                    for fname in os.listdir(full_book_path):
                        base_name = os.path.splitext(fname)[0]
                        full_id = f"{book_dir}-{base_name}"

                        is_match = (
                            clean_id in (full_id, base_name, fname) or
                            target_pattern in (base_name, fname) or
                            full_id.lower() in id_variants or
                            base_name.lower() in id_variants or
                            (req_timestamp and req_timestamp in base_name)
                        )

                        if is_match:
                            file_to_del = os.path.join(full_book_path, fname)
                            try:
                                os.remove(file_to_del)
                                deleted_count += 1
                                logger.info(f"Deleted bookmark file: {file_to_del}")
                            except Exception as e:
                                logger.warning(f"Could not delete {file_to_del}: {e}")

                    # Clean up empty book directory if all files deleted
                    try:
                        if os.path.isdir(full_book_path) and not os.listdir(full_book_path):
                            os.rmdir(full_book_path)
                    except Exception:
                        pass

    # Extract final values for persistent tombstone
    final_lib_id = req_lib_id or found_metadata.get("library_item_id")
    final_time = resolved_time if resolved_time is not None else found_metadata.get("current_time", found_metadata.get("start_time"))
    if final_time is not None:
        try:
            final_time = float(final_time)
        except Exception:
            final_time = None
    final_start = resolved_start if resolved_start is not None else found_metadata.get("start_time")
    if final_start is not None:
        try:
            final_start = float(final_start)
        except Exception:
            final_start = None

    final_book_title = req_book_title or found_metadata.get("book_title")
    final_title = found_metadata.get("title") or found_metadata.get("bookmark_title")
    final_created_at = req_created_at or found_metadata.get("created_at") or found_metadata.get("date_time")
    final_timestamp = req_timestamp or found_metadata.get("timestamp") or clean_id

    # Record tombstone across all persistent file paths
    record_deleted_tombstone(
        snippet_id=clean_id,
        lib_id=final_lib_id,
        book_time=final_time,
        start_time=final_start,
        current_time=final_time,
        book_title=final_book_title,
        title=final_title,
        created_at=final_created_at,
        timestamp=final_timestamp
    )

    # Best-effort attempt to remove the bookmark from upstream Audiobookshelf server if permitted
    if final_lib_id and final_lib_id not in ("N/A", "unknown") and final_time is not None and server_url and raw_token:
        try:
            abs_del_url = f"{server_url.rstrip('/')}/api/me/bookmark/{final_lib_id}/{int(final_time)}"
            _http_session.delete(abs_del_url, headers={"Authorization": f"Bearer {raw_token}"}, timeout=4)
            logger.info(f"Notified ABS server to delete bookmark in item {final_lib_id} at {int(final_time)}s")
        except Exception as abs_err:
            logger.debug(f"Upstream ABS server delete response notice: {abs_err}")

    return {
        "status": "success",
        "deleted_id": snippet_id,
        "files_removed": deleted_count,
        "tombstone_recorded": True
    }


# --- Static Audio & Markdown File Serving ---

@app.get("/bookmarks/{username}/{book_title}/{filename}")
@app.get("/snippets/{username}/{book_title}/{filename}")
async def serve_bookmark_file(username: str, book_title: str, filename: str):
    """
    Serves the generated MP3 audio clip, Markdown transcript, or JSON metadata.
    Searches across all candidate volume roots and case variations.
    Supports HTTP Range requests so audio players and web browsers can stream audio smoothly with seeking.
    """
    safe_username = sanitize_filename(username)
    safe_book_title = sanitize_filename(book_title)
    safe_filename = os.path.basename(filename)

    candidate_roots = get_candidate_volume_dirs()
    user_variants = list(dict.fromkeys([safe_username, safe_username.lower(), safe_username.capitalize()]))

    file_path = None
    for root in candidate_roots:
        if not os.path.isdir(root):
            continue
        # Also discover any folder in root matching case-insensitively
        for existing in os.listdir(root):
            if existing.lower() == safe_username.lower() and existing not in user_variants:
                user_variants.append(existing)

        for u in user_variants:
            # 1. Primary: {root}/{u}/bookmarks/{book_title}/{filename}
            p = os.path.join(root, u, "bookmarks", safe_book_title, safe_filename)
            if os.path.isfile(p):
                file_path = p
                break

            # 2. Case-insensitive and space/underscore book folder check under bookmarks/
            bm_parent = os.path.join(root, u, "bookmarks")
            if os.path.isdir(bm_parent):
                for b_sub in os.listdir(bm_parent):
                    sub_norm = b_sub.replace("_", " ").strip().lower()
                    req_norm = safe_book_title.replace("_", " ").strip().lower()
                    if sub_norm == req_norm or b_sub.lower() == safe_book_title.lower():
                        p_sub = os.path.join(bm_parent, b_sub, safe_filename)
                        if os.path.isfile(p_sub):
                            file_path = p_sub
                            break
            if file_path:
                break

            # 3. Direct: {root}/{u}/{book_title}/{filename}
            p = os.path.join(root, u, safe_book_title, safe_filename)
            if os.path.isfile(p):
                file_path = p
                break
            # 3b. Case/space direct check
            u_dir = os.path.join(root, u)
            if os.path.isdir(u_dir):
                for b_sub in os.listdir(u_dir):
                    if b_sub.lower() in ("bookmarks", "snippets"):
                        continue
                    sub_norm = b_sub.replace("_", " ").strip().lower()
                    req_norm = safe_book_title.replace("_", " ").strip().lower()
                    if sub_norm == req_norm or b_sub.lower() == safe_book_title.lower():
                        p_sub = os.path.join(u_dir, b_sub, safe_filename)
                        if os.path.isfile(p_sub):
                            file_path = p_sub
                            break
            if file_path:
                break

            # 4. Snippets fallback: {root}/snippets/{u}/{book_title}/{filename}
            p = os.path.join(root, "snippets", u, safe_book_title, safe_filename)
            if os.path.isfile(p):
                file_path = p
                break
        if file_path:
            break

    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Requested audio or transcript file was not found")

    media_type = "audio/mpeg" if safe_filename.endswith(".mp3") else ("application/json" if safe_filename.endswith(".json") else "text/markdown")
    response = FileResponse(
        file_path,
        media_type=media_type,
        filename=safe_filename
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# --- Embedded Web Dashboard & Transparent Proxy Engine ---

async def render_extractor_dashboard(
    request: Request,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None)
) -> Response:
    """
    Renders the HTML bookmark extractor and audio player dashboard for the authenticated user,
    discovering bookmarks across all candidate volume locations.
    """
    auth_token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            auth_token = parts[1]
        elif len(parts) == 1:
            auth_token = parts[0]

    if not auth_token and token:
        auth_token = token
    if not auth_token:
        auth_token = request.cookies.get("abs_token")
    if not auth_token and _last_authenticated_session.get("token"):
        auth_token = _last_authenticated_session["token"]

    user = None
    error_msg = None

    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )

    if auth_token:
        try:
            user = validate_abs_token(auth_token, server_url=server_url)
        except HTTPException as e:
            error_msg = e.detail
            auth_token = None

    snippets = []
    if user:
        safe_username = sanitize_filename(user["username"])
        candidate_roots = get_candidate_volume_dirs()
        user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user["id"]]))
        seen_ids = set()

        for root in candidate_roots:
            for u in user_search_names:
                possible_dirs = [
                    os.path.join(root, u, "bookmarks"),
                    os.path.join(root, u)
                ]
                for scan_dir in possible_dirs:
                    if not os.path.isdir(scan_dir):
                        continue

                    for book_dir in sorted(os.listdir(scan_dir)):
                        if book_dir.lower() in ("bookmarks", "snippets"):
                            continue
                        full_book_path = os.path.join(scan_dir, book_dir)
                        if not os.path.isdir(full_book_path):
                            continue

                        for fname in sorted(os.listdir(full_book_path), reverse=True):
                            if fname.endswith(".md"):
                                base_name = fname[:-3]
                                key = f"{book_dir.lower()}-{base_name}"
                                if key in seen_ids:
                                    continue

                                md_path = os.path.join(full_book_path, fname)
                                mp3_path = os.path.join(full_book_path, f"{base_name}.mp3")

                                try:
                                    with open(md_path, "r", encoding="utf-8") as f:
                                        raw_md = f.read()
                                    parsed = parse_frontmatter(raw_md)
                                except Exception:
                                    parsed = {"body": ""}

                                created_time = base_name
                                try:
                                    mtime = os.path.getmtime(md_path)
                                    created_time = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %I:%M %p")
                                except Exception:
                                    pass

                                transcript_text = parsed.get("body", "")
                                if "## Transcript" in transcript_text:
                                    transcript_text = transcript_text.split("## Transcript", 1)[1].strip()

                                has_mp3 = os.path.exists(mp3_path)
                                seen_ids.add(key)
                                snippets.append({
                                    "title": parsed.get("title") or book_dir.replace("_", " "),
                                    "author": parsed.get("author") or "Unknown Author",
                                    "chapter": parsed.get("chapter") or "",
                                    "timestamp": parsed.get("timestamp") or base_name,
                                    "created_at_str": created_time,
                                    "duration": parsed.get("duration", "60"),
                                    "transcript": transcript_text,
                                    "audio_url": f"/bookmarks/{u}/{book_dir}/{base_name}.mp3" if has_mp3 else None,
                                    "md_url": f"/bookmarks/{u}/{book_dir}/{fname}"
                                })

    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "token": auth_token or "",
            "abs_server_url": ABS_SERVER_URL,
            "snippets": snippets,
            "error": error_msg
        }
    )

    if user and auth_token and not request.cookies.get("abs_token"):
        response.set_cookie(key="abs_token", value=auth_token, httponly=True, samesite="lax", max_age=86400 * 30)

    return response


@app.get("/extractor", response_class=HTMLResponse)
@app.get("/extractor/", response_class=HTMLResponse)
@app.get("/bookmarks-ui", response_class=HTMLResponse)
@app.get("/sidecar-ui", response_class=HTMLResponse)
async def web_dashboard(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    Dedicated dashboard view for audio bookmark management and transcription playback.
    """
    return await render_extractor_dashboard(request, token=token, authorization=authorization)


@app.get("/logout")
async def logout():
    """Clear session cookie and redirect to extractor dashboard."""
    response = RedirectResponse(url="/extractor", status_code=303)
    response.delete_cookie(key="abs_token")
    return response


@app.get("/api/installation-date")
@app.get("/api/user/installation-date")
async def get_installation_date():
    """Returns the immutable installation date and bookmark sync cutoff configuration."""
    return {
        "status": "success",
        "installation_date": INSTALLATION_CONFIG.get("installation_date"),
        "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
        "cutoff_timestamp": INSTALLATION_CONFIG.get("cutoff_timestamp"),
        "config": INSTALLATION_CONFIG
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint providing configuration, listener mode, and system status."""
    return {
        "status": "healthy",
        "service": "Audiobookshelf Bookmarks Manager",
        "tagline": "Autonomous manager and sidecar for Audiobookshelf with real-time Socket.IO listener & automated bookmark clipping",
        "version": "2.0.0",
        "architecture_mode": "Zero-Proxy Event-Driven Sidecar (Port 13380)",
        "abs_target_server": ABS_TARGET_SERVER,
        "volume_dir": VOLUME_DIR,
        "candidate_volume_dirs": get_candidate_volume_dirs(),
        "whisper_model": WHISPER_MODEL_NAME,
        "whisper_device": WHISPER_DEVICE,
        "socket_connected": _socket_listener.is_connected if "_socket_listener" in globals() else False,
        "installation_date": INSTALLATION_CONFIG.get("installation_date"),
        "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
        "sync_cutoff_rule": f"Extracts bookmarks created on or after {INSTALLATION_CONFIG.get('cutoff_datetime', 'installation')} only",
        "time": datetime.now().isoformat()
    }


# --- Dedicated Root View (Zero-Proxy Architecture) ---

@app.get("/", response_class=HTMLResponse)
async def root_view(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    Renders the Bookmarks Extractor & Sidecar Dashboard on the root URL.
    Zero-Proxy sidecar mode: Does not proxy Audiobookshelf traffic.
    """
    return await render_extractor_dashboard(request, token=token, authorization=authorization)


if __name__ == "__main__":
    import uvicorn
    # Default sidecar port is 13380 (SIDECAR_PORT is prioritized over PORT so it does not conflict if PORT is set to 13379 for the web dashboard)
    port = int(os.environ.get("SIDECAR_PORT") or os.environ.get("PORT") or "13380")
    # Do not reload by default to avoid watching parent directories (e.g. /home/pi)
    reload_enabled = os.environ.get("RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload_enabled,
        reload_dirs=[BASE_DIR] if reload_enabled else None,
        app_dir=BASE_DIR
    )
