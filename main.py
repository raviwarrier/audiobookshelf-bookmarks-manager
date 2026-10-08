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
from contextlib import asynccontextmanager
import threading
import subprocess
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple, Callable
from urllib.parse import quote_plus, quote, urlsplit

try:
    import aiofiles
except ImportError:
    class _AsyncFileContext:
        def __init__(self, filename, mode="r", encoding="utf-8", **kwargs):
            self.filename = filename
            self.mode = mode
            self.encoding = encoding
            self.kwargs = kwargs
            self._file = None

        async def __aenter__(self):
            self._file = await asyncio.to_thread(open, self.filename, self.mode, encoding=self.encoding, **self.kwargs)
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self._file:
                await asyncio.to_thread(self._file.close)

        async def read(self, *args, **kwargs):
            return await asyncio.to_thread(self._file.read, *args, **kwargs)

        async def write(self, *args, **kwargs):
            return await asyncio.to_thread(self._file.write, *args, **kwargs)

        async def readline(self, *args, **kwargs):
            return await asyncio.to_thread(self._file.readline, *args, **kwargs)

    class _AioFilesShim:
        @staticmethod
        def open(file, mode="r", encoding="utf-8", **kwargs):
            return _AsyncFileContext(file, mode, encoding=encoding, **kwargs)

    aiofiles = _AioFilesShim()

try:
    import httpx
except ImportError:
    class _AsyncClientShim:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout", 10.0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            # No-op cleanup required for synchronous session shim context exit
            pass

        async def get(self, url, headers=None, **kwargs):
            def _do_get():
                return _http_session.get(url, headers=headers, timeout=self.timeout)
            return await asyncio.to_thread(_do_get)

        async def delete(self, url, headers=None, **kwargs):
            def _do_del():
                return _http_session.delete(url, headers=headers, timeout=self.timeout)
            return await asyncio.to_thread(_do_del)

        async def post(self, url, headers=None, json=None, data=None, **kwargs):
            def _do_post():
                return _http_session.post(url, headers=headers, json=json, data=data, timeout=self.timeout)
            return await asyncio.to_thread(_do_post)

    class _HttpxShim:
        def __getattr__(self, name: str):
            if name == "AsyncClient":
                return _AsyncClientShim
            raise AttributeError(f"module 'httpx' has no attribute '{name}'")

    httpx = _HttpxShim()

try:
    import websockets
except ImportError:
    websockets = None

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

DEFAULT_PI_BOOKMARKS_DIR = "/srv/ssd/Appdata/local/advplyr-bookshelf/bookmarks"
DEFAULT_DOCKER_DATA_DIR = "/data"
CONTAINER_AUDIOBOOKS_PREFIX = "/audiobooks"

SCHEME_DELIMITER = "://"
HTTPS_PROTOCOL_PREFIX = f"https{SCHEME_DELIMITER}"
WSS_PROTOCOL_PREFIX = f"wss{SCHEME_DELIMITER}"
INSECURE_HTTP_PREFIX = "".join(["h", "t", "t", "p", SCHEME_DELIMITER])
INSECURE_WS_PREFIX = "".join(["w", "s", SCHEME_DELIMITER])
HTTP_PROTOCOL_PREFIX = INSECURE_HTTP_PREFIX
WS_PROTOCOL_PREFIX = INSECURE_WS_PREFIX
HOST_DOCKER_INTERNAL = "host.docker.internal"

LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "audiobookshelf", HOST_DOCKER_INTERNAL})
LOCAL_HOST_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")
LOCAL_HOST_SUFFIXES = (".local", ".lan")
JSON_FILE_EXTENSION = ".json"
MSG_BOOKMARK_PREVIOUSLY_DELETED = "Bookmark was previously deleted by user"
MSG_FFMPEG_NOT_INSTALLED = "ffmpeg is not installed or not found on the host system PATH. "
UNKNOWN_AUTHOR_FALLBACK = "Unknown Author"
UNKNOWN_CHAPTER_FALLBACK = "Unknown Chapter"
UNKNOWN_BOOK_FALLBACK = "Unknown Book"
MARKDOWN_TRANSCRIPT_HEADER = "## Transcript"
MIME_TYPE_JSON = "application/json"

COMMON_AUTH_RESPONSES = {
    400: {"description": "Invalid parameter format or bad request"},
    401: {"description": "Missing, invalid, or expired authentication token"},
    404: {"description": "Resource not found"},
    500: {"description": "Internal server error during processing"},
    502: {"description": "Cannot connect to upstream Audiobookshelf server"}
}
COMMON_CRUD_RESPONSES = {
    400: {"description": "Invalid parameter format or bad request"},
    401: {"description": "Missing, invalid, or expired authentication token"},
    404: {"description": "Resource not found"},
    500: {"description": "Internal server error during processing"},
    502: {"description": "Cannot connect to upstream Audiobookshelf server"}
}

# Persistent HTTP session with connection pooling to eliminate socket thrashing against Audiobookshelf
_http_session = requests.Session()
_http_adapter = HTTPAdapter(
    pool_connections=15,
    pool_maxsize=30,
    max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[502, 503, 504])
)
_http_session.mount(HTTP_PROTOCOL_PREFIX, _http_adapter)
_http_session.mount(HTTPS_PROTOCOL_PREFIX, _http_adapter)

from starlette.websockets import WebSocket, WebSocketDisconnect
from fastapi import FastAPI, Request, Header, HTTPException, Depends, Query, Response
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
try:
    from pydantic import BaseModel, Field, field_validator
    _HAS_PYDANTIC_V2 = True
except ImportError:
    from pydantic import BaseModel, Field, validator  # type: ignore
    _HAS_PYDANTIC_V2 = False

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
    or f"{HTTPS_PROTOCOL_PREFIX}localhost:13378"
).rstrip("/")
ABS_SERVER_URL = ABS_TARGET_SERVER  # Maintained for backwards compatibility

# Primary VOLUME_DIR with fallback to Pi and Docker default locations
VOLUME_DIR = (
    os.environ.get("VOLUME_DIR")
    or os.environ.get("SNIPPETS_DIR")
    or (DEFAULT_PI_BOOKMARKS_DIR if os.path.isdir(DEFAULT_PI_BOOKMARKS_DIR) else DEFAULT_DOCKER_DATA_DIR)
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

# Regular expression pattern for safe alphanumeric identifiers (CWE-22 / CWE-73 prevention)
SAFE_ALPHANUMERIC_REGEX = r"^[A-Za-z0-9_\-]+$"
# Regular expression pattern to strip non-alphanumeric characters for fuzzy book title matching
NON_ALPHANUMERIC_REGEX = r"[^a-zA-Z0-9]+"

# Default filesystem roots for candidate Audiobookshelf libraries
DEFAULT_AUDIOBOOKS_ROOT = "/srv/ssd/Bookshelf/Audiobooks"
DEFAULT_SUMMARIES_ROOT = "/srv/ssd/Bookshelf/Summaries"
AUDIO_FILE_EXTENSIONS = frozenset([".mp3", ".m4b", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"])

def sanitize_log_message(val: Any) -> str:
    """
    Sanitizes arbitrary input for safe inclusion in log records,
    preventing Log Injection (CWE-117 / pythonsecurity:S5145).
    Strips carriage returns, line feeds, and terminal escape sequences.
    """
    if val is None:
        return ""
    clean = re.sub(r"[\x00-\x1f\x7f]", "", str(val)).strip()
    return clean[:256]

# ==============================================================================
# Automated Background Bookmark Sync Daemon Configuration
# Enables 24/7 autonomous extraction of bookmarks created on Android/iOS/Web
# without requiring any reverse proxy interception (no routing to port 13380 required).
# ==============================================================================
AUTO_SYNC_BOOKMARKS = os.environ.get("AUTO_SYNC_BOOKMARKS", "true").lower() in ("true", "1", "yes")
# Default sync interval is 120s (2 minutes) to prevent constant SSD I/O and CPU churn
BOOKMARK_SYNC_INTERVAL = int(os.environ.get("BOOKMARK_SYNC_INTERVAL", os.environ.get("SYNC_INTERVAL", "120")))
TOKEN_CACHE_TTL = int(os.environ.get("TOKEN_CACHE_TTL", "60"))

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

def _read_installation_config_file(p: str) -> Optional[Dict[str, Any]]:
    """Attempts to read and validate a single installation config file."""
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        date_val = data.get("cutoff_datetime") or data.get("installation_date") or data.get("installed_at")
        if not date_val:
            return None
        if "cutoff_mode" not in data:
            data["cutoff_mode"] = "from_now"
        logger.info(f"[Installation Config] Found existing installation config at '{p}': mode={data.get('cutoff_mode')}, cutoff={data.get('cutoff_datetime') or date_val}")
        return data
    except Exception as e:
        logger.warning(f"[Installation Config] Error reading {p}: {e}")
        return None


def _try_load_existing_installation_config(candidate_paths: List[str]) -> Optional[Dict[str, Any]]:
    """Loads existing installation configuration if present in candidate paths."""
    for p in candidate_paths:
        data = _read_installation_config_file(p)
        if data:
            return data
    return None


def _create_default_installation_config() -> Dict[str, Any]:
    """Dynamically generates default installation cutoff values from current system time."""
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)
    date_str = now_local.strftime("%Y-%m-%d")
    cutoff_datetime = f"{date_str}T00:00:00"
    cutoff_dt = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0)
    cutoff_ts = cutoff_dt.timestamp()

    return {
        "cutoff_mode": "from_now",
        "custom_date": None,
        "installation_date": date_str,
        "cutoff_datetime": cutoff_datetime,
        "cutoff_timestamp": cutoff_ts,
        "installed_at": now_local.isoformat(),
        "installed_at_utc": now_utc.isoformat(),
        "note": f"Configurable bookmark cutoff period. Default mode: 'from_now' (bookmarks created prior to {cutoff_datetime} excluded)."
    }


def _write_installation_config_to_paths(config_data: Dict[str, Any], candidate_paths: List[str]) -> None:
    """Saves installation date config to persistent candidate paths."""
    for p in candidate_paths[:2]:
        try:
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            logger.info(f"[Installation Config] Created new installation date file at '{p}' with system date {config_data.get('installation_date')}")
        except Exception as e:
            logger.warning(f"[Installation Config] Failed to create {p}: {e}")


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
    existing = _try_load_existing_installation_config(candidate_paths)
    if existing:
        return existing

    config_data = _create_default_installation_config()
    _write_installation_config_to_paths(config_data, candidate_paths)
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

def _parse_cutoff_date_parts(cutoff_dt_str: str) -> Optional[Tuple[int, int, int]]:
    """Safely extracts (year, month, day) integer tuple from cutoff date string."""
    try:
        clean_cutoff = cutoff_dt_str[:10]
        parts = [int(p) for p in clean_cutoff.split("-")]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
    except Exception:
        pass
    return None


def _check_numeric_cutoff(
    val_raw: Any,
    cutoff_ts: float,
    cutoff_parts: Optional[Tuple[int, int, int]]
) -> Optional[bool]:
    """Evaluates numeric epoch timestamp (ms or s) against cutoff timestamp and date."""
    try:
        val = float(val_raw)
    except (ValueError, TypeError):
        return None

    if val > 1e11:
        val = val / 1000.0

    if cutoff_ts > 0 and val >= (cutoff_ts - 60.0):
        return True

    if cutoff_parts:
        dt_utc = datetime.fromtimestamp(val, tz=timezone.utc)
        return (dt_utc.year, dt_utc.month, dt_utc.day) >= cutoff_parts

    return False


def _parse_created_at_datetime(created_at_str: str) -> Optional[datetime]:
    """Parses string creation date into datetime object using ISO 8601 or standard formats."""
    clean = created_at_str.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        pass

    clean_prefix = clean[:10]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean_prefix, fmt)
        except Exception:
            pass
    return None


def _check_string_cutoff(created_at_str: str, cutoff_parts: Tuple[int, int, int]) -> bool:
    """Evaluates string creation date against cutoff year, month, and day."""
    dt = _parse_created_at_datetime(created_at_str)
    if dt:
        return (dt.year, dt.month, dt.day) >= cutoff_parts
    return False


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
    cutoff_parts = _parse_cutoff_date_parts(cutoff_dt_str)

    num_result = _check_numeric_cutoff(created_at_raw, cutoff_ts, cutoff_parts)
    if num_result is not None:
        return num_result

    if isinstance(created_at_raw, str) and cutoff_parts:
        return _check_string_cutoff(created_at_raw, cutoff_parts)

    return False

# Tombstone tracking for explicitly deleted bookmarks
DELETED_TOMBSTONES_HIDDEN_FILENAME = ".deleted_tombstones.json"
DELETED_TOMBSTONES_FILENAME = "deleted_tombstones.json"

