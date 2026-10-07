#!/usr/bin/env python3
"""
Installation Date Initializer
Creates the immutable installation_date.json configuration file at first installation time,
AFTER successful installation and BEFORE the application server starts.

Rules:
1. If installation_date.json already exists in any candidate location with valid values:
   DO NOT overwrite or modify it. Preserves existing installation date across updates.
2. If it does not exist:
   Dynamically records the current system date and time as the installation moment,
   computing the sync cutoff at 00:00:00 of that installation day.
3. This file is dynamic runtime state and is NOT part of git or repo source code.
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any


def _check_existing_installation_config(candidates: List[str]) -> bool:
    """Checks if valid installation configuration already exists across candidate locations."""
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("installation_date") or data.get("cutoff_datetime"):
                    print(f"[Installation Date] Existing configuration found at '{p}'. Preserving without overwrite.")
                    print(f"                     Installed at: {data.get('installed_at')}, Cutoff: {data.get('cutoff_datetime')}")
                    return True
        except Exception as e:
            print(f"[Installation Date] Notice: Could not parse '{p}': {e}")
    return False


def _build_initial_config_data() -> Tuple[Dict[str, Any], str, str, datetime]:
    """Generates initial installation date, cutoff metadata, and configuration dictionary."""
    now_local = datetime.now()
    now_utc = datetime.now(timezone.utc)

    installation_date_str = now_local.strftime("%Y-%m-%d")
    cutoff_datetime_str = f"{installation_date_str}T00:00:00"

    # Compute cutoff timestamp at 00:00:00 of the installation day
    cutoff_dt = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0)
    cutoff_timestamp = cutoff_dt.timestamp()

    config_data = {
        "installation_date": installation_date_str,
        "cutoff_datetime": cutoff_datetime_str,
        "cutoff_timestamp": cutoff_timestamp,
        "installed_at": now_local.isoformat(),
        "installed_at_utc": now_utc.isoformat(),
        "note": f"Immutable installation date created on first install. Bookmarks created prior to {cutoff_datetime_str} are excluded from automated extraction."
    }
    return config_data, installation_date_str, cutoff_datetime_str, now_local


def _write_config_to_targets(save_targets: List[str], config_data: Dict[str, Any]) -> bool:
    """Persists configuration dictionary to candidate target files."""
    created_any = False
    for target in save_targets:
        try:
            target_dir = os.path.dirname(target)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            created_any = True
            print(f"[Installation Date] Created initial installation config at '{target}'")
        except Exception as e:
            print(f"[Installation Date] Warning: Could not write to '{target}': {e}")
    return created_any


def init_installation_date() -> int:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Read VOLUME_DIR from env if available (or default to base_dir/bookmarks)
    volume_dir = os.environ.get("VOLUME_DIR", os.path.join(base_dir, "bookmarks"))

    # Candidate file locations
    candidates = [
        os.path.join(base_dir, "installation_date.json"),
        os.path.join(volume_dir, "installation_date.json"),
        os.path.join(base_dir, ".installation_date.json"),
        os.path.join(volume_dir, ".installation_date.json")
    ]

    # Check if file already exists in any candidate location
    if _check_existing_installation_config(candidates):
        return 0

    # File does not exist: dynamically determine current system date and time
    config_data, installation_date_str, cutoff_datetime_str, now_local = _build_initial_config_data()

    # Save to base_dir and volume_dir if distinct
    save_targets = [candidates[0]]
    if volume_dir and volume_dir != base_dir:
        save_targets.append(candidates[1])

    if _write_config_to_targets(save_targets, config_data):
        print("[Installation Date] Initialization successful!")
        print(f"                     Installation Date: {installation_date_str}")
        print(f"                     Sync Cutoff Date:  {cutoff_datetime_str}")
        print(f"                     Recorded Time:     {now_local.isoformat()}")
        return 0

    print("[Installation Date] Failed to write installation_date.json to any target location.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(init_installation_date())