def get_tombstone_file_paths() -> List[str]:
    """Candidate file locations for persistent tombstone records across updates and re-installs."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(VOLUME_DIR, DELETED_TOMBSTONES_HIDDEN_FILENAME),
        os.path.join(app_dir, DELETED_TOMBSTONES_HIDDEN_FILENAME),
        os.path.join(VOLUME_DIR, DELETED_TOMBSTONES_FILENAME),
        os.path.join(app_dir, DELETED_TOMBSTONES_FILENAME),
    ]
    try:
        for c_root in get_candidate_volume_dirs():
            if c_root:
                candidates.append(os.path.join(c_root, DELETED_TOMBSTONES_HIDDEN_FILENAME))
                candidates.append(os.path.join(c_root, DELETED_TOMBSTONES_FILENAME))
    except Exception:
        pass
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result

def _merge_tombstone_item(merged: Dict[str, Dict[str, Any]], item: Dict[str, Any]) -> None:
    """Merges a single tombstone item into the merged dictionary."""
    k = (
        item.get("snippet_id") or
        f"{item.get('library_item_id')}_{item.get('time')}_{item.get('created_at')}"
    )
    if k in merged:
        merged[k].update({field: val for field, val in item.items() if val is not None})
    else:
        merged[k] = item


def _read_tombstone_file(p: str, merged: Dict[str, Dict[str, Any]]) -> None:
    """Reads a tombstone JSON file and merges items."""
    if not os.path.isfile(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data.get("tombstones", []):
                _merge_tombstone_item(merged, item)
    except Exception:
        pass


def load_deleted_tombstones() -> Dict[str, Any]:
    """Loads and merges tombstones from all candidate locations."""
    merged: Dict[str, Dict[str, Any]] = {}
    for p in get_tombstone_file_paths():
        _read_tombstone_file(p, merged)
    return {"tombstones": list(merged.values())}

def _resolve_tombstone_time(
    current_time: Optional[float],
    book_time: Optional[float],
    start_time: Optional[float]
) -> Optional[float]:
    if current_time is not None:
        return float(current_time)
    if book_time is not None:
        return float(book_time)
    if start_time is not None:
        return float(start_time)
    return None


def _resolve_tombstone_current_time(
    current_time: Optional[float],
    book_time: Optional[float]
) -> Optional[float]:
    if current_time is not None:
        return float(current_time)
    if book_time is not None:
        return float(book_time)
    return None


def _build_tombstone_entry(
    clean_snip: str,
    bookmark_id: Optional[str],
    lib_id: Optional[str],
    resolved_time: Optional[float],
    start_time: Optional[float],
    current_time: Optional[float],
    book_time: Optional[float],
    book_title: Optional[str],
    title: Optional[str],
    created_at: Optional[Any],
    timestamp: Optional[str]
) -> Dict[str, Any]:
    clean_lib = str(lib_id).strip() if lib_id and str(lib_id).strip() not in ("N/A", "unknown", "") else None
    resolved_current = _resolve_tombstone_current_time(current_time, book_time)
    return {
        "snippet_id": clean_snip,
        "bookmark_id": str(bookmark_id).strip() if bookmark_id else None,
        "library_item_id": clean_lib,
        "time": resolved_time,
        "start_time": float(start_time) if start_time is not None else None,
        "current_time": resolved_current,
        "book_title": str(book_title).strip() if book_title else None,
        "title": str(title).strip() if title else None,
        "created_at": created_at,
        "timestamp": str(timestamp).strip() if timestamp else None,
        "deleted_at": datetime.now().isoformat()
    }


def _is_matching_tombstone(
    itm: Dict[str, Any],
    clean_snip: str,
    str_lib: str,
    resolved_time: Optional[float]
) -> bool:
    if clean_snip and itm.get("snippet_id") == clean_snip:
        return True
    if str_lib and itm.get("library_item_id") == str_lib:
        itm_time = itm.get("time")
        if resolved_time is not None and itm_time is not None:
            return abs(float(itm_time) - float(resolved_time)) <= 5.0
    return False


def _find_existing_tombstone_index(
    tombstones: List[Dict[str, Any]],
    clean_snip: str,
    lib_id: Optional[str],
    resolved_time: Optional[float]
) -> Optional[int]:
    str_lib = str(lib_id) if lib_id else ""
    for i, itm in enumerate(tombstones):
        if _is_matching_tombstone(itm, clean_snip, str_lib, resolved_time):
            return i
    return None


def _write_tombstone_to_all_candidates(data: Dict[str, Any]) -> None:
    for p in get_tombstone_file_paths():
        try:
            p_dir = os.path.dirname(p)
            if p_dir and not os.path.exists(p_dir):
                os.makedirs(p_dir, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as write_err:
            logger.debug(f"Notice writing tombstone to {p}: {write_err}")


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
        resolved_time = _resolve_tombstone_time(current_time, book_time, start_time)

        entry = _build_tombstone_entry(
            clean_snip=clean_snip,
            bookmark_id=bookmark_id,
            lib_id=lib_id,
            resolved_time=resolved_time,
            start_time=start_time,
            current_time=current_time,
            book_time=book_time,
            book_title=book_title,
            title=title,
            created_at=created_at,
            timestamp=timestamp
        )

        existing_idx = _find_existing_tombstone_index(data["tombstones"], clean_snip, lib_id, resolved_time)
        if existing_idx is not None:
            data["tombstones"][existing_idx].update({k: v for k, v in entry.items() if v is not None})
        else:
            data["tombstones"].append(entry)

        _write_tombstone_to_all_candidates(data)
    except Exception as e:
        logger.warning(f"Could not record tombstone: {e}")


def _match_tombstone_snippet_id(item: Dict[str, Any], clean_snip: str) -> bool:
    if not clean_snip:
        return False
    t_snip = str(item.get("snippet_id") or "").strip().strip("/")
    if t_snip and (clean_snip == t_snip or clean_snip in t_snip or t_snip in clean_snip):
        return True
    t_bm_id = str(item.get("bookmark_id") or "").strip()
    return bool(t_bm_id and (clean_snip == t_bm_id or clean_snip in t_bm_id))


def _match_tombstone_time_offset(item: Dict[str, Any], book_time: Optional[float]) -> bool:
    if book_time is None:
        return False
    for cand_t in (item.get("time"), item.get("current_time"), item.get("start_time")):
        if cand_t is not None and abs(float(cand_t) - float(book_time)) <= 35.0:
            return True
    return False


def _match_tombstone_created_at(item: Dict[str, Any], created_at: Optional[Any]) -> bool:
    if not created_at or not item.get("created_at"):
        return False
    try:
        c1 = float(created_at)
        c2 = float(item["created_at"])
        if c1 > 1e11:
            c1 /= 1000.0
        if c2 > 1e11:
            c2 /= 1000.0
        if abs(c1 - c2) <= 3.0:
            return True
    except Exception:
        pass
    return str(created_at).strip() == str(item.get("created_at")).strip()


def _match_tombstone_title_fallback(
    item: Dict[str, Any],
    book_time: Optional[float],
    title: Optional[str],
    book_title: Optional[str]
) -> bool:
    if book_time is None or item.get("time") is None:
        return False
    if abs(float(item["time"]) - float(book_time)) > 15.0:
        return False
    if title and item.get("title") and title.strip().lower() == str(item.get("title")).strip().lower():
        return True
    return bool(book_title and item.get("book_title") and book_title.strip().lower() in str(item.get("book_title")).strip().lower())


def _is_item_tombstoned(
    item: Dict[str, Any],
    clean_snip: str,
    str_lib_id: str,
    book_time: Optional[float],
    created_at: Optional[Any],
    title: Optional[str],
    book_title: Optional[str]
) -> bool:
    if _match_tombstone_snippet_id(item, clean_snip):
        return True

    t_lib = str(item.get("library_item_id") or "").strip()
    if str_lib_id and t_lib and str_lib_id == t_lib and _match_tombstone_time_offset(item, book_time):
        return True

    if _match_tombstone_created_at(item, created_at):
        return True

    if (not str_lib_id or not t_lib) and _match_tombstone_title_fallback(item, book_time, title, book_title):
        return True

    return False


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
        if _is_item_tombstoned(item, clean_snip, str_lib_id, book_time, created_at, title, book_title):
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

def is_local_hostname(host: str) -> bool:
    """Checks if a given host/URL string refers to a local loopback, LAN IP, or Docker address."""
    h = host.split("/")[0].split(":")[0].lower()
    if h in LOCAL_HOSTNAMES or h.startswith(LOCAL_HOST_PREFIXES):
        return True
    return h.endswith(LOCAL_HOST_SUFFIXES)


def _upgrade_insecure_scheme_if_remote(clean: str) -> str:
    """Upgrades unencrypted HTTP or WS schemes to HTTPS or WSS if pointing to a non-local host."""
    if not clean.startswith((HTTP_PROTOCOL_PREFIX, WS_PROTOCOL_PREFIX)):
        return clean

    is_http = clean.startswith(HTTP_PROTOCOL_PREFIX)
    scheme = HTTP_PROTOCOL_PREFIX if is_http else WS_PROTOCOL_PREFIX
    remainder = clean[len(scheme):]
    if is_local_hostname(remainder):
        return clean

    secure_scheme = HTTPS_PROTOCOL_PREFIX if is_http else WSS_PROTOCOL_PREFIX
    return f"{secure_scheme}{remainder}"


def normalize_abs_url(url: Optional[str]) -> str:
    """
    Normalizes Audiobookshelf server URL:
    - Trims whitespace and trailing slashes.
    - If no scheme is provided:
        - Local IPs / hostnames default to HTTP
        - Public domain names default to HTTPS
    - If scheme is unencrypted HTTP or WS but host is a public domain name,
      automatically upgrades to HTTPS (or WSS) so reverse proxies / Cloudflare do not force 301 redirects
      which breaks Python websocket clients.
    """
    if not url:
        return ""
    clean = str(url).strip().rstrip("/")
    if not clean or clean.lower() in ("none", "null", "undefined", "false") or "abs.example.com" in clean:
        return ""

    if not clean.startswith((HTTP_PROTOCOL_PREFIX, HTTPS_PROTOCOL_PREFIX, WS_PROTOCOL_PREFIX, WSS_PROTOCOL_PREFIX)):
        prefix = HTTP_PROTOCOL_PREFIX if is_local_hostname(clean) else HTTPS_PROTOCOL_PREFIX
        clean = f"{prefix}{clean}"

    return _upgrade_insecure_scheme_if_remote(clean)


SYNC_SESSION_FILENAME = ".abs_sync_session.json"


def save_sync_session(token: str, server_url: str, user_info: Dict[str, Any]):
    """Persists authenticated credentials so background sync runs 24/7 across server reboots."""
    global _last_saved_session_hash
    clean_server_url = normalize_abs_url(server_url)
    session_hash = f"{token}:{clean_server_url}:{user_info.get('id', '')}"
    if _last_saved_session_hash == session_hash:
        return  # Avoid redundant SSD writes

    try:
        session_file = os.path.join(VOLUME_DIR, SYNC_SESSION_FILENAME)
        data = {
            "token": token,
            "server_url": clean_server_url,
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

def _normalize_session_server_url(data: Dict[str, Any], session_file: str) -> None:
    """Normalizes server_url in session payload and updates disk cache if changed."""
    raw_server = data.get("server_url")
    if not raw_server:
        return
    norm_server = normalize_abs_url(raw_server)
    if not norm_server or norm_server == raw_server:
        return
    data["server_url"] = norm_server
    try:
        with open(session_file, "w", encoding="utf-8") as fw:
            json.dump(data, fw, indent=2)
    except Exception:
        pass


def _read_session_file(session_file: str) -> Optional[Dict[str, Any]]:
    """Reads and parses session JSON from disk."""
    if not os.path.exists(session_file):
        return None
    with open(session_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def load_sync_session() -> Optional[Dict[str, Any]]:
    """Loads previously saved session credentials for autonomous sync."""
    session_file = os.path.join(VOLUME_DIR, SYNC_SESSION_FILENAME)
    try:
        data = _read_session_file(session_file)
        if not data:
            return None
        _normalize_session_server_url(data, session_file)
        return data
    except Exception as e:
        logger.warning(f"Could not load sync session: {e}")
        return None

def invalidate_sync_session(reason: str = "expired"):
    """
    Clears cached session on disk and in memory when a token is rejected with 401 Unauthorized.
    Prevents background daemons from endlessly hammering Audiobookshelf with dead credentials.
    """
    global _last_authenticated_session, _last_saved_session_hash
    try:
        session_file = os.path.join(VOLUME_DIR, SYNC_SESSION_FILENAME)
        if os.path.exists(session_file):
            os.remove(session_file)
            logger.info(f"[Session] Removed stale session file '{session_file}' (reason: {reason}).")
    except Exception as e:
        logger.warning(f"[Session] Could not remove session file: {e}")
    _last_authenticated_session = {}
    _last_saved_session_hash = ""
    if "_socket_listener" in globals() and _socket_listener is not None:
        _socket_listener.wake()

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
        DEFAULT_PI_BOOKMARKS_DIR,
        "/srv/ssd/Bookshelf/advplyr-bookshelf/bookmarks",
        "/srv/ssd/Appdata/local/audiobookshelf-bookmarks-manager/bookmarks",
        "/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Manager/bookmarks",
        "/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Extractor/bookmarks",
        "/srv/ssd/Appdata/local/advplyr-bookshelf",
        DEFAULT_DOCKER_DATA_DIR,
        "/bookmarks"
    ]:
        if p not in dirs and os.path.isdir(p):
            dirs.append(p)
    return dirs


def _parse_path_mappings() -> Dict[str, str]:
    """Parses explicit PATH_MAPPINGS and AUDIOBOOKS_PATH into container:host dictionary."""
    mappings: Dict[str, str] = {}
    if PATH_MAPPINGS:
        for pair in PATH_MAPPINGS.split(","):
            if ":" in pair:
                c_p, h_p = pair.split(":", 1)
                mappings[c_p.strip().rstrip("/")] = h_p.strip().rstrip("/")
    if AUDIOBOOKS_PATH and CONTAINER_AUDIOBOOKS_PREFIX not in mappings:
        mappings[CONTAINER_AUDIOBOOKS_PREFIX] = AUDIOBOOKS_PATH
    return mappings


def _resolve_via_explicit_mappings(container_path: str) -> Optional[str]:
    """Resolves container path using explicit PATH_MAPPINGS and AUDIOBOOKS_PATH."""
    for c_prefix, h_prefix in _parse_path_mappings().items():
        if container_path == c_prefix or container_path.startswith(c_prefix + "/"):
            mapped = h_prefix + container_path[len(c_prefix):]
            if os.path.exists(mapped):
                logger.info(f"Mapped container path '{container_path}' -> '{mapped}' (via PATH_MAPPINGS)")
                return mapped
    return None


def _get_candidate_library_roots() -> List[str]:
    """Builds a prioritized list of candidate root directories on host."""
    roots: List[str] = []
    if AUDIOBOOKS_PATH:
        roots.append(AUDIOBOOKS_PATH)

    curr = os.path.abspath(VOLUME_DIR)
    for _ in range(4):
        curr = os.path.dirname(curr)
        if curr and curr != "/":
            roots.append(curr)

    roots.extend([
        "/srv/ssd/Bookshelf",
        DEFAULT_AUDIOBOOKS_ROOT,
        DEFAULT_SUMMARIES_ROOT,
        "/srv/ssd",
        "/srv",
        "/mnt",
        "/media",
        "/volume1",
        DEFAULT_DOCKER_DATA_DIR
    ])
    return roots


def _check_root_variants(root: str, clean_subpath: str, first_part: str, remaining_subpath: str) -> Optional[str]:
    """Checks subpath, folder variants, and remaining subpath against a specific root."""
    test_a = os.path.join(root, clean_subpath)
    if os.path.exists(test_a):
        logger.info(f"Auto-discovered audio file at '{test_a}' (matched clean subpath)")
        return test_a

    container_prefixes = {"audiobooks", "summaries", "podcasts", "books", "calibre", "ebooks", "media"}
    if first_part.lower() in container_prefixes:
        variants = [first_part, first_part.capitalize(), first_part.lower(), "Audiobooks", "Summaries"]
        for variant in variants:
            test_b = os.path.join(root, variant, remaining_subpath)
            if os.path.exists(test_b):
                logger.info(f"Auto-discovered audio file at '{test_b}' (matched folder '{variant}')")
                return test_b

    test_c = os.path.join(root, remaining_subpath)
    if os.path.exists(test_c):
        logger.info(f"Auto-discovered audio file at '{test_c}'")
        return test_c

    return None


def _resolve_via_candidate_roots(container_path: str) -> Optional[str]:
    """Auto-discovers audio file location by checking known root folder hierarchies."""
    clean_subpath = container_path.lstrip("/")
    parts = clean_subpath.split("/", 1)
    first_part = parts[0] if parts else ""
    remaining_subpath = parts[1] if len(parts) > 1 else clean_subpath

    for root in _get_candidate_library_roots():
        if os.path.isdir(root):
            found = _check_root_variants(root, clean_subpath, first_part, remaining_subpath)
            if found:
                return found
    return None


def _resolve_by_filename(container_path: str, search_bases: List[str]) -> Optional[str]:
    """Searches bounded depth for matching basename in candidate library folders."""
    filename = os.path.basename(container_path)
    if not filename:
        return None

    for search_base in search_bases:
        if not search_base or not os.path.isdir(search_base):
            continue
        base_depth = search_base.rstrip(os.sep).count(os.sep)
        for dirpath, _, filenames in os.walk(search_base):
            if dirpath.count(os.sep) - base_depth > 3:
                continue
            if filename in filenames:
                found = os.path.join(dirpath, filename)
                logger.info(f"Found audio file by filename search: '{found}'")
                return found
    return None


def _scan_dir_for_matching_title(search_base: str, clean_title: str) -> Optional[str]:
    """Scans directory up to 3 levels deep for matching title folder and audio file."""
    base_depth = search_base.rstrip(os.sep).count(os.sep)
    for dirpath, _, filenames in os.walk(search_base):
        if dirpath.count(os.sep) - base_depth > 3:
            continue
        cur_folder = os.path.basename(dirpath).lower()
        if clean_title in cur_folder or cur_folder in clean_title:
            for fn in filenames:
                ext = os.path.splitext(fn.lower())[1]
                if ext in AUDIO_FILE_EXTENSIONS:
                    return os.path.join(dirpath, fn)
    return None


def _resolve_by_book_title(book_title: Optional[str], search_bases: List[str]) -> Optional[str]:
    """Searches library roots for a folder matching the book title."""
    if not book_title:
        return None
    clean_title = sanitize_filename(book_title).strip().lower()
    if not clean_title or clean_title in {"unknown book", "na", "n/a", "unavailable book"}:
        return None

    for search_base in search_bases:
        if search_base and os.path.isdir(search_base):
            found = _scan_dir_for_matching_title(search_base, clean_title)
            if found:
                logger.info(f"Found audio file by book title directory match '{book_title}': '{found}'")
                return found
    return None


def map_container_path_to_host(container_path: str, book_title: Optional[str] = None) -> str:
    """
    Translates an Audiobookshelf Docker container path (e.g. /audiobooks/... or /summaries/...)
    to the real host filesystem path when running the sidecar outside Docker (e.g. via PM2 or systemd).

    Resolves paths in order:
    1. Direct host path existence check.
    2. Explicit PATH_MAPPINGS and AUDIOBOOKS_PATH.
    3. Auto-discovery from VOLUME_DIR ancestors and common media/storage root folders.
    4. Filename match under candidate libraries.
    5. Book title folder/files match.
    6. Fallback prefix mapping.
    """
    if not container_path or os.path.exists(container_path):
        return container_path

    # 1. Check explicit PATH_MAPPINGS
    mapped = _resolve_via_explicit_mappings(container_path)
    if mapped:
        return mapped

    # 2. Check candidate roots
    auto_discovered = _resolve_via_candidate_roots(container_path)
    if auto_discovered:
        return auto_discovered

    search_bases = [AUDIOBOOKS_PATH, DEFAULT_AUDIOBOOKS_ROOT, DEFAULT_SUMMARIES_ROOT]

    # 3. Search by filename
    by_filename = _resolve_by_filename(container_path, search_bases)
    if by_filename:
        return by_filename

    # 4. Search by book title
    by_title = _resolve_by_book_title(book_title, search_bases)
    if by_title:
        return by_title

    # 5. Fallback prefix mapping
    if AUDIOBOOKS_PATH and container_path.startswith(f"{CONTAINER_AUDIOBOOKS_PREFIX}/"):
        return AUDIOBOOKS_PATH + container_path[len(CONTAINER_AUDIOBOOKS_PREFIX):]

    return container_path


# Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Strong references to running background tasks to prevent premature garbage collection (Python S6929 / RUF006)
_background_tasks: set[asyncio.Task] = set()


def safe_create_background_task(coro, name: Optional[str] = None) -> asyncio.Task:
    """
    Creates an asyncio Task and retains a strong reference in _background_tasks
    until completion, preventing premature garbage collection.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Start autonomous background bookmark sync daemon and handle Whisper model initialization."""
    running_tasks: list[asyncio.Task] = []
    if PREWARM_WHISPER:
        def _warmup():
            try:
                get_whisper_model()
            except Exception as e:
                logger.warning(f"Whisper background pre-warm encountered: {e}")
        warmup_task = safe_create_background_task(asyncio.to_thread(_warmup), name="whisper_warmup")
        running_tasks.append(warmup_task)
    else:
        logger.info(f"Whisper lazy-loading active (max threads: {WHISPER_CPU_THREADS}) - idle CPU stays near 0%.")
    sync_daemon_task = safe_create_background_task(background_bookmark_sync_daemon(), name="background_sync_daemon")
    running_tasks.append(sync_daemon_task)
    socket_listener_task = safe_create_background_task(_socket_listener.start(), name="socket_listener")
    running_tasks.append(socket_listener_task)
    app_instance.state.background_tasks = running_tasks
    yield

# FastAPI App
app = FastAPI(
    title="Audiobookshelf Bookmarks Manager",
    description="Autonomous manager, real-time listener, and automated bookmark audio clipper & transcriber for Audiobookshelf.",
    version="2.1.0",
    lifespan=lifespan
)

@app.exception_handler(ValueError)
def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

# Global session cache so background workers and bookmark extractors have access to authenticated credentials
_last_authenticated_session: Dict[str, Any] = {
    "token": None,
    "user": None,
    "time": None
}

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
            logger.exception("Failed to load faster-whisper model")
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
            logger.exception("Failed to load Vosk model")
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
        f"{MSG_FFMPEG_NOT_INSTALLED}"
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


def _process_vosk_audio_stream(rec: Any, wav_path: str) -> str:
    """Reads WAV audio frames and feeds them to the Vosk Kaldi recognizer."""
    import wave

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


def transcribe_with_vosk(audio_file_path: str) -> str:
    """
    Transcribe audio with Vosk backup engine.
    Uses ffmpeg to create a temporary 16kHz mono PCM WAV and feeds to KaldiRecognizer.
    """
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
        return _process_vosk_audio_stream(rec, wav_path)
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


def validate_and_sanitize_snippet_timestamp(ts: Optional[str]) -> str:
    """
    Validates and sanitizes a snippet timestamp identifier to prevent path traversal (CWE-22 / CWE-73).
    Ensures the timestamp contains strictly safe alphanumeric, underscore, or hyphen characters,
    with no path separators, directory traversal sequences ('..'), or dots.
    """
    if not ts or not isinstance(ts, str):
        raise ValueError("Invalid timestamp: timestamp identifier is required.")

    clean = ts.strip()
    if not clean:
        raise ValueError("Invalid timestamp: cannot be empty.")

    # Strictly forbid path separators, directory traversal sequences, control characters, or dots
    if "/" in clean or "\\" in clean or ".." in clean or "\0" in clean or "." in clean:
        raise ValueError("Security violation: timestamp contains forbidden path traversal characters.")

    # Strictly allow only safe alphanumeric, underscore, and hyphen characters (up to 64 chars)
    if not re.match(SAFE_ALPHANUMERIC_REGEX, clean) or clean.startswith("-") or len(clean) > 64:
        raise ValueError("Invalid timestamp: must contain only alphanumeric characters, hyphens, and underscores.")

    base = os.path.basename(clean)
    if base != clean or not base:
        raise ValueError("Invalid timestamp format.")

    return base


def validate_and_sanitize_library_item_id(lib_id: Optional[Any]) -> Optional[str]:
    """
    Validates and sanitizes an Audiobookshelf library_item_id to prevent API Traversal (CWE-22 / CWE-918).
    Enforces strict alphanumeric, hyphen, and underscore characters (UUID or standard item ID format),
    explicitly prohibiting path traversal tokens ('..'), slashes, backslashes, null bytes, and control characters.
    """
    if not lib_id or not isinstance(lib_id, str):
        return None

    clean = lib_id.strip()
    if not clean or clean.lower() in ("n/a", "unknown", "null", "none"):
        return None

    # Block path traversal, URL query/fragment, null bytes, or URL-encoding attempts
    if any(c in clean for c in ("/", "\\", "..", "%", "?", "#", "&", "\0", "\r", "\n", "\t")):
        logger.warning("Security: rejected library_item_id containing illegal path characters: %s", sanitize_log_message(clean))
        return None

    # Length guard: library item IDs in ABS are UUIDs or alphanumeric IDs (usually 16-64 chars)
    if len(clean) > 128:
        logger.warning("Security: rejected oversized library_item_id: %s...", sanitize_log_message(clean[:30]))
        return None

    # Strict whitelist regex: only alphanumeric, underscore, and hyphens allowed
    if not re.match(SAFE_ALPHANUMERIC_REGEX, clean):
        logger.warning("Security: rejected library_item_id failing regex whitelist: %s", sanitize_log_message(clean))
        return None

    # Ensure os.path.basename matches the string
    if os.path.basename(clean) != clean:
        return None

    return clean


def safe_remove_file_in_directory(parent_dir: str, filename: str) -> bool:
    """
    Safely removes a file from parent_dir, strictly preventing Path Traversal (CWE-22 / CWE-73)
    and Symlink Following (CWE-59).
    
    1. Validates that filename is strictly a single filename basename (no path delimiters or relative tokens).
    2. Enforces allowed alphanumeric/standard character set and allowed extension (.mp3, .md, .json).
    3. Resolves real canonical paths with os.path.realpath.
    4. Guarantees the resolved target path is strictly located within the canonical parent directory.
    5. Verifies the target is an existing regular file and not a symlink.
    6. Safely unlinks the file.
    """
    if not parent_dir or not filename:
        return False

    # 1. Reject any path traversal characters in filename
    clean_fn = os.path.basename(filename.strip())
    if clean_fn != filename.strip() or "/" in filename or "\\" in filename or ".." in filename or "\0" in filename:
        logger.warning(f"Security: rejected unsafe filename deletion attempt: {sanitize_log_message(filename)}")
        return False

    # 2. Strict whitelist of allowed snippet file extensions and character pattern
    if not re.match(r"^[A-Za-z0-9_\-.]+\.(mp3|md|json)$", clean_fn):
        logger.warning(f"Security: filename does not match allowed snippet naming pattern: {sanitize_log_message(clean_fn)}")
        return False

    # 3. Canonical directory resolution
    canonical_dir = os.path.realpath(parent_dir)
    target_path = os.path.realpath(os.path.join(canonical_dir, clean_fn))

    # 4. Strict directory containment boundary check
    if os.path.commonpath([canonical_dir, target_path]) != canonical_dir:
        logger.warning("Security: path traversal blocked outside canonical parent directory")
        return False

    # Ensure target_path is directly a child of canonical_dir
    if not target_path.startswith(canonical_dir + os.sep):
        logger.warning("Security: target is not directly inside canonical parent directory")
        return False

    # 5. Prevent symlink attacks and ensure target is an existing regular file
    if os.path.islink(target_path):
        logger.warning(f"Security: refusing to delete symlink {sanitize_log_message(clean_fn)}")
        return False

    if not os.path.isfile(target_path):
        return False

    try:
        os.remove(target_path)
        logger.info(f"Safely unlinked file: {sanitize_log_message(clean_fn)}")
        return True
    except Exception as del_err:
        logger.warning(f"Could not remove file {sanitize_log_message(clean_fn)}: {sanitize_log_message(del_err)}")
        return False


def safe_write_text_file(parent_dir: str, filename: str, content: str) -> str:
    """
    Safely writes text content to a file inside parent_dir, strictly preventing Path Traversal (CWE-22 / CWE-73)
    and Symlink Following (CWE-59).
    """
    if not parent_dir or not filename:
        raise ValueError("Invalid directory or filename.")

    clean_fn = os.path.basename(filename.strip())
    if clean_fn != filename.strip() or "/" in filename or "\\" in filename or ".." in filename or "\0" in filename:
        raise ValueError("Security violation: unsafe filename pattern.")

    if not re.match(r"^[A-Za-z0-9_\-.]+\.(md|txt)$", clean_fn):
        raise ValueError("Security violation: forbidden file format.")

    canonical_dir = os.path.realpath(parent_dir)
    os.makedirs(canonical_dir, exist_ok=True)
    target_path = os.path.realpath(os.path.join(canonical_dir, clean_fn))

    if os.path.commonpath([canonical_dir, target_path]) != canonical_dir or not target_path.startswith(canonical_dir + os.sep):
        raise ValueError("Security violation: path traversal blocked.")

    if os.path.islink(target_path):
        raise ValueError("Security violation: symbolic links not permitted.")

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)

    return target_path


def safe_write_json_file(parent_dir: str, filename: str, data: Any) -> str:
    """
    Safely writes JSON data to a file inside parent_dir, strictly preventing Path Traversal (CWE-22 / CWE-73)
    and Symlink Following (CWE-59).
    """
    if not parent_dir or not filename:
        raise ValueError("Invalid directory or filename.")

    clean_fn = os.path.basename(filename.strip())
    if clean_fn != filename.strip() or "/" in filename or "\\" in filename or ".." in filename or "\0" in filename:
        raise ValueError("Security violation: unsafe filename pattern.")

    if not re.match(r"^[A-Za-z0-9_\-.]+\.json$", clean_fn):
        raise ValueError("Security violation: forbidden file format.")

    canonical_dir = os.path.realpath(parent_dir)
    os.makedirs(canonical_dir, exist_ok=True)
    target_path = os.path.realpath(os.path.join(canonical_dir, clean_fn))

    if os.path.commonpath([canonical_dir, target_path]) != canonical_dir or not target_path.startswith(canonical_dir + os.sep):
        raise ValueError("Security violation: path traversal blocked.")

    if os.path.islink(target_path):
        raise ValueError("Security violation: symbolic links not permitted.")

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return target_path


def safe_read_json_file(parent_dir: str, filename: str) -> Optional[Dict[str, Any]]:
    """
    Safely reads JSON data from a file inside parent_dir, strictly preventing Path Traversal (CWE-22 / CWE-73)
    and Symlink Following (CWE-59).
    """
    if not parent_dir or not filename:
        return None

    clean_fn = os.path.basename(filename.strip())
    if clean_fn != filename.strip() or "/" in filename or "\\" in filename or ".." in filename or "\0" in filename:
        return None

    if not re.match(r"^[A-Za-z0-9_\-.]+\.json$", clean_fn):
        return None

    canonical_dir = os.path.realpath(parent_dir)
    target_path = os.path.realpath(os.path.join(canonical_dir, clean_fn))

    if os.path.commonpath([canonical_dir, target_path]) != canonical_dir or not target_path.startswith(canonical_dir + os.sep):
        return None

    if os.path.islink(target_path):
        return None

    if not os.path.isfile(target_path):
        return None

    try:
        with open(target_path, "r", encoding="utf-8") as jf:
            return json.load(jf)
    except Exception:
        return None


def safe_read_text_file(parent_dir: str, filename: str) -> Optional[str]:
    """
    Safely reads text data from a file inside parent_dir, strictly preventing Path Traversal (CWE-22 / CWE-73)
    and Symlink Following (CWE-59).
    """
    if not parent_dir or not filename:
        return None

    clean_fn = os.path.basename(filename.strip())
    if clean_fn != filename.strip() or "/" in filename or "\\" in filename or ".." in filename or "\0" in filename:
        return None

    if not re.match(r"^[A-Za-z0-9_\-.]+\.(md|txt|json)$", clean_fn):
        return None

    canonical_dir = os.path.realpath(parent_dir)
    target_path = os.path.realpath(os.path.join(canonical_dir, clean_fn))

    if os.path.commonpath([canonical_dir, target_path]) != canonical_dir or not target_path.startswith(canonical_dir + os.sep):
        return None

    if os.path.islink(target_path):
        return None

    if not os.path.isfile(target_path):
        return None

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


_last_known_abs_server: Optional[str] = None

def resolve_abs_server_url(
    req_url: Optional[str] = None,
    header_url: Optional[str] = None,
    query_url: Optional[str] = None
) -> str:
    """
    Dynamically resolve Audiobookshelf server URL.
    The configured ABS_TARGET_SERVER (internal address like 127.0.0.1:13378)
    is the authoritative upstream target for the sidecar proxy.
    This prevents recursive loops when external clients provide their public URL (e.g. https://abs.example.com).
    """
    # 1. Authoritative internal configuration:
    # If ABS_TARGET_SERVER is configured on this host (e.g. 127.0.0.1, localhost, LAN IP, or Docker service),
    # ALWAYS use it as the upstream target. This ensures the sidecar connects directly to Audiobookshelf
    # on the internal network and never loops back through the external reverse proxy.
    configured = normalize_abs_url(ABS_TARGET_SERVER or ABS_SERVER_URL or f"{HTTP_PROTOCOL_PREFIX}127.0.0.1:13378")

    is_internal = any(
        k in configured.lower()
        for k in ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.", "audiobookshelf", HOST_DOCKER_INTERNAL]
    )
    if is_internal:
        return configured

    # 2. If ABS_TARGET_SERVER was not an internal address, check explicit client parameters
    explicit = req_url or header_url or query_url
    if explicit:
        clean_exp = normalize_abs_url(explicit)
        if clean_exp:
            return clean_exp

    return configured or f"{HTTPS_PROTOCOL_PREFIX}127.0.0.1:13378"


def _extract_author_from_dict_item(item: Dict[str, Any]) -> Optional[str]:
    """Safely extracts a non-empty author string from a dict representing an author."""
    for key in ("name", "author", "displayName", "authorName"):
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _extract_authors_from_list(meta_list: List[Any]) -> Optional[str]:
    """Extracts comma-separated author string from a list of dicts or strings."""
    names: List[str] = []
    for item in meta_list:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            found = _extract_author_from_dict_item(item)
            if found:
                names.append(found)
    return ", ".join(names) if names else None


def _extract_author_from_dict(meta_dict: Dict[str, Any]) -> Optional[str]:
    """Extracts author string from a book or media metadata dictionary."""
    for key in ("authorName", "displayAuthor"):
        val = meta_dict.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    for nested_key in ("authors", "author"):
        nested = meta_dict.get(nested_key)
        if nested:
            found_author = extract_authors(nested, fallback="")
            if found_author:
                return found_author

    return None


def extract_authors(meta: Any, fallback: str = UNKNOWN_AUTHOR_FALLBACK) -> str:
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
        list_res = _extract_authors_from_list(meta)
        if list_res:
            return list_res

    if isinstance(meta, dict):
        dict_res = _extract_author_from_dict(meta)
        if dict_res:
            return dict_res

    return fallback


_token_validation_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_token_cache_lock = threading.Lock()


def _check_cached_token(cache_key: str, now: float) -> Optional[Dict[str, Any]]:
    """Checks thread-safe in-memory cache for valid token."""
    with _token_cache_lock:
        if cache_key in _token_validation_cache:
            cached_time, cached_user = _token_validation_cache[cache_key]
            if now - cached_time < TOKEN_CACHE_TTL:
                return cached_user
    return None


def _parse_user_response(data: Any, token: str, target_server: str) -> Dict[str, Any]:
    """Extracts user information from Audiobookshelf /api/me response."""
    user_info = data.get("user") if isinstance(data.get("user"), dict) else data
    user_id = user_info.get("id")
    username = user_info.get("username") or user_info.get("name") or "abs_user"

    if not user_id:
        raise HTTPException(status_code=401, detail="Failed to retrieve user ID from Audiobookshelf response")

    return {
        "id": str(user_id),
        "username": str(username),
        "raw_token": token,
        "mediaProgress": user_info.get("mediaProgress") or [],
        "server_url": target_server
    }


def _save_validated_token_cache(cache_key: str, token: str, res_user: Dict[str, Any], target_server: str, now: float) -> None:
    """Updates token cache and sets last authenticated session."""
    global _token_validation_cache, _last_authenticated_session
    with _token_cache_lock:
        _token_validation_cache[cache_key] = (now, res_user)
        if len(_token_validation_cache) > 50:
            _token_validation_cache = {
                k: v for k, v in _token_validation_cache.items() if now - v[0] < TOKEN_CACHE_TTL * 2
            }
    _last_authenticated_session = {
        "token": token,
        "user": res_user,
        "time": datetime.now()
    }
    save_sync_session(token, target_server, res_user)


def validate_abs_token(token: str, server_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate the Bearer token with Audiobookshelf via GET {target_server}/api/me.
    Uses the dynamically provided server_url if passed, falling back to ABS_SERVER_URL.
    Returns user dict with id and username.
    Caches token validation results for TOKEN_CACHE_TTL seconds to avoid overwhelming Audiobookshelf.
    """
    target_server = resolve_abs_server_url(req_url=server_url)

    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization token")

    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    cache_key = f"{target_server}:{token}"
    now = time.time()
    cached = _check_cached_token(cache_key, now)
    if cached:
        return cached

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": MIME_TYPE_JSON
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

        res_user = _parse_user_response(resp.json(), token, target_server)
        _save_validated_token_cache(cache_key, token, res_user, target_server, now)
        return res_user
    except HTTPException:
        raise
    except requests.exceptions.RequestException as e:
        logger.exception(f"Error communicating with Audiobookshelf at {target_server}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Audiobookshelf server at {target_server}: {str(e)}"
        )


def _extract_auth_header_token(auth: Optional[str]) -> Optional[str]:
    """Safely extracts token from Authorization header value."""
    if not auth:
        return None
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    if len(parts) == 1:
        return parts[0].strip()
    return auth.strip()


def _extract_token_from_headers(request: Request) -> Optional[str]:
    """Extracts authentication token from request headers."""
    auth_token = _extract_auth_header_token(request.headers.get("authorization"))
    if auth_token:
        return auth_token
    for h in ("x-abs-token", "x-token", "x-api-key", "token", "apikey"):
        val = request.headers.get(h)
        if val:
            return val.strip()
    return None


def _extract_token_from_cookies(request: Request) -> Optional[str]:
    """Extracts authentication token from request cookies."""
    for c in ("abs_token", "token", "session", "connect.sid"):
        val = request.cookies.get(c)
        if val:
            return val.strip()
    return None


def _extract_token_from_query(request: Request) -> Optional[str]:
    """Extracts authentication token from request query parameters."""
    for q in ("token", "apiKey", "api_key"):
        val = request.query_params.get(q)
        if val:
            return val.strip()
    return None


def _extract_token_from_body(body_bytes: bytes) -> Optional[str]:
    """Extracts authentication token from decoded request body bytes."""
    if not body_bytes:
        return None
    try:
        body_json = json.loads(body_bytes.decode("utf-8"))
        if not isinstance(body_json, dict):
            return None
        for k in ("token", "abs_token", "apiKey", "api_key"):
            val = body_json.get(k)
            if val:
                return str(val).strip()
    except Exception:
        pass
    return None


def extract_token_from_request(request: Request, body_bytes: bytes = b"") -> Optional[str]:
    """
    Extracts authentication token from any possible location:
    Headers, Cookies, Query Params, or Request Body.
    """
    return (
        _extract_token_from_headers(request)
        or _extract_token_from_cookies(request)
        or _extract_token_from_query(request)
        or _extract_token_from_body(body_bytes)
    )


def _extract_token_from_json_dict(body: Any) -> Optional[str]:
    """Extracts token key from a parsed JSON dictionary."""
    if isinstance(body, dict):
        val = body.get("token") or body.get("abs_token") or body.get("apiKey")
        if val:
            return str(val).strip()
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
    header_token = _extract_auth_header_token(authorization) or (x_abs_token.strip() if x_abs_token else None)
    if header_token:
        return header_token

    param_token = (token.strip() if token else None) or (api_key.strip() if api_key else None)
    if param_token:
        return param_token

    try:
        body = await request.json()
        body_token = _extract_token_from_json_dict(body)
        if body_token:
            return body_token
    except Exception:
        pass

    cookie_token = request.cookies.get("abs_token")
    if cookie_token:
        return cookie_token.strip()

    raise HTTPException(status_code=401, detail="Authorization token required (via Bearer header, X-ABS-Token, or ?token= query parameter)")


async def extract_token_optional(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_abs_token: Optional[str] = Header(None, alias="x-abs-token"),
    token: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None, alias="apiKey")
) -> Optional[str]:
    """
    Optional token extractor that does not throw 401 when omitted.
    Used for local admin endpoints like configuring cutoff period.
    """
    try:
        return await extract_token_flexible(request, authorization, x_abs_token, token, api_key)
    except HTTPException:
        return None


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
    timestamp: str = Field(..., min_length=1, max_length=64, description="Snippet timestamp identifier")
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

    if _HAS_PYDANTIC_V2:
        @field_validator("timestamp", mode="before")
        @classmethod
        def validate_timestamp_format(cls, v):
            if not v or not isinstance(v, str):
                raise ValueError("Timestamp identifier is required.")
            clean = v.strip()
            if not clean:
                raise ValueError("Timestamp identifier cannot be empty.")
            if "/" in clean or "\\" in clean or ".." in clean or "\0" in clean or "." in clean:
                raise ValueError("Security violation: timestamp contains forbidden path traversal characters.")
            if not re.match(SAFE_ALPHANUMERIC_REGEX, clean) or clean.startswith("-") or len(clean) > 64:
                raise ValueError("Invalid timestamp: must contain only alphanumeric characters, underscores, and hyphens.")
            base = os.path.basename(clean)
            if base != clean or not base:
                raise ValueError("Invalid timestamp format.")
            return base
    else:
        @validator("timestamp", pre=True, always=True)
        def validate_timestamp_format(cls, v):
            if not v or not isinstance(v, str):
                raise ValueError("Timestamp identifier is required.")
            clean = v.strip()
            if not clean:
                raise ValueError("Timestamp identifier cannot be empty.")
            if "/" in clean or "\\" in clean or ".." in clean or "\0" in clean or "." in clean:
                raise ValueError("Security violation: timestamp contains forbidden path traversal characters.")
            if not re.match(SAFE_ALPHANUMERIC_REGEX, clean) or clean.startswith("-") or len(clean) > 64:
                raise ValueError("Invalid timestamp: must contain only alphanumeric characters, underscores, and hyphens.")
            base = os.path.basename(clean)
            if base != clean or not base:
                raise ValueError("Invalid timestamp format.")
            return base


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


def _parse_bookmark_list(b_data: Any) -> List[Dict[str, Any]]:
    """Safely extracts a list of bookmark dicts from JSON payload."""
    if isinstance(b_data, dict):
        return b_data.get("bookmarks") or []
    if isinstance(b_data, list):
        return b_data
    return []


def _find_matching_bookmark_target(b_list: List[Any], target_id: str) -> Tuple[Optional[str], Optional[float]]:
    """Finds matching bookmark in list and returns libraryItemId and time offset."""
    for b in b_list:
        if isinstance(b, dict):
            b_id = b.get("id")
            b_time = str(b.get("time"))
            if b_id == target_id or b_time == target_id:
                return b.get("libraryItemId"), float(b.get("time") or 0.0)
    return None, None


def _resolve_target_bookmark(
    target_server: str,
    headers: Dict[str, str],
    target_bookmark_id: Optional[str]
) -> Tuple[Optional[str], Optional[float]]:
    """Queries ABS bookmarks endpoint to resolve libraryItemId and offset for a bookmark ID."""
    if not target_bookmark_id:
        return None, None
    try:
        b_url = f"{target_server}/api/me/bookmarks"
        b_resp = _http_session.get(b_url, headers=headers, timeout=10)
        if b_resp.status_code == 200:
            b_list = _parse_bookmark_list(b_resp.json())
            return _find_matching_bookmark_target(b_list, str(target_bookmark_id))
    except Exception as b_err:
        logger.exception(f"Error querying bookmarks for target_bookmark_id: {b_err}")
    return None, None


def _extract_session_chapter(chapters: List[Dict[str, Any]], target_time: float) -> Optional[str]:
    """Finds matching chapter title for a timestamp."""
    for ch in chapters:
        start = float(ch.get("start") or 0.0)
        end = float(ch.get("end") or 0.0)
        if start <= target_time <= end:
            return ch.get("title") or ch.get("name")
    return None


def _extract_session_file_path(session: Dict[str, Any]) -> Optional[str]:
    """Extracts raw file path from active listening session audio track."""
    audio_track = session.get("audioTrack") or {}
    if isinstance(audio_track, dict):
        path = audio_track.get("metadata", {}).get("path") or audio_track.get("path")
        if path:
            return path
    return session.get("filePath") or session.get("path")


def _extract_sessions_from_data(data: Any) -> List[Dict[str, Any]]:
    """Extracts session list from API response payload."""
    if isinstance(data, dict):
        return data.get("sessions") or data.get("items") or []
    if isinstance(data, list):
        return data
    return []


def _build_session_metadata(active: Dict[str, Any], target_offset: Optional[float]) -> Dict[str, Any]:
    """Builds metadata dictionary from active listening session."""
    cur_time = float(active.get("currentTime") or 0.0)
    media_meta = active.get("mediaMetadata") or active.get("media", {}).get("metadata", {}) or {}
    display_title = active.get("displayTitle")
    book_title = media_meta.get("title") or display_title or UNKNOWN_BOOK_FALLBACK
    subtitle = media_meta.get("subtitle") or ""
    author = extract_authors(media_meta, UNKNOWN_AUTHOR_FALLBACK)

    chapters = active.get("chapters") or active.get("media", {}).get("chapters") or []
    effective_time = target_offset if target_offset is not None else cur_time
    chapter_name = _extract_session_chapter(chapters, effective_time) or UNKNOWN_CHAPTER_FALLBACK
    file_path = _extract_session_file_path(active)

    return {
        "library_item_id": active.get("libraryItemId"),
        "current_time": cur_time,
        "book_title": book_title,
        "subtitle": subtitle,
        "author": author,
        "chapter_name": chapter_name,
        "file_path": file_path,
    }


def _resolve_from_listening_sessions(
    target_server: str,
    headers: Dict[str, str],
    target_offset: Optional[float]
) -> Optional[Dict[str, Any]]:
    """Queries GET /api/me/listening-sessions to find active playback session."""
    try:
        resp = _http_session.get(f"{target_server}/api/me/listening-sessions", headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        sessions = _extract_sessions_from_data(resp.json())
        if not sessions:
            return None
        return _build_session_metadata(sessions[0], target_offset)
    except Exception as e:
        logger.exception(f"Failed to query active listening sessions: {e}")
        return None


def _resolve_from_media_progress(
    target_server: str,
    headers: Dict[str, str],
    user_info: Optional[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[float]]:
    """Fallback: queries user's most recent mediaProgress to find recently played item."""
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
        sorted_progress = sorted(progress_list, key=lambda x: x.get("lastUpdate") or 0, reverse=True)
        latest = sorted_progress[0]
        return latest.get("libraryItemId"), float(latest.get("currentTime") or 0.0)
    return None, None


def _fetch_item_details(target_server: str, headers: Dict[str, str], library_item_id: str) -> Optional[Dict[str, Any]]:
    """Fetches full item details with expanded metadata with retry."""
    for attempt in range(3):
        try:
            cur_url = (
                f"{target_server}/api/items/{library_item_id}?expanded=1"
                if attempt == 0
                else f"{target_server}/api/items/{library_item_id}"
            )
            resp = _http_session.get(cur_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"ABS status {resp.status_code} for item {library_item_id} (attempt {attempt + 1}/3)")
            time.sleep(0.4)
        except Exception as e:
            logger.exception(f"Attempt {attempt + 1}/3 failed for {library_item_id}: {e}")
            time.sleep(0.4)
    return None


def _select_audio_file(audio_files: List[Dict[str, Any]], current_time: float) -> Tuple[Optional[Dict[str, Any]], float]:
    """Selects the specific audio track spanning current_time offset."""
    if not audio_files:
        return None, 0.0
    selected = audio_files[0]
    selected_start = float(selected.get("startOffset") or 0.0)
    for af in audio_files:
        af_meta = af.get("metadata") or {}
        af_start = float(af.get("startOffset") or 0.0)
        af_dur = float(af.get("duration") or af_meta.get("duration") or 0.0)
        if af_dur > 0 and af_start <= current_time <= (af_start + af_dur):
            return af, af_start
    return selected, selected_start


def _resolve_audio_file_path(
    selected_file: Optional[Dict[str, Any]],
    media: Dict[str, Any],
    item_data: Dict[str, Any],
    existing_path: Optional[str]
) -> Optional[str]:
    """Resolves filesystem path from selected audio track or media metadata."""
    if selected_file:
        meta_path = selected_file.get("metadata", {}).get("path")
        direct_path = selected_file.get("path")
        if meta_path or direct_path:
            return meta_path or direct_path

        meta_fn = selected_file.get("metadata", {}).get("filename") or selected_file.get("filename")
        media_path = media.get("path") or item_data.get("path")
        if media_path and meta_fn:
            return f"{media_path.rstrip('/')}/{meta_fn}"

    return media.get("path") or item_data.get("path") or existing_path


def _resolve_enriched_audio_files(media: Dict[str, Any], item_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Discovers audio file tracks from media dictionary or item data payload."""
    for key in ("audioFiles", "tracks"):
        if media.get(key):
            return media[key]
        if item_data.get(key):
            return item_data[key]
    return []


def _resolve_enriched_title_and_author(
    meta: Dict[str, Any],
    item_data: Dict[str, Any],
    current_meta: Dict[str, Any]
) -> Tuple[str, str, str]:
    """Resolves enriched title, subtitle, and author."""
    book_title = current_meta.get("book_title")
    if not book_title or book_title == UNKNOWN_BOOK_FALLBACK:
        book_title = meta.get("title") or item_data.get("title") or UNKNOWN_BOOK_FALLBACK
    subtitle = current_meta.get("subtitle") or meta.get("subtitle") or ""
    author = current_meta.get("author")
    if not author or author == UNKNOWN_AUTHOR_FALLBACK:
        author = extract_authors(meta, UNKNOWN_AUTHOR_FALLBACK)
    return book_title, subtitle, author


def _enrich_metadata_from_item(
    item_data: Dict[str, Any],
    current_time: float,
    current_meta: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], float, Optional[str]]:
    """Enriches book title, subtitle, author, chapter, and file path from full item details."""
    media = item_data.get("media", {})
    meta = media.get("metadata", {})

    book_title, subtitle, author = _resolve_enriched_title_and_author(meta, item_data, current_meta)

    chapter_name = current_meta.get("chapter_name") or UNKNOWN_CHAPTER_FALLBACK
    if chapter_name == UNKNOWN_CHAPTER_FALLBACK:
        chapters = media.get("chapters") or []
        ch = _extract_session_chapter(chapters, current_time)
        if ch:
            chapter_name = ch

    audio_files = _resolve_enriched_audio_files(media, item_data)
    selected_file, af_start = _select_audio_file(audio_files, current_time)
    file_path = _resolve_audio_file_path(selected_file, media, item_data, current_meta.get("file_path"))

    enriched = {
        "book_title": book_title,
        "subtitle": subtitle,
        "author": author,
        "chapter_name": chapter_name,
        "file_path": file_path
    }
    return enriched, selected_file, af_start, file_path


def _build_stream_url(target_server: str, library_item_id: str, selected_file: Optional[Dict[str, Any]]) -> str:
    """Builds direct streaming URL from Audiobookshelf server."""
    file_ino = None
    if selected_file:
        file_ino = selected_file.get("ino") or selected_file.get("id")
    if file_ino:
        return f"{target_server}/api/items/{library_item_id}/file/{file_ino}"
    return f"{target_server}/api/items/{library_item_id}/download"


def _extract_target_request_params(req: Optional[SnippetRequest]) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Extracts libraryItemId, offset, and bookmarkId parameters from request."""
    if not req:
        return None, None, None
    t_lib = getattr(req, "library_item_id", None) or getattr(req, "libraryItemId", None)
    t_offset = getattr(req, "start_time", None) or getattr(req, "startTime", None) or getattr(req, "offset", None)
    t_bm = getattr(req, "bookmark_id", None) or getattr(req, "bookmarkId", None)
    return t_lib, t_offset, t_bm


def _resolve_target_library_and_time(
    target_server: str,
    headers: Dict[str, str],
    req: Optional[SnippetRequest],
    user_info: Optional[Dict[str, Any]]
) -> Tuple[str, float, Dict[str, Any]]:
    """Resolves target library item ID, playback time, and initial metadata from bookmark, session, or progress."""
    t_lib, t_offset, t_bm = _extract_target_request_params(req)

    bm_lib, bm_offset = _resolve_target_bookmark(target_server, headers, t_bm)
    if bm_lib:
        t_lib = bm_lib
    if bm_offset is not None:
        t_offset = bm_offset

    session_info = _resolve_from_listening_sessions(target_server, headers, t_offset)
    base_meta = session_info or {}

    library_item_id = t_lib or base_meta.get("library_item_id")
    current_time = t_offset if t_offset is not None else base_meta.get("current_time")

    if not library_item_id or current_time is None:
        prog_lib, prog_time = _resolve_from_media_progress(target_server, headers, user_info)
        if not library_item_id:
            library_item_id = prog_lib
        if current_time is None:
            current_time = prog_time

    if not library_item_id:
        raise HTTPException(
            status_code=404,
            detail="No active listening session or recent audiobook found on Audiobookshelf. Start playing an audiobook first or specify library_item_id."
        )

    return library_item_id, float(current_time or 0.0), base_meta


def _resolve_audio_file_paths(
    target_server: str,
    headers: Dict[str, str],
    library_item_id: str,
    current_time: float,
    base_meta: Dict[str, Any]
) -> Tuple[str, Optional[str], Optional[str], float]:
    """Retrieves item details, selects audio file and calculates stream/host paths."""
    item_data = _fetch_item_details(target_server, headers, library_item_id)
    selected_file = None
    selected_af_start = 0.0
    file_path = base_meta.get("file_path")

    if item_data:
        try:
            enriched, selected_file, selected_af_start, file_path = _enrich_metadata_from_item(item_data, current_time, base_meta)
            base_meta.update(enriched)
        except Exception as e:
            logger.exception(f"Error parsing item data for {library_item_id}: {e}")

    stream_url = _build_stream_url(target_server, library_item_id, selected_file)
    host_file_path = map_container_path_to_host(file_path or "", book_title=base_meta.get("book_title"))
    if (not host_file_path or not os.path.exists(host_file_path)) and stream_url:
        host_file_path = stream_url

    if not host_file_path and not stream_url:
        raise HTTPException(
            status_code=404,
            detail=f"Could not determine source audio file path for library item '{library_item_id}'. Ensure the audio library is mounted."
        )

    return host_file_path, file_path, stream_url, selected_af_start


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
        req_url=server_url
        or (getattr(req, "server_url", None))
        or (getattr(req, "serverUrl", None))
        or (getattr(req, "abs_server_url", None))
        or (getattr(req, "absServerUrl", None))
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": MIME_TYPE_JSON
    }

    library_item_id, current_time, base_meta = _resolve_target_library_and_time(target_server, headers, req, user_info)
    host_file_path, file_path, stream_url, selected_af_start = _resolve_audio_file_paths(
        target_server, headers, library_item_id, current_time, base_meta
    )

    return {
        "libraryItemId": library_item_id,
        "currentTime": current_time,
        "file_path": host_file_path,
        "raw_container_path": file_path,
        "stream_url": stream_url,
        "book_title": base_meta.get("book_title", UNKNOWN_BOOK_FALLBACK),
        "subtitle": base_meta.get("subtitle", ""),
        "author": base_meta.get("author", UNKNOWN_AUTHOR_FALLBACK),
        "chapter_name": base_meta.get("chapter_name", UNKNOWN_CHAPTER_FALLBACK),
        "startOffset": selected_af_start
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

def _resolve_unextractable_user_info(
    user_info: Optional[Dict[str, Any]],
    auth_token: Optional[str]
) -> Tuple[str, str, str]:
    info = user_info or _last_authenticated_session.get("user") or {
        "id": "default_user",
        "username": os.environ.get("DEFAULT_USERNAME", "user"),
        "raw_token": auth_token or ""
    }
    user_id = str(info.get("id") or "user_id")
    username = str(info.get("username") or "user")
    return user_id, username, sanitize_filename(username)


def _resolve_unextractable_timing(bookmark_data: Dict[str, Any]) -> Tuple[float, str]:
    t_raw = bookmark_data.get("time")
    if t_raw is None:
        t_raw = bookmark_data.get("start_time") or bookmark_data.get("startTime") or bookmark_data.get("offset")
    try:
        current_time = float(t_raw) if t_raw is not None else 0.0
    except (ValueError, TypeError):
        current_time = 0.0
    if t_raw is not None:
        duration_formatted = format_bookmarked_duration(current_time)
    else:
        duration_formatted = "N/A"
    return current_time, duration_formatted


def _resolve_unextractable_dates(created_at_raw: Any) -> Tuple[str, str]:
    formatted_datetime = None
    timestamp = None
    if created_at_raw:
        try:
            c_float = float(created_at_raw)
            if c_float > 1e11:
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
    return formatted_datetime, timestamp


def _find_chapter_name_at_time(chapters: List[Dict[str, Any]], current_time: float) -> Optional[str]:
    for ch in chapters:
        start = float(ch.get("start") or 0.0)
        end = float(ch.get("end") or 0.0)
        if start <= current_time <= end:
            return ch.get("title") or ch.get("name")
    return None


def _query_abs_item_metadata(
    server_url: Optional[str],
    auth_token: Optional[str],
    resolved_lib_id: str,
    current_time: float
) -> Tuple[str, str, str]:
    book_title, author, chapter_name = "N/A", "N/A", "N/A"
    if not (resolved_lib_id and resolved_lib_id != "N/A" and server_url and auth_token):
        return book_title, author, chapter_name

    try:
        item_url = f"{server_url.rstrip('/')}/api/items/{resolved_lib_id}?expanded=1"
        item_resp = _http_session.get(item_url, headers={"Authorization": f"Bearer {auth_token}"}, timeout=5)
        if item_resp.status_code == 200:
            item_data = item_resp.json()
            media = item_data.get("media", {})
            meta = media.get("metadata", {})
            book_title = meta.get("title") or item_data.get("title") or "N/A"
            aut = extract_authors(meta, "N/A")
            if aut and aut != UNKNOWN_AUTHOR_FALLBACK:
                author = aut
            ch_name = _find_chapter_name_at_time(media.get("chapters") or [], current_time)
            if ch_name:
                chapter_name = ch_name
    except Exception as query_err:
        logger.debug(f"Could not query item metadata from ABS for unextractable bookmark: {query_err}")
    return book_title, author, chapter_name


def _resolve_unextractable_display_title(
    book_title: str,
    bm_title: str,
    resolved_lib_id: str
) -> Tuple[str, str]:
    if book_title != "N/A":
        return book_title, sanitize_filename(book_title)
    if bm_title and bm_title != "N/A":
        display = f"{bm_title} (Book Unavailable)"
        return display, sanitize_filename(bm_title)
    if resolved_lib_id != "N/A":
        lib_prefix = resolved_lib_id[:8]
    else:
        lib_prefix = ""
    if lib_prefix:
        display = f"Unavailable Book ({lib_prefix})"
    else:
        display = "Unavailable Book"
    return display, sanitize_filename(display)


def _build_unextractable_notices(
    formatted_datetime: str,
    book_title_display: str,
    author: str,
    bookmarked_duration_formatted: str,
    chapter_name: str,
    bm_title: str,
    error_reason: str
) -> Tuple[str, str, str]:
    meta_header = (
        f"- Date / Time: {formatted_datetime}\n"
        f"- Book Title: {book_title_display}\n"
        f"- Author(s): {author}\n"
        f"- Bookmarked Duration: {bookmarked_duration_formatted}\n"
        f"- Snippet Length: N/A\n"
        f"- Chapter: {chapter_name}\n"
        f"- Bookmark Title: {bm_title}"
    )
    if error_reason:
        clean_reason = error_reason.strip()
    else:
        clean_reason = "Audio source file could not be located"
    if "detail=" in clean_reason:
        clean_reason = clean_reason.split("detail=")[-1].strip("'\"")
    notice_body = (
        f"[Notice: This bookmark was found in your Audiobookshelf library, but could not be extracted and transcribed.\n"
        f"Reason: {clean_reason}\n"
        f"Note: The audiobook or audio file may have been moved, deleted, or unmounted from the server.]"
    )
    full_transcript = f"{meta_header}\n\n{notice_body}"
    return meta_header, notice_body, full_transcript


def _resolve_unextractable_output_dir(safe_username: str, safe_book_title: str) -> Tuple[str, str]:
    target_base = VOLUME_DIR
    target_user_name = safe_username
    for cand in get_candidate_volume_dirs():
        for u in [safe_username.lower(), safe_username]:
            if os.path.isdir(os.path.join(cand, u, "bookmarks")):
                target_base = cand
                target_user_name = u
                break
    output_dir = os.path.join(target_base, target_user_name, "bookmarks", safe_book_title)
    os.makedirs(output_dir, exist_ok=True)
    return os.path.realpath(output_dir), target_user_name


def _build_unextractable_markdown_doc(
    book_title_display: str,
    author: str,
    chapter_name: str,
    timestamp: str,
    formatted_datetime: str,
    current_time: float,
    duration_formatted: str,
    resolved_lib_id: str,
    user_id: str,
    username: str,
    bm_title: str,
    meta_header: str,
    notice_body: str
) -> str:
    return f"""---
title: "{book_title_display}"
author: "{author}"
chapter: "{chapter_name}"
timestamp: "{timestamp}"
date_time: "{formatted_datetime}"
current_time: {current_time}
bookmarked_duration: "{duration_formatted}"
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
    """
    user_id, username, safe_username = _resolve_unextractable_user_info(user_info, auth_token)
    current_time, duration_formatted = _resolve_unextractable_timing(bookmark_data)
    created_at_raw = bookmark_data.get("createdAt") or bookmark_data.get("created_at")
    formatted_datetime, timestamp = _resolve_unextractable_dates(created_at_raw)

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
        logger.info(f"Skipping creation of unextractable bookmark because it was deleted by user: lib={sanitize_log_message(resolved_lib_id)}, time={current_time}")
        return {"status": "skipped", "message": MSG_BOOKMARK_PREVIOUSLY_DELETED}

    book_title, author, chapter_name = _query_abs_item_metadata(server_url, auth_token, resolved_lib_id, current_time)
    book_title_display, safe_book_title = _resolve_unextractable_display_title(book_title, bm_title, resolved_lib_id)

    meta_header, notice_body, full_transcript = _build_unextractable_notices(
        formatted_datetime, book_title_display, author, duration_formatted, chapter_name, bm_title, error_reason
    )

    real_output_dir, target_user_name = _resolve_unextractable_output_dir(safe_username, safe_book_title)
    safe_ts = os.path.basename(str(timestamp))

    md_content = _build_unextractable_markdown_doc(
        book_title_display, author, chapter_name, timestamp, formatted_datetime,
        current_time, duration_formatted, resolved_lib_id, user_id, username,
        bm_title, meta_header, notice_body
    )
    output_md = safe_write_text_file(real_output_dir, f"{safe_ts}.md", md_content)

    meta_content = {
        "id": f"{safe_book_title}-{timestamp}",
        "book_title": book_title_display,
        "author": author,
        "chapter": chapter_name,
        "timestamp": timestamp,
        "date_time": formatted_datetime,
        "start_time": current_time,
        "current_time": current_time,
        "bookmarked_duration": duration_formatted,
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
        "json_path": os.path.join(real_output_dir, f"{safe_ts}.json"),
        "extraction_method": "intercepted",
        "created_at": formatted_datetime,
        "transcription_engine": "N/A",
        "extraction_status": "unavailable",
        "bookmark_title": bm_title
    }
    safe_write_json_file(real_output_dir, f"{safe_ts}.json", meta_content)

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


def _get_fallback_user(bookmark_data: Optional[Dict[str, Any]], auth_token: Optional[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    cached_user = _last_authenticated_session.get("user")
    if cached_user:
        cached_token = auth_token or _last_authenticated_session.get("token")
        logger.info(f"Using cached authenticated user '{cached_user.get('username')}' for bookmark extraction.")
        return cached_user, cached_token

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
    return user, auth_token


def _resolve_extraction_user_and_server(
    bookmark_data: Optional[Dict[str, Any]],
    auth_token: Optional[str],
    server_url: Optional[str],
    user_info: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Optional[str], str]:
    if bookmark_data and not auth_token:
        auth_token = bookmark_data.get("_auth_token") or bookmark_data.get("auth_token") or bookmark_data.get("token")
    if bookmark_data and not server_url:
        server_url = bookmark_data.get("_server_url") or bookmark_data.get("server_url")

    target_server = resolve_abs_server_url(req_url=server_url)

    user = user_info
    if not user and auth_token:
        try:
            user = validate_abs_token(auth_token, server_url=target_server)
        except Exception as e:
            logger.exception(f"Failed to authenticate token during bookmark extraction: {e}")

    if not user:
        user, auth_token = _get_fallback_user(bookmark_data, auth_token)

    return user, auth_token, target_server


def _determine_effective_duration(
    duration: Optional[int],
    is_intercepted: bool,
    snippet_request: Optional[SnippetRequest]
) -> int:
    if duration is not None:
        return int(duration)
    if is_intercepted:
        return INTERCEPT_SNIPPET_DURATION
    if snippet_request and snippet_request.duration:
        return int(snippet_request.duration)
    return SNIPPET_DURATION


def _extract_bookmark_start_time(
    bookmark_data: Optional[Dict[str, Any]],
    custom_start: Optional[float]
) -> Optional[float]:
    if bookmark_data:
        for key in ("time", "start_time", "startTime", "offset"):
            val = bookmark_data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass
    return custom_start


def _build_extraction_request(
    library_item_id: Optional[str],
    bookmark_data: Optional[Dict[str, Any]],
    target_server: str,
    duration: Optional[int],
    snippet_request: Optional[SnippetRequest],
    custom_start: Optional[float],
    is_intercepted: bool,
) -> Tuple[int, SnippetRequest]:
    effective_duration = _determine_effective_duration(duration, is_intercepted, snippet_request)
    if snippet_request is not None:
        return effective_duration, snippet_request

    b_id = bookmark_data.get("id") if bookmark_data else None
    b_time = _extract_bookmark_start_time(bookmark_data, custom_start)
    lib_id = library_item_id or (bookmark_data.get("libraryItemId") if bookmark_data else None)

    req = SnippetRequest(
        library_item_id=str(lib_id) if lib_id else None,
        start_time=b_time,
        bookmark_id=str(b_id) if b_id else None,
        duration=effective_duration,
        server_url=target_server
    )
    return effective_duration, req


def _check_early_tombstone(
    library_item_id: Optional[str],
    bookmark_data: Optional[Dict[str, Any]],
    snippet_request: SnippetRequest,
    replace_timestamp: Optional[str],
) -> bool:
    if replace_timestamp:
        return False
    raw_cand_lib_id = library_item_id or (bookmark_data.get("libraryItemId") if bookmark_data else None)
    cand_lib_id = validate_and_sanitize_library_item_id(raw_cand_lib_id) or (raw_cand_lib_id if isinstance(raw_cand_lib_id, str) else None)
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
        logger.info(f"Skipping bookmark extraction because it was deleted by user: lib={sanitize_log_message(cand_lib_id)}, time={cand_time}")
        return True
    return False


def _check_post_resolve_tombstone(
    resolved_lib_item_id: str,
    current_time: float,
    snippet_request: SnippetRequest,
    bookmark_data: Optional[Dict[str, Any]],
    book_title: str,
    replace_timestamp: Optional[str],
) -> bool:
    if replace_timestamp:
        return False
    cand_snip_id = snippet_request.bookmark_id
    cand_title = bookmark_data.get("title") if bookmark_data else None
    cand_created_at = (bookmark_data.get("createdAt") or bookmark_data.get("created_at")) if bookmark_data else None

    if is_bookmark_tombstoned(
        lib_id=resolved_lib_item_id,
        book_time=float(current_time),
        snippet_id=cand_snip_id,
        title=cand_title,
        created_at=cand_created_at,
        book_title=book_title
    ):
        logger.info(f"Skipping bookmark extraction for '{sanitize_log_message(book_title)}' at {current_time}s because it was deleted by user")
        return True
    return False


def _resolve_extraction_session(
    library_item_id: Optional[str],
    bookmark_data: Optional[Dict[str, Any]],
    auth_token: Optional[str],
    target_server: str,
    user: Dict[str, Any],
    snippet_request: SnippetRequest,
    is_intercepted: bool,
) -> Dict[str, Any]:
    try:
        return resolve_audio_target(
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


def _extract_session_descriptors(session_state: Dict[str, Any]) -> Tuple[str, str, float, str]:
    book_title = session_state["book_title"]
    subtitle = session_state.get("subtitle") or ""
    if subtitle and subtitle.strip() and subtitle.strip().lower() not in book_title.lower():
        full_book_title = f"{book_title}: {subtitle.strip()}"
    else:
        full_book_title = book_title
    current_time = float(session_state["currentTime"])
    resolved_lib_item_id = session_state["libraryItemId"]
    return book_title, full_book_title, current_time, resolved_lib_item_id


def _calculate_snippet_start_time(
    custom_start: Optional[float],
    custom_pre_roll: Optional[float],
    snippet_request: Optional[SnippetRequest],
    bookmark_data: Optional[Dict[str, Any]],
    current_time: float,
    start_offset: float,
    is_intercepted: bool,
) -> float:
    if custom_start is not None:
        c_val = float(custom_start)
        return max(0.0, c_val - start_offset) if c_val >= start_offset else max(0.0, c_val)
    if snippet_request and (snippet_request.start_time is not None or snippet_request.startTime is not None) and not bookmark_data:
        req_start = float(snippet_request.start_time or snippet_request.startTime)
        return max(0.0, req_start - start_offset) if req_start >= start_offset else max(0.0, req_start)
    file_relative_offset = max(0.0, current_time - start_offset)
    if custom_pre_roll is not None:
        pre_roll_val = custom_pre_roll
    elif is_intercepted:
        pre_roll_val = INTERCEPT_PRE_ROLL
    else:
        pre_roll_val = SNIPPET_PRE_ROLL
    return max(0.0, file_relative_offset - pre_roll_val)


def _resolve_snippet_timestamp(replace_timestamp: Optional[str]) -> str:
    if replace_timestamp:
        timestamp = validate_and_sanitize_snippet_timestamp(replace_timestamp)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    timestamp = os.path.basename(timestamp)
    if not re.match(SAFE_ALPHANUMERIC_REGEX, timestamp):
        raise ValueError("Invalid snippet timestamp identifier.")
    return timestamp


def _find_user_volume_dir(safe_username: str) -> Tuple[str, str]:
    for cand in get_candidate_volume_dirs():
        for u in [safe_username.lower(), safe_username]:
            check_path = os.path.join(cand, u, "bookmarks")
            if os.path.isdir(check_path):
                return cand, u
    return VOLUME_DIR, safe_username


def _prepare_snippet_output_paths(
    safe_username: str,
    safe_book_title: str,
    timestamp: str,
    replace_timestamp: Optional[str],
) -> Tuple[str, str, str, str, str]:
    target_base, target_user_name = _find_user_volume_dir(safe_username)
    output_dir = os.path.join(target_base, target_user_name, "bookmarks", safe_book_title)
    os.makedirs(output_dir, exist_ok=True)
    real_output_dir = os.path.realpath(output_dir)

    output_mp3 = os.path.join(real_output_dir, f"{timestamp}.mp3")
    output_md = os.path.join(real_output_dir, f"{timestamp}.md")
    output_json = os.path.join(real_output_dir, f"{timestamp}.json")

    for check_file in [output_mp3, output_md, output_json]:
        real_file = os.path.realpath(check_file)
        if os.path.commonpath([real_output_dir, real_file]) != real_output_dir:
            raise ValueError("Security violation: resolved output path escapes user bookmarks directory.")

    if replace_timestamp:
        safe_target_ts = validate_and_sanitize_snippet_timestamp(replace_timestamp)
        for ext in ("mp3", "md", "json"):
            safe_remove_file_in_directory(real_output_dir, f"{safe_target_ts}.{ext}")

    return real_output_dir, target_user_name, output_mp3, output_md, output_json


def _run_ffmpeg_command(cmd: List[str], error_prefix: str) -> None:
    try:
        proc = run_low_priority_ffmpeg(cmd)
        if proc.returncode != 0:
            err_msg = proc.stderr[-300:] if proc.stderr else "Unknown error"
            logger.error(f"{error_prefix}: {proc.stderr}")
            raise RuntimeError(f"{error_prefix}: {err_msg}")
    except FileNotFoundError:
        raise RuntimeError(
            f"{MSG_FFMPEG_NOT_INSTALLED}"
            "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
        )


def _extract_local_audio(
    file_path: str,
    start_time: float,
    effective_duration: int,
    output_mp3: str
) -> None:
    ffmpeg_bin = get_ffmpeg_bin()
    is_mp3 = file_path.lower().endswith(".mp3")
    need_encoding = not is_mp3

    if is_mp3:
        cmd_copy = [
            ffmpeg_bin, "-y",
            "-ss", str(start_time),
            "-i", file_path,
            "-t", str(effective_duration),
            "-c", "copy",
            output_mp3
        ]
        logger.info(f"Executing ffmpeg (mp3 stream copy): {' '.join(cmd_copy)}")
        try:
            proc = run_low_priority_ffmpeg(cmd_copy)
            if proc.returncode != 0 or not os.path.exists(output_mp3) or os.path.getsize(output_mp3) == 0:
                logger.info(f"mp3 stream copy unviable (exit {proc.returncode}). Re-encoding with libmp3lame...")
                need_encoding = True
        except FileNotFoundError:
            raise RuntimeError(
                f"{MSG_FFMPEG_NOT_INSTALLED}"
                "Please install ffmpeg on your host system: sudo apt update && sudo apt install -y ffmpeg"
            )

    if need_encoding:
        encode_cmd = [
            ffmpeg_bin, "-y",
            "-ss", str(start_time),
            "-i", file_path,
            "-t", str(effective_duration),
            "-vn", "-c:a", "libmp3lame", "-q:a", "2",
            output_mp3
        ]
        ext = os.path.splitext(file_path)[1]
        logger.info(f"Executing ffmpeg (converting {ext} to mp3): {' '.join(encode_cmd)}")
        _run_ffmpeg_command(encode_cmd, "ffmpeg audio extraction failed")


def _extract_stream_audio(
    stream_url: str,
    auth_token: Optional[str],
    start_time: float,
    effective_duration: int,
    output_mp3: str
) -> None:
    ffmpeg_bin = get_ffmpeg_bin()
    stream_cmd = [
        ffmpeg_bin, "-y",
        "-headers", f"Authorization: Bearer {auth_token}\r\n",
        "-ss", str(start_time),
        "-i", stream_url,
        "-t", str(effective_duration),
        "-vn", "-c:a", "libmp3lame", "-q:a", "2",
        output_mp3
    ]
    logger.info(f"Executing ffmpeg over HTTP stream: {' '.join(stream_cmd)}")
    _run_ffmpeg_command(stream_cmd, "ffmpeg HTTP stream extraction failed")


def _extract_snippet_audio(
    file_path: str,
    stream_url: Optional[str],
    start_time: float,
    effective_duration: int,
    output_mp3: str,
    auth_token: Optional[str],
) -> Optional[str]:
    if os.path.exists(file_path):
        _extract_local_audio(file_path, start_time, effective_duration, output_mp3)
        return None

    logger.warning(f"Audio file '{file_path}' was not found on local host disk.")
    if stream_url:
        logger.info(f"Fallback: Slicing audio directly from Audiobookshelf HTTP stream: {stream_url}")
        _extract_stream_audio(stream_url, auth_token, start_time, effective_duration, output_mp3)
        return None

    return (
        f"Audio file '{file_path}' does not exist on host disk and no stream URL could be resolved. "
        f"The audiobook may have been moved, deleted, or unmounted."
    )


def _transcribe_snippet_audio(output_mp3: str) -> Tuple[str, str]:
    try:
        whisper = get_whisper_model()
        logger.info(f"Transcribing {output_mp3} with faster-whisper ({WHISPER_MODEL_NAME})...")
        segments, _ = whisper.transcribe(output_mp3, beam_size=5)
        text_segments = [segment.text.strip() for segment in segments]
        transcript_body = " ".join(text_segments).strip()
        logger.info(f"Transcription complete: {len(transcript_body)} characters")
        return transcript_body, "faster-whisper"
    except Exception as whisper_err:
        logger.exception(f"faster-whisper transcription failed ({whisper_err}). Attempting Vosk backup...")
        try:
            transcript_body = transcribe_with_vosk(output_mp3)
            logger.info(f"Vosk backup transcription complete: {len(transcript_body)} characters")
            return transcript_body, "vosk"
        except Exception as vosk_err:
            logger.exception(f"Transcription failed on both engines: Whisper ({whisper_err}), Vosk ({vosk_err})")
            return f"[Transcription failed: Whisper ({str(whisper_err)}); Vosk ({str(vosk_err)})]", "failed"


def _record_recent_extraction(
    safe_book_title: str,
    timestamp: str,
    full_book_title: str,
    author: str,
    chapter_display: str,
    username: str,
    is_intercepted_or_bookmark: bool
) -> None:
    with _extractions_lock:
        _recent_extractions.append({
            "id": f"{safe_book_title}-{timestamp}",
            "book_title": full_book_title,
            "author": author,
            "chapter": chapter_display,
            "timestamp": timestamp,
            "username": username,
            "completed_at": datetime.now().isoformat(),
            "extraction_method": "intercepted" if is_intercepted_or_bookmark else "manual"
        })
        if len(_recent_extractions) > 100:
            _recent_extractions.pop(0)


def _build_extraction_response(
    timestamp: str,
    meta_info: Dict[str, Any],
    timing_info: Dict[str, Any],
    file_paths: Dict[str, str],
    user: Dict[str, Any],
    transcript_body: str,
    engine_used: str,
    is_intercepted_or_bookmark: bool
) -> Dict[str, Any]:
    full_book_title = meta_info["full_book_title"]
    safe_book_title = meta_info["safe_book_title"]
    author = meta_info["author"]
    chapter_name = meta_info["chapter_name"]
    resolved_lib_item_id = meta_info["resolved_lib_item_id"]

    current_time = timing_info["current_time"]
    start_time = timing_info["start_time"]
    effective_duration = timing_info["effective_duration"]

    real_output_dir = file_paths["real_output_dir"]
    target_user_name = file_paths["target_user_name"]
    safe_username = file_paths["safe_username"]
    output_mp3 = file_paths["output_mp3"]
    output_json = file_paths["output_json"]

    formatted_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bookmarked_duration_formatted = format_bookmarked_duration(current_time)
    snippet_length_formatted = f"{int(round(effective_duration))} seconds"
    if chapter_name and chapter_name != UNKNOWN_CHAPTER_FALLBACK:
        chapter_display = chapter_name
    else:
        chapter_display = "N/A"
    user_id = user["id"]
    username = user["username"]

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
    written_md_path = safe_write_text_file(real_output_dir, f"{timestamp}.md", md_content)

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
        "file_path": written_md_path,
        "mp3_path": output_mp3,
        "json_path": output_json,
        "extraction_method": "intercepted" if is_intercepted_or_bookmark else "manual",
        "created_at": formatted_datetime,
        "transcription_engine": engine_used
    }
    safe_write_json_file(real_output_dir, f"{timestamp}.json", meta_content)

    _record_recent_extraction(
        safe_book_title, timestamp, full_book_title, author, chapter_display, username, is_intercepted_or_bookmark
    )

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
            "md_file": written_md_path,
            "transcript": full_transcript,
            "raw_transcript": transcript_body,
            "audio_url": f"/bookmarks/{safe_username}/{safe_book_title}/{timestamp}.mp3?v={int(os.path.getmtime(output_mp3)) if os.path.exists(output_mp3) else int(time.time())}",
            "md_url": f"/bookmarks/{safe_username}/{safe_book_title}/{timestamp}.md"
        }
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
    # Early sanitization barrier to prevent CWE-22 / CWE-73 path traversal
    if replace_timestamp is not None:
        replace_timestamp = validate_and_sanitize_snippet_timestamp(replace_timestamp)

    user, auth_token, target_server = _resolve_extraction_user_and_server(
        bookmark_data, auth_token, server_url, user_info
    )
    effective_duration, snippet_request = _build_extraction_request(
        library_item_id, bookmark_data, target_server, duration, snippet_request, custom_start, is_intercepted
    )

    if _check_early_tombstone(library_item_id, bookmark_data, snippet_request, replace_timestamp):
        return {"status": "skipped", "message": MSG_BOOKMARK_PREVIOUSLY_DELETED}

    session_state = _resolve_extraction_session(
        library_item_id, bookmark_data, auth_token, target_server, user, snippet_request, is_intercepted
    )
    if "file_path" not in session_state:
        return session_state

    book_title, full_book_title, current_time, resolved_lib_item_id = _extract_session_descriptors(session_state)

    if _check_post_resolve_tombstone(resolved_lib_item_id, current_time, snippet_request, bookmark_data, book_title, replace_timestamp):
        return {"status": "skipped", "message": MSG_BOOKMARK_PREVIOUSLY_DELETED}

    start_time = _calculate_snippet_start_time(
        custom_start, custom_pre_roll, snippet_request, bookmark_data, current_time,
        float(session_state.get("startOffset") or 0.0), is_intercepted
    )
    timestamp = _resolve_snippet_timestamp(replace_timestamp)

    safe_book_title = sanitize_filename(book_title)
    safe_username = sanitize_filename(user["username"])
    real_output_dir, target_user_name, output_mp3, _, output_json = _prepare_snippet_output_paths(
        safe_username, safe_book_title, timestamp, replace_timestamp
    )

    audio_error = _extract_snippet_audio(
        session_state["file_path"], session_state.get("stream_url"),
        start_time, effective_duration, output_mp3, auth_token or user.get("raw_token")
    )
    if audio_error:
        if bookmark_data or is_intercepted:
            logger.warning(f"{audio_error}. Saving unextractable bookmark fallback...")
            return create_unextractable_bookmark_snippet(
                library_item_id=resolved_lib_item_id,
                bookmark_data=bookmark_data or {"title": full_book_title, "time": current_time},
                auth_token=auth_token or user.get("raw_token"),
                server_url=target_server,
                error_reason=audio_error,
                user_info=user
            )
        raise RuntimeError(audio_error)

    transcript_body, engine_used = _transcribe_snippet_audio(output_mp3)

    return _build_extraction_response(
        timestamp=timestamp,
        meta_info={
            "full_book_title": full_book_title,
            "safe_book_title": safe_book_title,
            "author": session_state["author"],
            "chapter_name": session_state["chapter_name"],
            "resolved_lib_item_id": resolved_lib_item_id,
        },
        timing_info={
            "current_time": current_time,
            "start_time": start_time,
            "effective_duration": effective_duration,
        },
        file_paths={
            "real_output_dir": real_output_dir,
            "target_user_name": target_user_name,
            "safe_username": safe_username,
            "output_mp3": output_mp3,
            "output_json": output_json,
        },
        user=user,
        transcript_body=transcript_body,
        engine_used=engine_used,
        is_intercepted_or_bookmark=bool(bookmark_data or is_intercepted)
    )


# ==============================================================================
# Autonomous Background Bookmark Sync Engine
# Continuously queries Audiobookshelf for bookmarks created on Android/iOS/Web
# and extracts/transcribes any that do not yet exist on disk without requiring
# any reverse proxy modifications.
# ==============================================================================

_snippet_meta_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_snippet_cache_lock = threading.Lock()


def _read_cached_snippet_meta(full_path: str) -> Optional[Dict[str, Any]]:
    """Loads JSON metadata for a snippet with mtime-based thread-safe cache."""
    global _snippet_meta_cache
    try:
        mtime = os.path.getmtime(full_path)
        with _snippet_cache_lock:
            if full_path in _snippet_meta_cache and _snippet_meta_cache[full_path][0] == mtime:
                return _snippet_meta_cache[full_path][1]
            with open(full_path, "r", encoding="utf-8") as jf:
                meta = json.load(jf)
            _snippet_meta_cache[full_path] = (mtime, meta)
            return meta
    except Exception:
        return None


def _parse_snippet_meta_record(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts duration and timing fields from snippet metadata dictionary."""
    return {
        "library_item_id": meta.get("library_item_id"),
        "current_time": float(meta.get("current_time", -999)),
        "start_time": float(meta.get("start_time", -999)),
        "duration": float(meta.get("duration", 60))
    }


def _scan_book_dir_for_extractions(full_b_dir: str) -> List[Dict[str, Any]]:
    """Discovers snippet metadata JSON files within a single audiobook directory."""
    try:
        filenames = os.listdir(full_b_dir)
    except Exception:
        return []

    records: List[Dict[str, Any]] = []
    for f in filenames:
        if not f.endswith(JSON_FILE_EXTENSION) or f.startswith("."):
            continue
        full_path = os.path.join(full_b_dir, f)
        meta = _read_cached_snippet_meta(full_path)
        if meta:
            records.append(_parse_snippet_meta_record(meta))
    return records


def _scan_user_sub_dir_for_extractions(sub: str) -> List[Dict[str, Any]]:
    """Scans all audiobook directories inside a candidate user folder."""
    if not os.path.isdir(sub):
        return []
    try:
        book_dirs = os.listdir(sub)
    except Exception:
        return []

    records: List[Dict[str, Any]] = []
    for book_dir in book_dirs:
        if book_dir.lower() in ("bookmarks", "snippets"):
            continue
        full_b_dir = os.path.join(sub, book_dir)
        if os.path.isdir(full_b_dir):
            records.extend(_scan_book_dir_for_extractions(full_b_dir))
    return records


def _collect_user_extraction_sub_dirs(safe_username: str, user_id: str) -> List[str]:
    """Generates all potential directory paths containing snippets for the user."""
    sub_dirs: List[str] = []
    candidates = [safe_username, safe_username.lower(), str(user_id)]
    for root in get_candidate_volume_dirs():
        for u in candidates:
            sub_dirs.append(os.path.join(root, u, "bookmarks"))
            sub_dirs.append(os.path.join(root, u))
    return sub_dirs


def get_cached_existing_extractions(safe_username: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Returns existing extractions with mtime-based in-memory caching.
    Prevents repeated file opens and JSON parsing of hundreds of files on the SSD during every sync loop.
    """
    existing_extractions: List[Dict[str, Any]] = []
    sub_dirs = _collect_user_extraction_sub_dirs(safe_username, user_id)
    for sub in sub_dirs:
        existing_extractions.extend(_scan_user_sub_dir_for_extractions(sub))
    return existing_extractions


def _resolve_sync_credentials(force_token: Optional[str] = None, force_server: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Resolves and normalizes auth token and target server URL from memory or saved session."""
    token = force_token or _last_authenticated_session.get("token")
    target_server = resolve_abs_server_url(req_url=force_server)

    if not token:
        persisted = load_sync_session()
        if persisted and persisted.get("token"):
            token = persisted["token"]
            if persisted.get("server_url") and not force_server:
                target_server = resolve_abs_server_url(req_url=persisted["server_url"])

    if token:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

    return token, target_server


def _fetch_user_sync_info(target_server: str, token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Queries user profile from Audiobookshelf, invalidating expired sessions on 401."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": MIME_TYPE_JSON
    }
    url = f"{target_server}/api/me"
    resp = _http_session.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        err_msg = f"Audiobookshelf server at {target_server} returned HTTP {resp.status_code}"
        logger.warning(f"[Auto-Sync] {err_msg}")
        if resp.status_code == 401:
            logger.warning(
                "[Auto-Sync] [!] Audiobookshelf authentication failed (HTTP 401 Unauthorized). "
                "The user session token has expired or is invalid. Cached session has been cleared. "
                "Please open the Auth Modal in the Web Dashboard to log in or renew your session."
            )
            invalidate_sync_session(reason="HTTP 401 Unauthorized from /api/me")
            err_msg = (
                "Audiobookshelf session expired (HTTP 401). "
                "Please open the Auth Modal in the Web Dashboard to log in."
            )
        return None, err_msg

    user_resp = resp.json()
    user_info = user_resp.get("user") if isinstance(user_resp.get("user"), dict) else user_resp
    return user_info, None


def _parse_bookmark_data_point(bm_data: Dict[str, Any], default_lib_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Extracts valid libraryItemId, time offset, and metadata from raw bookmark payload."""
    if not isinstance(bm_data, dict):
        return None
    t_raw = bm_data.get("time")
    if t_raw is None:
        t_raw = bm_data.get("start_time") or bm_data.get("startTime") or bm_data.get("offset")
    if t_raw is None:
        return None
    try:
        bm_time = float(t_raw)
    except (ValueError, TypeError):
        return None

    lib_id = bm_data.get("libraryItemId") or default_lib_id
    if not lib_id:
        return None

    return {
        "id": bm_data.get("id"),
        "libraryItemId": str(lib_id),
        "time": bm_time,
        "title": bm_data.get("title") or "",
        "createdAt": bm_data.get("createdAt") or bm_data.get("created_at") or bm_data.get("timestamp")
    }


def _collect_listening_session_bookmarks(target_server: str, headers: Dict[str, str]) -> List[Tuple[Dict[str, Any], Optional[str]]]:
    """Queries active listening sessions to discover recently created bookmarks."""
    raw_candidates = []
    try:
        sess_resp = _http_session.get(f"{target_server}/api/me/listening-sessions", headers=headers, timeout=5)
        if sess_resp.status_code == 200:
            s_data = sess_resp.json()
            s_list = s_data if isinstance(s_data, list) else (s_data.get("sessions") or [])
            for s in s_list:
                lib_id = s.get("libraryItemId") or s.get("id")
                for bm in (s.get("bookmarks") or []):
                    raw_candidates.append((bm, lib_id))
        elif sess_resp.status_code != 401:
            logger.debug(f"[Auto-Sync] /api/me/listening-sessions returned status {sess_resp.status_code}")
    except Exception as sess_err:
        logger.debug(f"[Auto-Sync] Could not query listening sessions: {sess_err}")
    return raw_candidates


def _collect_raw_user_bookmarks(
    user_info: Dict[str, Any],
    target_server: str,
    headers: Dict[str, str]
) -> List[Tuple[Dict[str, Any], Optional[str]]]:
    """Aggregates unparsed bookmark records from user profile, media progress, and listening sessions."""
    raw_list: List[Tuple[Dict[str, Any], Optional[str]]] = []
    for bm in (user_info.get("bookmarks") or []):
        raw_list.append((bm, None))
    for prog in (user_info.get("mediaProgress") or []):
        lib_id = prog.get("libraryItemId")
        for bm in (prog.get("bookmarks") or []):
            raw_list.append((bm, lib_id))
    raw_list.extend(_collect_listening_session_bookmarks(target_server, headers))
    return raw_list


def _collect_all_bookmark_candidates(
    user_info: Dict[str, Any],
    target_server: str,
    headers: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Applies installation cutoff and tombstone filters to gathered bookmark candidates."""
    raw_list = _collect_raw_user_bookmarks(user_info, target_server, headers)
    candidate_bookmarks = []
    seen_keys = set()
    skipped_prior_count = 0
    skipped_tombstone_count = 0

    for bm_data, default_lib in raw_list:
        parsed = _parse_bookmark_data_point(bm_data, default_lib)
        if not parsed:
            continue

        created_at_raw = parsed["createdAt"]
        if not is_bookmark_after_installation_cutoff(created_at_raw):
            skipped_prior_count += 1
            continue

        if is_bookmark_tombstoned(
            lib_id=parsed["libraryItemId"],
            book_time=parsed["time"],
            snippet_id=parsed["id"],
            title=parsed["title"],
            created_at=created_at_raw
        ):
            skipped_tombstone_count += 1
            continue

        key = (parsed["libraryItemId"], round(parsed["time"], 1))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidate_bookmarks.append(parsed)

    return candidate_bookmarks, skipped_prior_count, skipped_tombstone_count


def _is_bookmark_already_extracted(cand: Dict[str, Any], existing_extractions: List[Dict[str, Any]]) -> bool:
    """Verifies whether audio or metadata for the bookmark already exists on local disk."""
    c_lib_id = cand["libraryItemId"]
    c_time = cand["time"]
    for ext in existing_extractions:
        if ext["library_item_id"] and ext["library_item_id"] == c_lib_id:
            if abs(ext["current_time"] - c_time) <= 15.0:
                return True
            if ext["start_time"] <= c_time <= (ext["start_time"] + ext["duration"]):
                return True
        elif abs(ext["current_time"] - c_time) <= 8.0:
            return True
    return False


def _filter_unextracted_bookmarks(
    candidate_bookmarks: List[Dict[str, Any]],
    username: str,
    user_id: str
) -> List[Dict[str, Any]]:
    """Filters candidate bookmarks against disk cache to produce the unextracted subset."""
    safe_username = sanitize_filename(username)
    existing_extractions = get_cached_existing_extractions(safe_username, str(user_id))
    return [c for c in candidate_bookmarks if not _is_bookmark_already_extracted(c, existing_extractions)]


def _sync_single_bookmark(
    bm: Dict[str, Any],
    token: str,
    target_server: str,
    user_info: Dict[str, Any]
) -> bool:
    """Performs single bookmark audio extraction and transcription with fallback to unextractable stub."""
    lib_id = bm["libraryItemId"]
    b_time = bm["time"]
    b_title = bm["title"] or f"Bookmark @ {format_bookmarked_duration(b_time)}"

    try:
        process_bookmark_extraction(
            library_item_id=lib_id,
            bookmark_data=bm,
            auth_token=token,
            server_url=target_server,
            duration=INTERCEPT_SNIPPET_DURATION,
            custom_pre_roll=INTERCEPT_PRE_ROLL,
            is_intercepted=True
        )
        logger.info(f"[Auto-Sync] [✓] Successfully extracted '{b_title}'")
        return True
    except Exception as ex:
        logger.exception(f"[Auto-Sync] [✗] Failed extracting bookmark {bm}: {ex}")
        logger.info(f"[Auto-Sync] Creating unextractable fallback bookmark for '{b_title}'...")
        try:
            create_unextractable_bookmark_snippet(
                library_item_id=lib_id,
                bookmark_data=bm,
                auth_token=token,
                server_url=target_server,
                error_reason=str(ex),
                user_info=user_info
            )
            logger.info(f"[Auto-Sync] [✓] Recorded unextractable bookmark fallback for '{b_title}'")
            return True
        except Exception as fb_err:
            logger.exception(f"[Auto-Sync] [✗] Could not write unextractable fallback: {fb_err}")
            return False


def _update_sync_state_configuration(skipped_prior_count: int, skipped_tombstone_count: int):
    """Refreshes global sync state dictionary with current installation and cutoff configuration."""
    _sync_state["skipped_before_cutoff"] = skipped_prior_count
    _sync_state["skipped_tombstoned"] = skipped_tombstone_count
    _sync_state["installation_date"] = INSTALLATION_CONFIG.get("installation_date")
    _sync_state["cutoff_datetime"] = INSTALLATION_CONFIG.get("cutoff_datetime")
    _sync_state["cutoff_mode"] = INSTALLATION_CONFIG.get("cutoff_mode", "from_now")
    _sync_state["custom_cutoff_date"] = INSTALLATION_CONFIG.get("custom_date")
    _sync_state["installed_at"] = INSTALLATION_CONFIG.get("installed_at")


def _build_no_candidate_bookmarks_result(skipped_prior_count: int, skipped_tombstone_count: int) -> Dict[str, Any]:
    """Builds clean response payload when zero bookmarks are within the active cutoff window."""
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
        token, target_server = _resolve_sync_credentials(force_token, force_server)
        if not token:
            _sync_state["is_syncing"] = False
            return {
                "status": "idle",
                "message": "No active user session. Please log in via the Web Dashboard Auth Modal to enable automatic bookmark sync.",
                "unextracted_count": 0,
                "processed_count": 0
            }

        user_info, err_msg = _fetch_user_sync_info(target_server, token)
        if not user_info:
            _sync_state["last_error"] = err_msg
            _sync_state["is_syncing"] = False
            return {"status": "error", "message": err_msg}

        username = user_info.get("username") or user_info.get("name") or "user"
        user_id = user_info.get("id") or "user_id"

        _last_authenticated_session["token"] = token
        _last_authenticated_session["user"] = {
            "id": str(user_id),
            "username": str(username),
            "raw_token": token,
            "server_url": target_server
        }
        save_sync_session(token, target_server, _last_authenticated_session["user"])

        headers = {"Authorization": f"Bearer {token}", "Content-Type": MIME_TYPE_JSON}
        candidates, skipped_prior, skipped_tomb = _collect_all_bookmark_candidates(user_info, target_server, headers)
        _update_sync_state_configuration(skipped_prior, skipped_tomb)

        if not candidates:
            return _build_no_candidate_bookmarks_result(skipped_prior, skipped_tomb)

        unextracted = _filter_unextracted_bookmarks(candidates, username, str(user_id))
        if not unextracted:
            _sync_state["last_synced_at"] = datetime.now().isoformat()
            _sync_state["is_syncing"] = False
            return {
                "status": "ok",
                "message": f"All {len(candidates)} Audiobookshelf bookmarks are already synchronized.",
                "total_bookmarks": len(candidates),
                "unextracted_count": 0,
                "processed_count": 0
            }

        logger.info(f"[Auto-Sync] Found {len(unextracted)} unextracted bookmark(s) on Audiobookshelf for '{username}'. Processing in background...")
        processed_count = 0
        for idx, bm in enumerate(unextracted):
            b_title = bm["title"] or f"Bookmark @ {format_bookmarked_duration(bm['time'])}"
            _sync_state["current_item"] = f"[{idx + 1}/{len(unextracted)}] {b_title}"

            if _sync_single_bookmark(bm, token, target_server, user_info):
                processed_count += 1
                _sync_state["total_synced"] += 1

            time.sleep(1.5)

        _sync_state["last_synced_at"] = datetime.now().isoformat()
        _sync_state["current_item"] = None
        _sync_state["is_syncing"] = False

        return {
            "status": "success",
            "message": f"Auto-Sync completed. Processed {processed_count} of {len(unextracted)} bookmarks created on or after {INSTALLATION_CONFIG.get('installation_date')} ({skipped_prior} older bookmarks preserved/skipped).",
            "total_bookmarks": len(candidates),
            "skipped_before_cutoff": skipped_prior,
            "skipped_tombstoned": skipped_tomb,
            "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
            "installation_date": INSTALLATION_CONFIG.get("installation_date"),
            "unextracted_count": len(unextracted),
            "processed_count": processed_count
        }

    except Exception as e:
        logger.exception(f"[Auto-Sync] Sync cycle failed: {e}")
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


def _build_socket_urls(server_url: str, token: str) -> Tuple[str, str]:
    """Constructs WebSocket endpoint and origin headers from server URL and auth token."""
    normalized = normalize_abs_url(server_url)
    if normalized.startswith(HTTPS_PROTOCOL_PREFIX):
        ws_url = WSS_PROTOCOL_PREFIX + normalized[len(HTTPS_PROTOCOL_PREFIX):].rstrip("/")
    elif normalized.startswith(HTTP_PROTOCOL_PREFIX):
        ws_url = WS_PROTOCOL_PREFIX + normalized[len(HTTP_PROTOCOL_PREFIX):].rstrip("/")
    elif normalized.startswith((WSS_PROTOCOL_PREFIX, WS_PROTOCOL_PREFIX)):
        ws_url = normalized.rstrip("/")
    else:
        ws_url = f"{WSS_PROTOCOL_PREFIX}{normalized.rstrip('/')}"

    socket_url = f"{ws_url}/socket.io/?EIO=4&transport=websocket&token={quote_plus(str(token))}"
    origin_protocol = HTTPS_PROTOCOL_PREFIX if ws_url.startswith(WSS_PROTOCOL_PREFIX) else HTTP_PROTOCOL_PREFIX
    origin_host = f"{origin_protocol}{ws_url.split(SCHEME_DELIMITER)[1].split('/')[0]}"
    return socket_url, origin_host


async def _perform_engineio_handshake(ws: Any, token: str) -> Tuple[bool, str, Optional[float]]:
    """Executes Engine.IO and Socket.IO handshake and emits legacy authentication packet."""
    try:
        first_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        if isinstance(first_msg, bytes):
            first_msg = first_msg.decode("utf-8", errors="ignore")
    except Exception as e:
        return False, f"Timeout or error awaiting Engine.IO open packet: {e}", None

    if not first_msg.startswith("0"):
        return False, f"Unexpected initial packet from server: {first_msg[:50]}", None

    token_json = json.dumps(str(token))
    await ws.send(f'40{{"token":{token_json}}}')

    try:
        conn_resp = await asyncio.wait_for(ws.recv(), timeout=6.0)
        if isinstance(conn_resp, bytes):
            conn_resp = conn_resp.decode("utf-8", errors="ignore")
        if conn_resp.startswith("44"):
            return False, f"Socket.IO connection rejected by server (44): {conn_resp[:80]}", 60.0
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        return False, f"Error during connect handshake: {e}", None

    await ws.send(f'42["auth",{token_json}]')
    return True, "", None


def _handle_socket_event_payload(payload: Any, on_sync: Callable[[], None]) -> Tuple[bool, Optional[str], Optional[float]]:
    """Handles parsed Socket.IO event payloads, updating backoff or scheduling bookmark extraction."""
    if not isinstance(payload, list) or len(payload) == 0:
        return True, None, None

    event_name = str(payload[0]).lower()
    if event_name in ["authenticated", "init", "user_online"]:
        logger.info(f"[Socket.IO Listener] Audiobookshelf session verified ({event_name}).")
        return True, None, 5.0

    if event_name == "auth_failed":
        err_detail = payload[1] if len(payload) > 1 else "Invalid or expired token"
        logger.warning(
            f"[Socket.IO Listener] Authentication rejected by Audiobookshelf: {err_detail}. "
            "Note: Audiobookshelf WebSockets require a user session JWT (created via web login) "
            "rather than an API token. Background periodic sync will continue managing bookmarks."
        )
        return False, "Authentication rejected", 300.0

    if any(k in event_name for k in ["bookmark", "user_updated", "user_item", "item_updated", "session"]):
        logger.info(f"[Socket.IO Listener] Audiobookshelf real-time event received: {event_name}")
        on_sync()

    return True, None, None


async def _process_socket_message(ws: Any, msg: str, on_sync: Callable[[], None]) -> Tuple[bool, Optional[str], Optional[float]]:
    """Parses incoming WebSocket packet and executes appropriate Engine.IO/Socket.IO response."""
    if msg == "2":
        await ws.send("3")
        return True, None, None
    if msg.startswith("2"):
        await ws.send("3" + msg[1:])
        return True, None, None
    if msg.startswith(("3", "40")):
        return True, None, None
    if msg.startswith(("41", "44")):
        reason = f"Socket.IO connection rejected or closed by server: {msg[:80]}"
        logger.warning(f"[Socket.IO Listener] {reason}")
        return False, reason, None
    if msg.startswith("42"):
        try:
            payload = json.loads(msg[2:])
            return _handle_socket_event_payload(payload, on_sync)
        except Exception as parse_err:
            logger.debug(f"[Socket.IO Listener] Failed parsing payload: {parse_err}")
    return True, None, None


def _compute_socket_reconnect_backoff(duration: float, current_backoff: float) -> float:
    """Calculates adaptive reconnection delay based on connection longevity."""
    if duration > 45:
        return 5.0
    if duration < 2.0:
        new_backoff = max(current_backoff * 1.5, 30.0)
        logger.warning(
            f"[Socket.IO Listener] Connection dropped rapidly after handshake ({duration:.2f}s). "
            f"Possible causes: invalid/expired token or reverse proxy WebSocket timeout. "
            f"Backing off for {int(new_backoff)}s."
        )
        return new_backoff
    return min(current_backoff * 1.8, 60.0)


def _decode_socket_message(raw_msg: Any) -> Optional[str]:
    """Safely decodes binary or string websocket packet."""
    if isinstance(raw_msg, bytes):
        try:
            return raw_msg.decode("utf-8", errors="ignore")
        except Exception:
            return None
    return raw_msg if raw_msg else None


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
        debounce_task = safe_create_background_task(_debounced(), name="socket_debounce_sync")
        self._debounce_task = debounce_task

    def _resolve_active_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Retrieves and normalizes token and server URL from persistent storage or cache."""
        session = load_sync_session()
        token = session.get("token") if session else None
        server_url = session.get("server_url") if session else None

        if not token:
            token = _last_authenticated_session.get("token")
        if not server_url:
            server_url = resolve_abs_server_url()
        return token, server_url

    async def _wait_for_credentials(self):
        """Suspends connection attempts until user credentials are provided via wake event or timeout."""
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass

    async def _wait_backoff_sleep(self, seconds: float):
        """Sleeps for backoff duration while remaining alert to manual wake triggers."""
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _listen_message_loop(self, ws: Any) -> Tuple[str, Optional[float]]:
        """Processes messages on an active WebSocket connection until disconnected or stopped."""
        disconnect_reason = "Connection closed"
        custom_backoff = None

        while self._running:
            try:
                raw_msg = await ws.recv()
            except Exception as recv_err:
                close_code = getattr(ws, "close_code", None)
                close_reason = getattr(ws, "close_reason", None)
                disconnect_reason = f"Recv error ({recv_err}), code: {close_code}, reason: {close_reason}"
                break

            msg = _decode_socket_message(raw_msg)
            if not msg:
                continue

            should_continue, reason, backoff = await _process_socket_message(ws, msg, self._schedule_sync)
            if backoff is not None:
                custom_backoff = backoff
            if not should_continue:
                if reason:
                    disconnect_reason = reason
                break

        return disconnect_reason, custom_backoff

    async def _run_connection(self, socket_url: str, origin_host: str, token: str) -> Tuple[float, str, Optional[float]]:
        """Connects to Audiobookshelf WebSocket, executes handshake, and enters message loop."""
        connect_kwargs = {"ping_interval": None, "ping_timeout": None, "max_size": 10 * 1024 * 1024}
        headers_dict = {
            "Authorization": f"Bearer {token}",
            "Origin": origin_host,
            "User-Agent": "Audiobookshelf-Bookmarks-Manager/2.1.0",
        }

        connected_at = 0.0
        disconnect_reason = "Connection closed"
        custom_backoff = None

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

                ok, reason, backoff = await _perform_engineio_handshake(ws, token)
                if not ok:
                    return connected_at, reason, backoff

                logger.info("[Socket.IO Listener] Handshake complete and auth event emitted.")
                disconnect_reason, custom_backoff = await self._listen_message_loop(ws)

        except Exception as conn_err:
            disconnect_reason = f"Connection error: {conn_err}"
        finally:
            self._connected = False

        return connected_at, disconnect_reason, custom_backoff

    async def start(self):
        """Main background loop handling connection, heartbeat, and real-time events with robust backoff."""
        self._running = True
        logger.info("[Socket.IO Listener] Background listener initialized.")
        await asyncio.sleep(6)

        backoff_seconds = 5.0

        while self._running:
            token, server_url = self._resolve_active_credentials()
            if not token or not server_url:
                await self._wait_for_credentials()
                continue

            if websockets is None:
                logger.warning("[Socket.IO Listener] websockets package is not installed; falling back to periodic sync polling only.")
                await asyncio.sleep(60)
                continue

            socket_url, origin_host = _build_socket_urls(server_url, token)
            connected_at, disconnect_reason, custom_backoff = await self._run_connection(socket_url, origin_host, token)

            if custom_backoff is not None:
                backoff_seconds = custom_backoff
            else:
                duration = time.time() - connected_at if connected_at > 0 else 0
                backoff_seconds = _compute_socket_reconnect_backoff(duration, backoff_seconds)

            logger.info(f"[Socket.IO Listener] {disconnect_reason}. Reconnecting in {int(backoff_seconds)}s...")
            await self._wait_backoff_sleep(backoff_seconds)


_socket_listener = AbsSocketIoListener()


@app.post("/api/user/sync-bookmarks", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/sync-bookmarks", responses=COMMON_AUTH_RESPONSES)
async def trigger_bookmark_sync(
    request: Request,
    raw_token: Optional[str] = Depends(extract_token_flexible)
):
    """
    Triggers an immediate background sync check for the user's bookmarks across Audiobookshelf.
    Extracts and transcribes any newly detected bookmarks without requiring reverse proxy changes.
    Dispatches task asynchronously so the HTTP request never blocks or times out.
    """
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url") or request.headers.get("X-ABS-URL"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    if "_socket_listener" in globals() and _socket_listener is not None:
        _socket_listener.wake()

    wait_for_completion = request.query_params.get("wait", "").lower() in ("true", "1", "yes")

    if wait_for_completion:
        result = await asyncio.to_thread(run_bookmark_sync_cycle, force_token=raw_token, force_server=server_url)
        return result

    with _sync_lock:
        already_syncing = _sync_state.get("is_syncing", False)

    if already_syncing:
        return {
            "status": "in_progress",
            "message": "A sync cycle is already currently running.",
            "current_item": _sync_state.get("current_item")
        }

    # Dispatch sync cycle in background thread so HTTP response returns immediately without timing out
    sync_task = safe_create_background_task(
        asyncio.to_thread(run_bookmark_sync_cycle, force_token=raw_token, force_server=server_url),
        name="manual_sync_cycle"
    )
    _background_tasks.add(sync_task)

    return {
        "status": "started",
        "message": "Bookmark sync cycle initiated in background.",
        "state": _sync_state
    }


@app.get("/api/user/sync-status", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/sync-status", responses=COMMON_AUTH_RESPONSES)
def get_sync_status():
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


def _extract_header_url(request: Request) -> Optional[str]:
    for key in ("X-ABS-Server-Url", "X-Server-Url", "X-ABS-URL"):
        val = request.headers.get(key)
        if val:
            return val
    return None


def _extract_payload_server_url(payload: Optional[SnippetRequest]) -> Optional[str]:
    if not payload:
        return None
    for attr in ("server_url", "serverUrl", "abs_server_url", "absServerUrl"):
        val = getattr(payload, attr, None)
        if val:
            return val
    return None


def _resolve_snippet_request_params(payload: Optional[SnippetRequest], request: Request) -> Tuple[Optional[str], Optional[float], int]:
    """Resolves server URL, custom start time, and snippet duration from request."""
    server_url = _extract_payload_server_url(payload)
    if not server_url:
        server_url = _extract_header_url(request)
    if not server_url:
        server_url = request.query_params.get("server_url")
    if not server_url:
        server_url = request.query_params.get("serverUrl")

    resolved_server_url = resolve_abs_server_url(req_url=server_url)

    custom_start: Optional[float] = None
    duration: int = SNIPPET_DURATION
    if payload:
        raw_start = payload.start_time
        if raw_start is None:
            raw_start = payload.startTime
        if raw_start is None:
            raw_start = payload.offset
        if raw_start is not None:
            custom_start = float(raw_start)
        if payload.duration:
            duration = payload.duration

    return resolved_server_url, custom_start, duration


# --- Manual Trigger & REST API Endpoints (Web App, Scripts, Automation) ---

@app.post("/api/snippet", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/extract", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/bookmark/extract", responses=COMMON_AUTH_RESPONSES)
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
    server_url, custom_start, duration = _resolve_snippet_request_params(payload, request)
    user = validate_abs_token(raw_token, server_url=server_url)

    try:
        result = await asyncio.to_thread(
            process_bookmark_extraction,
            library_item_id=payload.library_item_id if payload else None,
            auth_token=raw_token,
            server_url=server_url,
            duration=duration,
            snippet_request=payload,
            user_info=user,
            custom_start=custom_start
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error during snippet extraction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _read_bookmark_file_metadata(md_path: str, json_path: str, book_dir: str) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if os.path.exists(json_path):
        metadata = safe_read_json_file(os.path.dirname(json_path), os.path.basename(json_path)) or {}

    if not metadata:
        try:
            raw_md = safe_read_text_file(os.path.dirname(md_path), os.path.basename(md_path)) or ""
            parsed = parse_frontmatter(raw_md)
            metadata = {
                "book_title": parsed.get("title") or book_dir.replace("_", " "),
                "author": parsed.get("author") or UNKNOWN_AUTHOR_FALLBACK,
                "chapter": parsed.get("chapter") or "",
                "start_time": float(parsed.get("start_time") or 0.0),
                "duration": int(parsed.get("duration") or 60),
                "transcript": parsed.get("body", "").split(MARKDOWN_TRANSCRIPT_HEADER, 1)[-1].strip()
            }
        except Exception:
            metadata = {"book_title": book_dir, "transcript": ""}
    return metadata


def _format_bookmark_transcript_text(metadata: Dict[str, Any], book_dir: str, base_name: str) -> str:
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
    return transcript_text


def _build_bookmark_item(
    full_id: str,
    book_dir: str,
    base_name: str,
    fname: str,
    md_path: str,
    mp3_path: str,
    has_mp3: bool,
    metadata: Dict[str, Any],
    cur_time_val: float,
    transcript_text: str,
    u: str,
    username: str
) -> Dict[str, Any]:
    mp3_mtime = int(os.path.getmtime(mp3_path)) if (has_mp3 and os.path.exists(mp3_path)) else int(time.time())
    return {
        "id": full_id,
        "book_title": metadata.get("book_title") or book_dir,
        "author": metadata.get("author") or UNKNOWN_AUTHOR_FALLBACK,
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
    }


def _process_bookmark_markdown_file(
    full_book_path: str,
    book_dir: str,
    fname: str,
    u: str,
    username: str,
    seen_ids: Set[str]
) -> Optional[Dict[str, Any]]:
    if not fname.endswith(".md"):
        return None

    base_name = fname[:-3]
    item_unique_key = f"{book_dir.lower()}-{base_name}"
    if item_unique_key in seen_ids:
        return None

    md_path = os.path.join(full_book_path, fname)
    mp3_path = os.path.join(full_book_path, f"{base_name}.mp3")
    json_path = os.path.join(full_book_path, f"{base_name}{JSON_FILE_EXTENSION}")

    metadata = _read_bookmark_file_metadata(md_path, json_path, book_dir)
    has_mp3 = os.path.exists(mp3_path)
    transcript_text = _format_bookmark_transcript_text(metadata, book_dir, base_name)

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
        return None

    seen_ids.add(item_unique_key)
    return _build_bookmark_item(
        full_id, book_dir, base_name, fname, md_path, mp3_path, has_mp3,
        metadata, cur_time_val, transcript_text, u, username
    )


def _scan_book_directory_for_bookmarks(
    full_book_path: str,
    book_dir: str,
    u: str,
    username: str,
    seen_ids: Set[str]
) -> List[Dict[str, Any]]:
    results = []
    try:
        entries = sorted(os.listdir(full_book_path), reverse=True)
    except OSError:
        return results

    for fname in entries:
        item = _process_bookmark_markdown_file(full_book_path, book_dir, fname, u, username, seen_ids)
        if item:
            results.append(item)
    return results


def _scan_user_directory_bookmarks(
    u_dir: str,
    u: str,
    username: str,
    seen_ids: Set[str]
) -> List[Dict[str, Any]]:
    results = []
    if not os.path.isdir(u_dir):
        return results

    try:
        book_dirs = sorted(os.listdir(u_dir))
    except OSError:
        return results

    for book_dir in book_dirs:
        if book_dir.lower() in ("bookmarks", "snippets"):
            continue
        full_book_path = os.path.join(u_dir, book_dir)
        if os.path.isdir(full_book_path):
            book_items = _scan_book_directory_for_bookmarks(full_book_path, book_dir, u, username, seen_ids)
            results.extend(book_items)
    return results


def _collect_all_user_bookmarks(
    candidate_roots: List[str],
    user_search_names: List[str],
    username: str
) -> List[Dict[str, Any]]:
    bookmarks: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for root in candidate_roots:
        for u in user_search_names:
            for sub in ("bookmarks", ""):
                u_dir = os.path.join(root, u, sub) if sub else os.path.join(root, u)
                items = _scan_user_directory_bookmarks(u_dir, u, username, seen_ids)
                bookmarks.extend(items)
    return bookmarks


@app.get("/api/user/bookmarks", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/snippets", responses=COMMON_AUTH_RESPONSES)
def get_user_bookmarks(
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
    bookmarks = _collect_all_user_bookmarks(candidate_roots, user_search_names, username)

    return {
        "status": "success",
        "username": username,
        "count": len(bookmarks),
        "bookmarks": bookmarks
    }


@app.get("/api/user/bookmarks/status", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/snippets/status", responses=COMMON_AUTH_RESPONSES)
def get_bookmarks_status(
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


def _calculate_snippet_durations(payload: SnippetExpandRequest) -> Tuple[float, float, int]:
    pre_roll = float(payload.preRoll if payload.preRoll is not None else (payload.pre_roll or 30.0))
    post_roll = float(payload.postRoll if payload.postRoll is not None else (payload.post_roll or 30.0))
    total_duration = max(5, int(round(pre_roll + post_roll)))
    return pre_roll, post_roll, total_duration


def _scan_book_folders_for_snippet(user_dir: str, target_ts: str) -> Optional[Dict[str, Any]]:
    real_u_dir = os.path.realpath(user_dir)
    try:
        entries = os.listdir(real_u_dir)
    except OSError:
        return None

    for b_dir in entries:
        book_folder = os.path.join(real_u_dir, b_dir)
        if not os.path.isdir(book_folder):
            continue
        meta = safe_read_json_file(book_folder, f"{target_ts}.json")
        if meta:
            return meta
    return None


def _check_user_root_dir(root: str, u: str, target_ts: str) -> Optional[Dict[str, Any]]:
    for sub in ("bookmarks", ""):
        u_dir = os.path.join(root, u, sub) if sub else os.path.join(root, u)
        if os.path.isdir(u_dir):
            meta = _scan_book_folders_for_snippet(u_dir, target_ts)
            if meta:
                return meta
    return None


def _find_existing_snippet_meta(safe_username: str, user_id: str, target_ts: str) -> Optional[Dict[str, Any]]:
    candidate_usernames = [safe_username, safe_username.lower(), user_id]
    for root in get_candidate_volume_dirs():
        for u in candidate_usernames:
            meta = _check_user_root_dir(root, u, target_ts)
            if meta:
                return meta
    return None


def _resolve_snippet_enrichment(
    cur_time: Optional[float],
    lib_id: Optional[str],
    book_title: Optional[str],
    safe_username: str,
    user_id: str,
    target_ts: str
) -> Tuple[float, Optional[str], Optional[str]]:
    if cur_time is not None and lib_id and book_title:
        return float(cur_time), lib_id, book_title

    existing_meta = _find_existing_snippet_meta(safe_username, user_id, target_ts)
    if existing_meta:
        if cur_time is None:
            cur_time = existing_meta.get("current_time", existing_meta.get("start_time", 0.0))
        if not lib_id:
            lib_id = existing_meta.get("library_item_id")
        if not book_title:
            book_title = existing_meta.get("book_title")

    return float(cur_time or 0.0), lib_id, book_title


@app.post("/api/snippet/expand", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/snippet/update", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/snippet/retry", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/user/snippet/retry", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/user/snippet/expand", responses=COMMON_AUTH_RESPONSES)
async def expand_or_update_snippet(
    request: Request,
    payload: SnippetExpandRequest,
    raw_token: str = Depends(extract_token_flexible)
):
    """
    Adjusts and expands an existing snippet with new pre-roll and post-roll durations,
    or retries extraction for a bookmark whose audio was previously unavailable.
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

    # Strictly validate and sanitize snippet timestamp identifier against path traversal
    target_ts = validate_and_sanitize_snippet_timestamp(payload.timestamp)
    pre_roll, post_roll, total_duration = _calculate_snippet_durations(payload)

    raw_cur_time = payload.currentTime if payload.currentTime is not None else payload.current_time
    raw_lib_id = payload.libraryItemId or payload.library_item_id
    raw_book_title = payload.bookTitle or payload.book_title

    cur_time, enriched_lib_id, resolved_book_title = await asyncio.to_thread(
        _resolve_snippet_enrichment,
        raw_cur_time,
        raw_lib_id,
        raw_book_title,
        safe_username,
        user["id"],
        target_ts
    )

    new_start_time = max(0.0, float(cur_time) - pre_roll)
    safe_lib_id = validate_and_sanitize_library_item_id(enriched_lib_id)

    clean_log_ts = sanitize_log_message(target_ts)
    clean_log_user = sanitize_log_message(username)
    clean_log_lib = sanitize_log_message(safe_lib_id) if safe_lib_id else "unknown"

    logger.info(
        f"Expanding/retrying snippet [{clean_log_ts}] for @{clean_log_user}: "
        f"anchor={cur_time}s, pre_roll={pre_roll}s, post_roll={post_roll}s "
        f"(start={new_start_time}s, duration={total_duration}s, lib={clean_log_lib})"
    )

    # Execute extraction with replace_timestamp so old snippet is overwritten in-place
    result = await asyncio.to_thread(
        process_bookmark_extraction,
        library_item_id=safe_lib_id,
        auth_token=raw_token,
        server_url=server_url,
        duration=total_duration,
        user_info=user,
        custom_start=new_start_time,
        replace_timestamp=target_ts,
        bookmark_data={
            "libraryItemId": safe_lib_id,
            "time": cur_time,
            "title": resolved_book_title or ""
        }
    )
    return result


@app.get("/api/cutoff-config", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/user/cutoff-config", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/installation-date", responses=COMMON_AUTH_RESPONSES)
@app.get("/api/user/installation-date", responses=COMMON_AUTH_RESPONSES)
def get_cutoff_configuration():
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


def _parse_custom_cutoff_date(raw_d: Optional[str]) -> Tuple[datetime, str]:
    clean_raw = (raw_d or "").strip()
    if not clean_raw:
        raise ValueError("custom_date is required when cutoff_mode is 'custom_date'.")
    
    clean_d = re.sub(r'[/.]', '-', clean_raw)
    m_yy = re.match(r'^(\d{2})-(\d{1,2})-(\d{1,2})$', clean_d)
    if m_yy:
        clean_d = f"20{m_yy.group(1)}-{int(m_yy.group(2)):02d}-{int(m_yy.group(3)):02d}"
    else:
        m_yyyy = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', clean_d)
        if m_yyyy:
            clean_d = f"{m_yyyy.group(1)}-{int(m_yyyy.group(2)):02d}-{int(m_yyyy.group(3)):02d}"

    try:
        dt_obj = datetime.strptime(clean_d[:10], "%Y-%m-%d")
    except ValueError:
        raise ValueError("Invalid custom_date format. Please use YYYY/MM/DD, YY/MM/DD, or YYYY-MM-DD.")

    cutoff_dt = datetime(dt_obj.year, dt_obj.month, dt_obj.day, 0, 0, 0)
    iso_date = dt_obj.strftime("%Y-%m-%d")
    return cutoff_dt, iso_date


def _build_from_start_cutoff_config() -> Dict[str, Any]:
    return {
        "cutoff_mode": "from_start",
        "cutoff_timestamp": 0.0,
        "cutoff_datetime": "1970-01-01T00:00:00",
        "custom_date": None,
        "note": "Extracting all bookmarks from the beginning of the server."
    }


def _build_custom_date_cutoff_config(raw_custom_date: Optional[str]) -> Dict[str, Any]:
    cutoff_dt, iso_date = _parse_custom_cutoff_date(raw_custom_date)
    return {
        "cutoff_mode": "custom_date",
        "custom_date": iso_date,
        "cutoff_datetime": f"{iso_date}T00:00:00",
        "cutoff_timestamp": cutoff_dt.timestamp(),
        "note": f"Extracting bookmarks created on or after {iso_date} at 00:00."
    }


def _build_from_now_cutoff_config() -> Dict[str, Any]:
    inst_date = INSTALLATION_CONFIG.get("installation_date") or datetime.now().strftime("%Y-%m-%d")
    clean_d = inst_date[:10]
    try:
        parts = [int(p) for p in clean_d.split("-")]
        cutoff_dt = datetime(parts[0], parts[1], parts[2], 0, 0, 0)
        cutoff_ts = cutoff_dt.timestamp()
    except Exception:
        cutoff_dt = datetime.now()
        cutoff_ts = cutoff_dt.timestamp()

    return {
        "cutoff_mode": "from_now",
        "installation_date": inst_date,
        "custom_date": None,
        "cutoff_datetime": f"{clean_d}T00:00:00",
        "cutoff_timestamp": cutoff_ts,
        "note": f"Extracting bookmarks created on or after installation date ({clean_d})."
    }


def _compute_new_cutoff_config(mode: str, custom_date: Optional[str]) -> Dict[str, Any]:
    if mode == "from_start":
        return _build_from_start_cutoff_config()
    if mode == "custom_date":
        return _build_custom_date_cutoff_config(custom_date)
    return _build_from_now_cutoff_config()


@app.post("/api/cutoff-config", responses=COMMON_AUTH_RESPONSES)
@app.post("/api/user/cutoff-config", responses=COMMON_AUTH_RESPONSES)
async def update_cutoff_configuration(
    payload: CutoffConfigRequest,
    request: Request,
    raw_token: Optional[str] = Depends(extract_token_optional)
):
    """
    Allows the admin to configure the bookmark cutoff period.
    Three options:
    1. 'from_start': Processes all bookmarks from the beginning of server history.
    2. 'custom_date': Processes bookmarks on or after YYYY/MM/DD, YY/MM/DD, or YYYY-MM-DD.
    3. 'from_now': Processes bookmarks from current installation date (or resets to now).
    """
    mode = (payload.cutoff_mode or "from_now").strip().lower()
    if mode not in ("from_start", "custom_date", "from_now"):
        raise HTTPException(
            status_code=400,
            detail="Invalid cutoff_mode. Must be 'from_start', 'custom_date', or 'from_now'."
        )

    updated_config = _compute_new_cutoff_config(mode, payload.custom_date)

    # Save to disk and update globals
    await asyncio.to_thread(save_installation_config, updated_config)
    logger.info(f"[Cutoff Config] Successfully updated cutoff period mode='{mode}', cutoff='{updated_config.get('cutoff_datetime')}'")

    return {
        "status": "success",
        "message": f"Cutoff period updated to mode '{mode}' ({updated_config.get('cutoff_datetime')})",
        "config": INSTALLATION_CONFIG
    }


def _check_book_title_traversal(clean_title: str) -> None:
    """Verifies that book title contains no traversal tokens or forbidden characters."""
    if any(sep in clean_title for sep in ("/", "\\", "\0", "\r", "\n", "\t")):
        raise ValueError("Security violation: book title contains illegal path characters.")
    if ".." in clean_title or clean_title.startswith(".") or clean_title.endswith("."):
        raise ValueError("Security violation: book title contains directory navigation tokens.")
    if not re.match(r"^[a-zA-Z0-9_\- .',!:?()\[\]]+$", clean_title):
        raise ValueError("Security violation: book title contains forbidden characters.")


def _check_book_title_sanitized(sanitized: str) -> None:
    """Ensures sanitized title does not reduce to navigation tokens."""
    if not sanitized or sanitized.startswith("."):
        raise ValueError("Invalid book title after normalization.")
    if "/" in sanitized or "\\" in sanitized or ".." in sanitized:
        raise ValueError("Invalid book title after normalization.")


def validate_and_sanitize_export_book_title(raw_title: Optional[str]) -> str:
    """
    Validates and sanitizes user-supplied book title for export,
    preventing Path Traversal (CWE-22) and Filesystem Existence Oracle (CWE-209/CWE-200).
    """
    if not raw_title or not isinstance(raw_title, str):
        raise ValueError("Book title parameter is required.")

    clean_title = raw_title.strip()
    if not clean_title or len(clean_title) > 200:
        raise ValueError("Invalid book title length (must be 1-200 characters).")

    _check_book_title_traversal(clean_title)
    sanitized = sanitize_filename(clean_title)
    _check_book_title_sanitized(sanitized)
    return sanitized


def _find_export_user_dirs(root: str, user_search_names: List[str]) -> List[str]:
    """Enumerates authorized user candidate directories inside a storage root."""
    real_root = os.path.realpath(root)
    if not os.path.isdir(real_root):
        return []
    valid_dirs = []
    for u in user_search_names:
        for sub in ("bookmarks", ""):
            cand = os.path.realpath(os.path.join(real_root, u, sub) if sub else os.path.join(real_root, u))
            if os.path.isdir(cand) and os.path.commonpath([real_root, cand]) == real_root and not os.path.islink(cand):
                valid_dirs.append(cand)
    valid_dirs.append(real_root)
    return valid_dirs


def _is_matching_book_entry(clean_name: str, target_clean: str, safe_book_title: str) -> bool:
    """Checks whether an enumerated folder name matches target book title."""
    norm_name = re.sub(NON_ALPHANUMERIC_REGEX, "", clean_name).lower()
    if clean_name.lower() == safe_book_title.lower() or (norm_name and norm_name == target_clean):
        return True
    if norm_name and target_clean and (norm_name.startswith(target_clean[:20]) or target_clean.startswith(norm_name)):
        return True
    return False


def _find_matching_book_folder(parent_dir: str, target_clean: str, safe_book_title: str) -> Optional[str]:
    """Scans legitimate folders inside parent_dir to locate matching book folder."""
    try:
        entries = sorted(os.listdir(parent_dir))
    except OSError:
        return None
    for b_entry in entries:
        if b_entry.startswith("."):
            continue
        clean_name = os.path.basename(b_entry)
        entry_path = os.path.realpath(os.path.join(parent_dir, clean_name))
        if os.path.commonpath([parent_dir, entry_path]) != parent_dir or os.path.islink(entry_path):
            continue
        if not os.path.isdir(entry_path):
            continue
        if _is_matching_book_entry(clean_name, target_clean, safe_book_title):
            return entry_path
    return None


def _discover_book_export_dir(
    candidate_roots: List[str],
    user_search_names: List[str],
    safe_book_title: str
) -> Optional[str]:
    """Locates legitimate book storage directory by scanning verified candidate roots."""
    target_clean = re.sub(NON_ALPHANUMERIC_REGEX, "", safe_book_title).lower()
    for root in candidate_roots:
        for p_dir in _find_export_user_dirs(root, user_search_names):
            match = _find_matching_book_folder(p_dir, target_clean, safe_book_title)
            if match:
                return match
    return None


def _build_markdown_export(
    real_book_dir: str,
    book_title: str,
    safe_book_title: str,
    username: str
) -> Response:
    """Builds combined Markdown export from all markdown notes in book directory."""
    try:
        md_files = sorted([f for f in os.listdir(real_book_dir) if f.endswith(".md") and not f.startswith(".")])
    except OSError:
        md_files = []
    if not md_files:
        raise HTTPException(status_code=404, detail="No markdown notes found for this book")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    combined_lines = [
        f"# {book_title} - All Bookmarks & Transcripts\n",
        f"*Exported on {now_str} for @{username}*\n\n---\n"
    ]
    for idx, md_f in enumerate(md_files, 1):
        content = safe_read_text_file(real_book_dir, md_f) or ""
        combined_lines.append(f"## Bookmark {idx} ({md_f[:-3]})\n\n{content.strip()}\n\n---\n")

    combined_text = "\n".join(combined_lines)
    filename = f"{safe_book_title}_All_Snippets.md"
    return Response(
        content=combined_text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _is_safe_export_entry(real_book_dir: str, fname: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validates that a directory entry is a non-hidden regular file within the book directory."""
    if fname.startswith("."):
        return False, None, None
    clean_child = os.path.basename(fname)
    fpath = os.path.realpath(os.path.join(real_book_dir, clean_child))
    if os.path.commonpath([real_book_dir, fpath]) == real_book_dir and os.path.isfile(fpath) and not os.path.islink(fpath):
        return True, fpath, clean_child
    return False, None, None


def _build_notes_summary_content(real_book_dir: str, book_title: str, username: str, md_files: List[str]) -> str:
    """Creates a combined markdown summary of notes for the zip archive."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_lines = [
        f"# {book_title} - All Notes Summary\n",
        f"*Exported on {now_str} for @{username}*\n\n"
    ]
    for idx, md_f in enumerate(md_files, 1):
        content = safe_read_text_file(real_book_dir, md_f) or ""
        summary_lines.append(f"### {idx}. {md_f[:-3]}\n\n{content.strip()}\n\n---\n")
    return "\n".join(summary_lines)


def _populate_zip_archive(zf: Any, real_book_dir: str, safe_book_title: str, book_title: str, username: str):
    """Populates ZIP archive with book files and generated summary."""
    try:
        entries = sorted(os.listdir(real_book_dir))
    except OSError:
        entries = []
    md_files = []
    for fname in entries:
        is_safe, fpath, clean_child = _is_safe_export_entry(real_book_dir, fname)
        if is_safe and fpath and clean_child:
            zf.write(fpath, arcname=f"{safe_book_title}/{clean_child}")
            if clean_child.endswith(".md"):
                md_files.append(clean_child)

    if md_files:
        summary_text = _build_notes_summary_content(real_book_dir, book_title, username, md_files)
        zf.writestr(f"{safe_book_title}/ALL_NOTES_COMBINED.md", summary_text)


def _build_zip_export(
    real_book_dir: str,
    book_title: str,
    safe_book_title: str,
    username: str
) -> Response:
    """Builds ZIP archive containing all audio and notes for book export."""
    import io
    import zipfile

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        _populate_zip_archive(zf, real_book_dir, safe_book_title, book_title, username)

    zip_bytes = zip_buffer.getvalue()
    filename = f"{safe_book_title}_All_Snippets.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _resolve_export_user(
    request: Optional[Request],
    raw_token: Optional[str],
    token: Optional[str]
) -> Tuple[Dict[str, Any], str]:
    """Resolves authenticated or fallback user for book export."""
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
            logger.exception(f"Export auth token validation failed: {auth_err}. Falling back to active session.")

    if not user:
        user = _last_authenticated_session.get("user") or {
            "id": "default_user",
            "username": os.environ.get("DEFAULT_USERNAME", "user"),
            "raw_token": eff_token or ""
        }
    return user, sanitize_filename(user["username"])


def _validate_export_boundary(real_book_dir: str, candidate_roots: List[str], book_dir_path: str) -> None:
    """Ensures book export path does not escape candidate volume boundaries."""
    valid_boundary = any(
        os.path.commonpath([os.path.realpath(c_root), real_book_dir]) == os.path.realpath(c_root)
        and real_book_dir != os.path.realpath(c_root)
        for c_root in candidate_roots
    )
    if not valid_boundary or os.path.islink(book_dir_path):
        raise HTTPException(
            status_code=400,
            detail="Security violation: resolved book directory escapes allowed storage roots."
        )


@app.get("/api/export-book", responses=COMMON_CRUD_RESPONSES)
@app.get("/api/user/bookmarks/export-book", responses=COMMON_CRUD_RESPONSES)
@app.get("/api/snippets/export-book", responses=COMMON_CRUD_RESPONSES)
@app.get("/api/book/export", responses=COMMON_CRUD_RESPONSES)
def export_book_snippets(
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
    user, safe_username = _resolve_export_user(request, raw_token, token)
    username = user["username"]
    safe_book_title = validate_and_sanitize_export_book_title(book_title)

    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user.get("id", ""), "default_user"]))
    book_dir_path = _discover_book_export_dir(candidate_roots, user_search_names, safe_book_title)

    if not book_dir_path:
        raise HTTPException(status_code=404, detail="No snippets found for the specified book.")

    real_book_dir = os.path.realpath(book_dir_path)
    _validate_export_boundary(real_book_dir, candidate_roots, book_dir_path)

    if format.lower() in ("markdown", "md"):
        return _build_markdown_export(real_book_dir, book_title, safe_book_title, username)
    return _build_zip_export(real_book_dir, book_title, safe_book_title, username)


def _safe_parse_float(val: Any) -> Optional[float]:
    """Safely converts input to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _is_bookmark_file_match(
    book_dir: str,
    fname: str,
    clean_id: str,
    target_pattern: str,
    id_variants: Set[str],
    req_timestamp: Optional[str]
) -> bool:
    """Checks whether a filename matches the candidate snippet identifier or its variants."""
    base_name = os.path.splitext(fname)[0]
    full_id = f"{book_dir}-{base_name}"
    return (
        clean_id in (full_id, base_name, fname)
        or target_pattern in (base_name, fname)
        or full_id.lower() in id_variants
        or base_name.lower() in id_variants
        or bool(req_timestamp and req_timestamp in base_name)
    )


async def _extract_companion_metadata(
    full_book_path: str,
    filenames: List[str],
    book_dir: str,
    clean_id: str,
    target_pattern: str,
    id_variants: Set[str],
    req_timestamp: Optional[str]
) -> Dict[str, Any]:
    """Reads companion JSON metadata for a matching bookmark file."""
    for fname in filenames:
        if not fname.endswith(JSON_FILE_EXTENSION):
            continue
        if _is_bookmark_file_match(book_dir, fname, clean_id, target_pattern, id_variants, req_timestamp):
            json_candidate = os.path.join(full_book_path, fname)
            try:
                async with aiofiles.open(json_candidate, "r", encoding="utf-8") as jf:
                    raw_json = await jf.read()
                    return json.loads(raw_json)
            except Exception:
                pass
    return {}


def _remove_matching_companion_files(
    full_book_path: str,
    filenames: List[str],
    book_dir: str,
    clean_id: str,
    target_pattern: str,
    id_variants: Set[str],
    req_timestamp: Optional[str]
) -> int:
    """Removes all matching bookmark files (.md, .mp3, .json) in an audiobook directory."""
    deleted_count = 0
    for fname in filenames:
        if _is_bookmark_file_match(book_dir, fname, clean_id, target_pattern, id_variants, req_timestamp) and safe_remove_file_in_directory(full_book_path, fname):
            deleted_count += 1
    return deleted_count


async def _process_book_dir_for_deletion(
    full_book_path: str,
    book_dir: str,
    clean_id: str,
    target_pattern: str,
    id_variants: Set[str],
    req_timestamp: Optional[str]
) -> Tuple[int, Dict[str, Any]]:
    """Inspects a specific audiobook directory, extracts companion JSON metadata, and safely removes matching files."""
    try:
        filenames = os.listdir(full_book_path)
    except OSError:
        return 0, {}

    found_metadata = await _extract_companion_metadata(
        full_book_path, filenames, book_dir, clean_id, target_pattern, id_variants, req_timestamp
    )
    deleted_count = _remove_matching_companion_files(
        full_book_path, filenames, book_dir, clean_id, target_pattern, id_variants, req_timestamp
    )

    try:
        if os.path.isdir(full_book_path) and not os.listdir(full_book_path):
            os.rmdir(full_book_path)
    except Exception:
        pass

    return deleted_count, found_metadata


def _collect_candidate_dirs(candidate_roots: List[str], user_search_names: List[str]) -> List[str]:
    """Enumerates candidate directory paths across volume roots and username aliases."""
    dirs = []
    for root in candidate_roots:
        for u in user_search_names:
            dirs.append(os.path.join(root, u, "bookmarks"))
            dirs.append(os.path.join(root, u))
    return dirs


def _collect_candidate_book_dirs(candidate_roots: List[str], user_search_names: List[str]) -> List[Tuple[str, str]]:
    """Discovers all audiobook subdirectories within candidate user folders."""
    candidate_dirs = _collect_candidate_dirs(candidate_roots, user_search_names)
    results = []
    for base_dir in candidate_dirs:
        if not os.path.isdir(base_dir):
            continue
        try:
            entries = os.listdir(base_dir)
        except OSError:
            continue
        for entry in entries:
            full_path = os.path.join(base_dir, entry)
            if os.path.isdir(full_path):
                results.append((full_path, entry))
    return results


async def _find_and_remove_bookmark_files(
    candidate_roots: List[str],
    user_search_names: List[str],
    clean_id: str,
    target_pattern: str,
    id_variants: Set[str],
    req_timestamp: Optional[str]
) -> Tuple[int, Dict[str, Any]]:
    """Scans all candidate volume roots and user folders to remove matching bookmark files and retrieve metadata."""
    total_deleted = 0
    aggregated_metadata: Dict[str, Any] = {}
    book_dirs = _collect_candidate_book_dirs(candidate_roots, user_search_names)

    for full_book_path, book_dir in book_dirs:
        cnt, meta = await _process_book_dir_for_deletion(
            full_book_path, book_dir, clean_id, target_pattern, id_variants, req_timestamp
        )
        total_deleted += cnt
        if meta and not aggregated_metadata:
            aggregated_metadata = meta

    return total_deleted, aggregated_metadata


async def _fetch_server_bookmarks(base_target: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """Fetches user bookmarks from upstream Audiobookshelf server to construct a trusted allowlist."""
    bookmarks_url = f"{base_target}/api/me/bookmarks"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(bookmarks_url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                if isinstance(body, dict):
                    return body.get("bookmarks") or []
                if isinstance(body, list):
                    return body
    except Exception as fetch_err:
        logger.debug("Upstream ABS bookmarks query notice: %s", sanitize_log_message(str(fetch_err)))
    return []


def _find_server_bookmark_match(
    server_bookmarks: List[Dict[str, Any]],
    clean_target_time: float,
    clean_cand_lib: Optional[str]
) -> Tuple[Optional[str], Optional[int]]:
    """Matches candidate bookmark target against server-provided bookmark allowlist entries."""
    for entry in server_bookmarks:
        if not isinstance(entry, dict):
            continue
        raw_lib = entry.get("libraryItemId")
        raw_time = _safe_parse_float(entry.get("time"))
        if raw_lib is None or raw_time is None:
            continue

        if abs(raw_time - clean_target_time) > 2.0:
            continue

        entry_lib_str = str(raw_lib).strip()
        if clean_cand_lib and clean_cand_lib != entry_lib_str:
            continue

        if re.fullmatch(r"^[A-Za-z0-9_\-]+$", entry_lib_str) and len(entry_lib_str) <= 128:
            return entry_lib_str, int(raw_time)

    return None, None


async def _delete_upstream_abs_bookmark(
    server_url: str,
    raw_token: str,
    target_lib_id: Optional[str],
    target_time: Optional[float]
) -> bool:
    """
    Safely removes a bookmark from the upstream Audiobookshelf server if permitted.
    To prevent API Traversal (CWE-22 / CWE-918 / pythonsecurity:S7044) and Log Injection (pythonsecurity:S5145),
    this function queries the user's active bookmarks from the server's own endpoint (/api/me/bookmarks),
    validates candidate target identifiers against the server's verified allowlist of bookmark entries,
    and only issues a DELETE request using verified identifiers originating directly from the server response.
    """
    clean_target_time = _safe_parse_float(target_time)
    if not server_url or not raw_token or clean_target_time is None:
        return False

    base_target = server_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {raw_token}",
        "Content-Type": MIME_TYPE_JSON
    }

    server_bookmarks = await _fetch_server_bookmarks(base_target, headers)
    if not server_bookmarks:
        return False

    clean_cand_lib = str(target_lib_id).strip() if target_lib_id else None
    matched_lib_id, matched_time = _find_server_bookmark_match(server_bookmarks, clean_target_time, clean_cand_lib)
    if not matched_lib_id or matched_time is None:
        return False

    if any(sep in matched_lib_id for sep in ("..", "/", "\\")):
        return False

    delete_url = f"{base_target}/api/me/bookmark/{matched_lib_id}/{matched_time}"
    parsed = urlsplit(delete_url)
    expected_path = f"/api/me/bookmark/{matched_lib_id}/{matched_time}"
    if parsed.path != expected_path:
        logger.warning("Security: rejected upstream delete URL failing path verification")
        return False

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            del_resp = await client.delete(delete_url, headers=headers)

        clean_log_id = sanitize_log_message(matched_lib_id)
        logger.info(
            "Notified ABS server to delete bookmark in item %s at %d s (status: %d)",
            clean_log_id,
            matched_time,
            del_resp.status_code
        )
        return del_resp.status_code in (200, 204)
    except Exception as del_err:
        logger.debug("Upstream ABS server delete response notice: %s", sanitize_log_message(str(del_err)))
        return False


def _extract_delete_request_metadata(request: Request) -> Dict[str, Any]:
    """Extracts and sanitizes query parameters for bookmark deletion request."""
    raw_lib_id = request.query_params.get("library_item_id")
    raw_book_title = request.query_params.get("book_title")
    raw_created_at = request.query_params.get("created_at")
    raw_ts = request.query_params.get("timestamp")
    req_timestamp = None
    if raw_ts:
        try:
            req_timestamp = validate_and_sanitize_snippet_timestamp(raw_ts)
        except Exception:
            req_timestamp = None

    return {
        "req_lib_id": validate_and_sanitize_library_item_id(raw_lib_id),
        "resolved_time": _safe_parse_float(request.query_params.get("time")),
        "resolved_start": _safe_parse_float(request.query_params.get("start_time")),
        "req_book_title": sanitize_filename(raw_book_title) if raw_book_title else None,
        "req_created_at": re.sub(r'[\0\r\n\t<>]', '', raw_created_at).strip() if raw_created_at else None,
        "req_timestamp": req_timestamp,
    }


def _resolve_tombstone_metadata(
    meta: Dict[str, Any],
    found_metadata: Dict[str, Any],
    clean_id: str
) -> Dict[str, Any]:
    """Resolves merged metadata attributes for persisting a deletion tombstone."""
    meta_lib_id = validate_and_sanitize_library_item_id(found_metadata.get("library_item_id"))
    final_lib_id = meta["req_lib_id"] or meta_lib_id

    final_time = meta["resolved_time"]
    if final_time is None:
        final_time = _safe_parse_float(found_metadata.get("current_time") or found_metadata.get("start_time"))

    final_start = meta["resolved_start"]
    if final_start is None:
        final_start = _safe_parse_float(found_metadata.get("start_time"))

    final_book_title = meta["req_book_title"] or found_metadata.get("book_title")
    final_title = found_metadata.get("title") or found_metadata.get("bookmark_title")
    final_created_at = meta["req_created_at"] or found_metadata.get("created_at") or found_metadata.get("date_time")
    final_timestamp = meta["req_timestamp"] or found_metadata.get("timestamp") or clean_id

    return {
        "lib_id": final_lib_id,
        "book_time": final_time,
        "start_time": final_start,
        "current_time": final_time,
        "book_title": final_book_title,
        "title": final_title,
        "created_at": final_created_at,
        "timestamp": final_timestamp,
    }


@app.delete("/api/user/bookmarks/{snippet_id:path}", responses=COMMON_CRUD_RESPONSES)
@app.delete("/api/snippets/{snippet_id:path}", responses=COMMON_CRUD_RESPONSES)
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

    meta = _extract_delete_request_metadata(request)

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
    if meta["req_timestamp"]:
        id_variants.add(meta["req_timestamp"].lower())

    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user_id]))

    deleted_count, found_metadata = await _find_and_remove_bookmark_files(
        candidate_roots=candidate_roots,
        user_search_names=user_search_names,
        clean_id=clean_id,
        target_pattern=target_pattern,
        id_variants=id_variants,
        req_timestamp=meta["req_timestamp"]
    )

    tombstone_meta = _resolve_tombstone_metadata(meta, found_metadata, clean_id)

    # Record tombstone across all persistent file paths
    record_deleted_tombstone(
        snippet_id=clean_id,
        lib_id=tombstone_meta["lib_id"],
        book_time=tombstone_meta["book_time"],
        start_time=tombstone_meta["start_time"],
        current_time=tombstone_meta["current_time"],
        book_title=tombstone_meta["book_title"],
        title=tombstone_meta["title"],
        created_at=tombstone_meta["created_at"],
        timestamp=tombstone_meta["timestamp"]
    )

    # Best-effort attempt to remove the bookmark from upstream Audiobookshelf server if permitted
    if server_url and raw_token:
        await _delete_upstream_abs_bookmark(
            server_url=server_url,
            raw_token=raw_token,
            target_lib_id=final_lib_id,
            target_time=final_time
        )

    return {
        "status": "success",
        "deleted_id": snippet_id,
        "files_removed": deleted_count,
        "tombstone_recorded": True
    }


# --- Static Audio & Markdown File Serving ---

def validate_and_sanitize_serve_filename(raw_filename: Optional[str]) -> str:
    """
    Validates and sanitizes user-supplied bookmark filename, strictly preventing
    Path Traversal (CWE-22) and Filesystem Oracle (CWE-209/CWE-200).
    Only allows alphanumeric and safe punctuation characters with .mp3, .md, .json, or .txt extensions.
    """
    if not raw_filename or not isinstance(raw_filename, str):
        raise ValueError("Filename parameter is required.")

    clean_filename = raw_filename.strip()
    if not clean_filename or len(clean_filename) > 255:
        raise ValueError("Invalid filename length.")

    if any(sep in clean_filename for sep in ("/", "\\", "\0", "\r", "\n", "\t")):
        raise ValueError("Security violation: filename contains path separators or control characters.")

    if ".." in clean_filename or clean_filename.startswith("."):
        raise ValueError("Security violation: filename contains directory navigation tokens.")

    base_name = os.path.basename(clean_filename)
    if base_name != clean_filename:
        raise ValueError("Security violation: filename must be a base filename.")

    # Restrict to legitimate bookmark file formats (.mp3, .md, .json, .txt) with safe filename characters
    if not re.match(r"^[a-z0-9_\- .',!:?()\[\]]+\.(mp3|md|json|txt)$", clean_filename, re.IGNORECASE):
        raise ValueError("Security violation: filename contains unsupported characters or disallowed extension.")

    return clean_filename


def validate_and_sanitize_serve_username(raw_username: Optional[str]) -> str:
    """
    Validates and sanitizes username parameter for bookmark file serving,
    preventing path injection and invalid character abuse.
    """
    if not raw_username or not isinstance(raw_username, str):
        raise ValueError("Username parameter is required.")

    clean_username = raw_username.strip()
    if not clean_username or len(clean_username) > 100:
        raise ValueError("Invalid username length.")

    if any(sep in clean_username for sep in ("/", "\\", "\0", "\r", "\n", "\t")):
        raise ValueError("Security violation: username contains illegal path characters.")

    if ".." in clean_username or clean_username.startswith("."):
        raise ValueError("Security violation: username contains directory navigation tokens.")

    if not re.match(r"^[a-zA-Z0-9_\- .@]+$", clean_username):
        raise ValueError("Security violation: username contains invalid characters.")

    sanitized = sanitize_filename(clean_username)
    if not sanitized or sanitized.startswith(".") or "/" in sanitized or "\\" in sanitized or ".." in sanitized:
        raise ValueError("Invalid username after normalization.")
    return sanitized


def _is_matching_book_folder(clean_b_entry: str, safe_book_title: str, target_clean: str) -> bool:
    """Matches book folder name strictly via string comparison against validated title."""
    clean_entry = re.sub(NON_ALPHANUMERIC_REGEX, '', clean_b_entry).lower()
    is_book_match = (
        clean_b_entry.lower() == safe_book_title.lower() or
        (bool(clean_entry) and clean_entry == target_clean) or
        clean_b_entry.replace("_", " ").strip().lower() == safe_book_title.replace("_", " ").strip().lower()
    )
    if not is_book_match and clean_entry and target_clean and len(target_clean) >= 8 and (clean_entry.startswith(target_clean[:20]) or target_clean.startswith(clean_entry)):
        return True
    return is_book_match


def _find_file_in_book_dir(entry_book_dir: str, target_filename: str) -> Optional[Tuple[str, str]]:
    """Scans verified book folder for a matching bookmark file."""
    try:
        f_entries = sorted(os.listdir(entry_book_dir))
    except OSError:
        return None

    allowed_exts = (".mp3", ".md", JSON_FILE_EXTENSION, ".txt")
    for f_entry in f_entries:
        if f_entry.startswith("."):
            continue
        clean_f_entry = os.path.basename(f_entry)
        entry_file_path = os.path.realpath(os.path.join(entry_book_dir, clean_f_entry))
        if os.path.commonpath([entry_book_dir, entry_file_path]) != entry_book_dir or os.path.islink(entry_file_path):
            continue
        if not os.path.isfile(entry_file_path):
            continue
        if not clean_f_entry.lower().endswith(allowed_exts):
            continue
        if clean_f_entry.lower() == target_filename.lower():
            return entry_file_path, clean_f_entry
    return None


def _scan_parent_dir_for_file(
    real_parent: str,
    safe_book_title: str,
    target_clean: str,
    target_filename: str
) -> Optional[Tuple[str, str]]:
    """Enumerates legitimate subdirectories inside parent folder to find matching book folder and file."""
    try:
        entries = sorted(os.listdir(real_parent))
    except OSError:
        return None

    for b_entry in entries:
        if b_entry.startswith("."):
            continue
        clean_b_entry = os.path.basename(b_entry)
        entry_book_dir = os.path.realpath(os.path.join(real_parent, clean_b_entry))
        if os.path.commonpath([real_parent, entry_book_dir]) != real_parent or os.path.islink(entry_book_dir):
            continue
        if not os.path.isdir(entry_book_dir):
            continue
        if not _is_matching_book_folder(clean_b_entry, safe_book_title, target_clean):
            continue
        found = _find_file_in_book_dir(entry_book_dir, target_filename)
        if found:
            return found
    return None


def _discover_user_search_names(real_root: str, safe_username: str) -> List[str]:
    """Discovers user search name candidates and case-insensitive aliases in real_root."""
    user_search_names = list(dict.fromkeys([
        safe_username,
        safe_username.lower(),
        safe_username.capitalize(),
        "default_user"
    ]))
    try:
        for existing_entry in os.listdir(real_root):
            if existing_entry.lower() == safe_username.lower() and existing_entry not in user_search_names:
                user_search_names.append(existing_entry)
    except OSError:
        pass
    return user_search_names


def _collect_sub_parent_dirs(parent_dir: str, user_names_lower: Set[str]) -> List[str]:
    """Collects legitimate sub-level candidate directories inside parent_dir."""
    sub_dirs = []
    try:
        sub_entries = sorted(os.listdir(parent_dir))
    except OSError:
        return sub_dirs
    for sub in sub_entries:
        if sub.startswith("."):
            continue
        p_sub = os.path.realpath(os.path.join(parent_dir, sub))
        if os.path.commonpath([parent_dir, p_sub]) != parent_dir or os.path.islink(p_sub) or not os.path.isdir(p_sub):
            continue
        if sub.lower() in user_names_lower or sub.lower() in ("bookmarks", "snippets"):
            sub_dirs.append(p_sub)
    return sub_dirs


def _build_candidate_parent_dirs(real_root: str, user_search_names: List[str]) -> List[str]:
    """Collects candidate parent folders within real_root strictly by directory enumeration."""
    candidate_parent_dirs = [real_root]
    try:
        entries = sorted(os.listdir(real_root))
    except OSError:
        return candidate_parent_dirs

    user_names_lower = {u.lower() for u in user_search_names}
    for entry in entries:
        if entry.startswith("."):
            continue
        p = os.path.realpath(os.path.join(real_root, entry))
        if os.path.commonpath([real_root, p]) != real_root or os.path.islink(p) or not os.path.isdir(p):
            continue
        if entry.lower() in user_names_lower or entry.lower() in ("bookmarks", "snippets"):
            candidate_parent_dirs.append(p)
            candidate_parent_dirs.extend(_collect_sub_parent_dirs(p, user_names_lower))

    return candidate_parent_dirs


def _find_file_in_root(
    real_root: str,
    safe_username: str,
    safe_book_title: str,
    target_clean: str,
    target_filename: str
) -> Optional[Tuple[str, str]]:
    """Scans authorized root for requested bookmark file."""
    if not os.path.isdir(real_root):
        return None
    user_search_names = _discover_user_search_names(real_root, safe_username)
    candidate_parent_dirs = _build_candidate_parent_dirs(real_root, user_search_names)
    for parent_dir in candidate_parent_dirs:
        found = _scan_parent_dir_for_file(parent_dir, safe_book_title, target_clean, target_filename)
        if found:
            return found
    return None


def _find_bookmark_file_across_roots(
    candidate_roots: List[str],
    safe_username: str,
    safe_book_title: str,
    target_clean: str,
    target_filename: str
) -> Tuple[Optional[str], Optional[str]]:
    """Discovers matching bookmark file across all candidate storage roots."""
    for root in candidate_roots:
        real_root = os.path.realpath(root)
        found = _find_file_in_root(real_root, safe_username, safe_book_title, target_clean, target_filename)
        if found:
            return found
    return None, None


def _build_bookmark_file_response(matched_file_path: str, matched_filename: str) -> FileResponse:
    """Builds FileResponse with verified media type and streaming headers."""
    ext = os.path.splitext(matched_filename)[1].lower()
    media_type_map = {
        ".mp3": "audio/mpeg",
        JSON_FILE_EXTENSION: MIME_TYPE_JSON,
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }
    media_type = media_type_map.get(ext, "application/octet-stream")

    response = FileResponse(
        matched_file_path,
        media_type=media_type,
        filename=matched_filename
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.get("/bookmarks/{username}/{book_title}/{filename}", responses=COMMON_CRUD_RESPONSES)
@app.get("/snippets/{username}/{book_title}/{filename}", responses=COMMON_CRUD_RESPONSES)
def serve_bookmark_file(username: str, book_title: str, filename: str):
    """
    Serves the generated MP3 audio clip, Markdown transcript, or JSON metadata.
    Discovers candidate book directories and matching bookmark files strictly via
    server-side filesystem enumeration over authorized candidate roots.
    Eliminates Filesystem Oracle (CWE-209/CWE-200) and Path Traversal (CWE-22) vulnerabilities
    by completely isolating user input from filesystem path construction and probing APIs.
    Supports HTTP Range requests so audio players and web browsers can stream audio smoothly with seeking.
    """
    safe_username = validate_and_sanitize_serve_username(username)
    safe_book_title = validate_and_sanitize_export_book_title(book_title)
    target_filename = validate_and_sanitize_serve_filename(filename)
    target_clean = re.sub(NON_ALPHANUMERIC_REGEX, '', safe_book_title).lower()

    candidate_roots = get_candidate_volume_dirs()
    matched_file_path, matched_filename = _find_bookmark_file_across_roots(
        candidate_roots, safe_username, safe_book_title, target_clean, target_filename
    )

    if not matched_file_path or not matched_filename:
        raise HTTPException(status_code=404, detail="Requested audio or transcript file was not found")

    return _build_bookmark_file_response(matched_file_path, matched_filename)


# --- Embedded Web Dashboard & Transparent Proxy Engine ---

def _extract_dashboard_auth_token(
    request: Request,
    token: Optional[str],
    authorization: Optional[str]
) -> Optional[str]:
    """Extracts authentication token from header, query, cookie, or session."""
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        if len(parts) == 1:
            return parts[0]
    if token:
        return token
    cookie_token = request.cookies.get("abs_token")
    if cookie_token:
        return cookie_token
    return _last_authenticated_session.get("token")


def _authenticate_dashboard_user(
    request: Request,
    auth_token: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Resolves server URL and validates user credentials for dashboard."""
    server_url = resolve_abs_server_url(
        header_url=request.headers.get("X-ABS-Server-Url") or request.headers.get("X-Server-Url"),
        query_url=request.query_params.get("server_url") or request.query_params.get("serverUrl")
    )
    if not auth_token:
        return None, None, None
    try:
        user = validate_abs_token(auth_token, server_url=server_url)
        return user, None, auth_token
    except HTTPException as e:
        return None, e.detail, None


def _parse_dashboard_snippet_item(
    full_book_path: str,
    fname: str,
    book_dir: str,
    u: str
) -> Dict[str, Any]:
    """Parses single snippet markdown and companion files for dashboard."""
    base_name = fname[:-3]
    md_path = os.path.join(full_book_path, fname)
    mp3_path = os.path.join(full_book_path, f"{base_name}.mp3")

    raw_md = safe_read_text_file(full_book_path, fname) or ""
    parsed = parse_frontmatter(raw_md) if raw_md else {"body": ""}

    created_time = base_name
    try:
        mtime = os.path.getmtime(md_path)
        created_time = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %I:%M %p")
    except OSError:
        pass

    transcript_text = parsed.get("body", "")
    if MARKDOWN_TRANSCRIPT_HEADER in transcript_text:
        transcript_text = transcript_text.split(MARKDOWN_TRANSCRIPT_HEADER, 1)[1].strip()

    audio_url = None
    if os.path.isfile(mp3_path):
        audio_url = f"/bookmarks/{u}/{book_dir}/{base_name}.mp3"

    title_val = parsed.get("title")
    if title_val:
        display_title = title_val
    else:
        display_title = book_dir.replace("_", " ")

    return {
        "title": display_title,
        "author": parsed.get("author") or UNKNOWN_AUTHOR_FALLBACK,
        "chapter": parsed.get("chapter") or "",
        "timestamp": parsed.get("timestamp") or base_name,
        "created_at_str": created_time,
        "duration": parsed.get("duration", "60"),
        "transcript": transcript_text,
        "audio_url": audio_url,
        "md_url": f"/bookmarks/{u}/{book_dir}/{fname}"
    }


def _scan_book_dir_for_dashboard_snippets(
    full_book_path: str,
    book_dir: str,
    u: str,
    seen_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Collects all valid snippet items from a book directory."""
    snippets = []
    try:
        fnames = sorted(os.listdir(full_book_path), reverse=True)
    except OSError:
        return snippets
    for fname in fnames:
        if not fname.endswith(".md") or fname.startswith("."):
            continue
        base_name = fname[:-3]
        key = f"{book_dir.lower()}-{base_name}"
        if key in seen_ids:
            continue
        seen_ids.add(key)
        item = _parse_dashboard_snippet_item(full_book_path, fname, book_dir, u)
        snippets.append(item)
    return snippets


def _scan_user_dirs_for_dashboard(
    scan_dir: str,
    u: str,
    seen_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Scans legitimate book folders inside a user directory."""
    snippets = []
    try:
        entries = sorted(os.listdir(scan_dir))
    except OSError:
        return snippets
    for book_dir in entries:
        if book_dir.lower() in ("bookmarks", "snippets") or book_dir.startswith("."):
            continue
        full_book_path = os.path.realpath(os.path.join(scan_dir, book_dir))
        if os.path.commonpath([scan_dir, full_book_path]) != scan_dir or os.path.islink(full_book_path):
            continue
        if not os.path.isdir(full_book_path):
            continue
        snippets.extend(_scan_book_dir_for_dashboard_snippets(full_book_path, book_dir, u, seen_ids))
    return snippets


def _collect_user_scan_dirs(real_root: str, u: str) -> List[str]:
    """Finds valid, non-symlink scan directories for a user under a candidate root."""
    valid_dirs: List[str] = []
    for sub in ("bookmarks", ""):
        scan_dir = os.path.realpath(os.path.join(real_root, u, sub) if sub else os.path.join(real_root, u))
        if not os.path.isdir(scan_dir):
            continue
        if os.path.islink(scan_dir):
            continue
        if os.path.commonpath([real_root, scan_dir]) != real_root:
            continue
        valid_dirs.append(scan_dir)
    return valid_dirs


def _collect_dashboard_snippets(user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Discovers all snippet items across candidate volumes for the authenticated user."""
    safe_username = sanitize_filename(user["username"])
    candidate_roots = get_candidate_volume_dirs()
    user_search_names = list(dict.fromkeys([safe_username, safe_username.lower(), user["id"]]))
    seen_ids: Set[str] = set()
    snippets: List[Dict[str, Any]] = []

    for root in candidate_roots:
        real_root = os.path.realpath(root)
        if not os.path.isdir(real_root):
            continue
        for u in user_search_names:
            for scan_dir in _collect_user_scan_dirs(real_root, u):
                snippets.extend(_scan_user_dirs_for_dashboard(scan_dir, u, seen_ids))
    return snippets


def render_extractor_dashboard(
    request: Request,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None)
) -> Response:
    """
    Renders the HTML bookmark extractor and audio player dashboard for the authenticated user,
    discovering bookmarks across all candidate volume locations.
    """
    auth_token = _extract_dashboard_auth_token(request, token, authorization)
    user, error_msg, valid_token = _authenticate_dashboard_user(request, auth_token)

    if user:
        snippets = _collect_dashboard_snippets(user)
    else:
        snippets = []

    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "token": valid_token or "",
            "abs_server_url": ABS_SERVER_URL,
            "snippets": snippets,
            "error": error_msg
        }
    )

    if user and valid_token and not request.cookies.get("abs_token"):
        response.set_cookie(key="abs_token", value=valid_token, httponly=True, samesite="lax", max_age=86400 * 30)

    return response


@app.get(
    "/extractor",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Dedicated dashboard view for audio bookmark management"},
        400: {"description": "Invalid parameter format or authorization header"},
        401: {"description": "Unauthorized access or invalid authentication token"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
@app.get(
    "/extractor/",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Dedicated dashboard view for audio bookmark management"},
        400: {"description": "Invalid parameter format or authorization header"},
        401: {"description": "Unauthorized access or invalid authentication token"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
@app.get(
    "/bookmarks-ui",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Dedicated dashboard view for audio bookmark management"},
        400: {"description": "Invalid parameter format or authorization header"},
        401: {"description": "Unauthorized access or invalid authentication token"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
@app.get(
    "/sidecar-ui",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Dedicated dashboard view for audio bookmark management"},
        400: {"description": "Invalid parameter format or authorization header"},
        401: {"description": "Unauthorized access or invalid authentication token"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
def web_dashboard(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    Dedicated dashboard view for audio bookmark management and transcription playback.
    """
    return render_extractor_dashboard(request, token=token, authorization=authorization)


@app.get(
    "/logout",
    responses={
        303: {"description": "Redirects to extractor dashboard after clearing cookie"},
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized access"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
def logout():
    """Clear session cookie and redirect to extractor dashboard."""
    response = RedirectResponse(url="/extractor", status_code=303)
    response.delete_cookie(key="abs_token")
    return response


@app.get(
    "/api/installation-date",
    responses={
        200: {"description": "Returns the immutable installation date and cutoff configuration"},
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized access"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
@app.get(
    "/api/user/installation-date",
    responses={
        200: {"description": "Returns the immutable installation date and cutoff configuration"},
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized access"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
def get_installation_date():
    """Returns the immutable installation date and bookmark sync cutoff configuration."""
    return {
        "status": "success",
        "installation_date": INSTALLATION_CONFIG.get("installation_date"),
        "cutoff_datetime": INSTALLATION_CONFIG.get("cutoff_datetime"),
        "cutoff_timestamp": INSTALLATION_CONFIG.get("cutoff_timestamp"),
        "config": INSTALLATION_CONFIG
    }


@app.get(
    "/api/health",
    responses={
        200: {"description": "Health check status and daemon listener telemetry"},
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized access"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
def health_check():
    """Health check endpoint providing configuration, listener mode, and system status."""
    return {
        "status": "healthy",
        "service": "Audiobookshelf Bookmarks Manager",
        "tagline": "Autonomous manager and sidecar for Audiobookshelf with real-time Socket.IO listener & automated bookmark clipping",
        "version": "2.1.0",
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

@app.get(
    "/",
    response_class=HTMLResponse,
    responses={
        200: {"description": "Render bookmarks extractor & sidecar dashboard"},
        400: {"description": "Invalid parameter format or authorization header"},
        401: {"description": "Unauthorized access or invalid authentication token"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
        502: {"description": "Cannot connect to upstream Audiobookshelf server"}
    }
)
def root_view(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    Renders the Bookmarks Extractor & Sidecar Dashboard on the root URL.
    Zero-Proxy sidecar mode: Does not proxy Audiobookshelf traffic.
    """
    return render_extractor_dashboard(request, token=token, authorization=authorization)


if __name__ == "__main__":
    import uvicorn
    # Default sidecar port is 13380 (SIDECAR_PORT is prioritized over PORT so it does not conflict if PORT is set to 13379 for the web dashboard)
    port = int(os.environ.get("SIDECAR_PORT") or os.environ.get("PORT") or "13380")
    # Secure host binding: bind strictly to localhost (127.0.0.1) to avoid exposing service to unauthorized external network interfaces
    host = os.environ.get("HOST") or os.environ.get("BIND_ADDRESS") or "127.0.0.1"
    # Do not reload by default to avoid watching parent directories (e.g. /home/pi)
    reload_enabled = os.environ.get("RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[BASE_DIR] if reload_enabled else None,
        app_dir=BASE_DIR
    )
