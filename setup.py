#!/usr/bin/env python3
"""
Audiobookshelf Bookmarks Manager - Interactive Setup Assistant
Cross-platform interactive wizard for configuring environment, paths, and ports.
Can be executed anytime via: python3 setup.py or ./setup.sh
"""

import os
import sys
import shutil
import re

def sanitize_input_string(s: str) -> str:
    """Strips null bytes and control characters from user input."""
    if not s:
        return ""
    return "".join(ch for ch in s.strip() if ch >= " " and ch != "\x7f")

FORBIDDEN_SYSTEM_PATHS = {
    "/", "/bin", "/sbin", "/usr", "/usr/bin", "/usr/sbin", "/etc", "/var",
    "/dev", "/proc", "/sys", "/boot", "/lib", "/lib64", "/root"
}

def is_safe_filesystem_path(target_path: str, base_dir: str = None) -> bool:
    """Verifies that the target path does not escape into dangerous system directories."""
    try:
        resolved = os.path.realpath(os.path.abspath(target_path))
        if resolved in FORBIDDEN_SYSTEM_PATHS:
            return False
        for sys_dir in FORBIDDEN_SYSTEM_PATHS:
            if sys_dir != "/" and (resolved == sys_dir or resolved.startswith(sys_dir + os.sep)):
                if base_dir and resolved.startswith(os.path.realpath(os.path.abspath(base_dir))):
                    continue
                return False
        return True
    except Exception:
        return False

def sanitize_directory_path(p: str, default: str, base_dir: str = None) -> str:
    """Sanitizes and canonicalizes directory paths, blocking null bytes, invalid characters, and system path traversal."""
    if not p or not isinstance(p, str):
        return default
    clean = sanitize_input_string(p)
    if not clean or "\0" in clean:
        print(f"   [!] Invalid directory path. Falling back to default: {default}")
        return default
    try:
        expanded = os.path.expanduser(os.path.expandvars(clean))
        normalized = os.path.abspath(expanded)
        if not is_safe_filesystem_path(normalized, base_dir):
            print(f"   [!] Path traversal or protected directory detected. Falling back to default: {default}")
            return default
        return normalized
    except Exception:
        return default

def sanitize_port(p_str: str, default: str) -> str:
    """Validates that port is a valid numeric TCP port between 1 and 65535."""
    clean = sanitize_input_string(p_str)
    if clean.isdigit() and 1 <= int(clean) <= 65535:
        return clean
    print(f"   [!] Invalid port '{p_str}'. Using default: {default}")
    return default

def sanitize_url(url: str, default: str) -> str:
    """Sanitizes server URL against injection and invalid characters."""
    clean = sanitize_input_string(url)
    if not clean or any(ch in clean for ch in ['"', "'", "\n", "\r", "\\"]):
        return default
    return clean.rstrip("/")

def get_input(prompt: str, default: str = "") -> str:
    default_hint = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{default_hint}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup aborted by user.")
        sys.exit(1)
    clean_val = sanitize_input_string(val)
    return clean_val if clean_val else default

def _parse_env_line(line: str, existing: dict) -> None:
    cleaned = line.strip()
    if not cleaned or cleaned.startswith("#") or "=" not in cleaned:
        return
    k, v = cleaned.split("=", 1)
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    if k in existing:
        existing[k] = v
    elif k == "ABS_SERVER_URL":
        existing["ABS_TARGET_SERVER"] = v
    elif k == "SNIPPETS_DIR":
        existing["VOLUME_DIR"] = v

def _load_existing_env(env_file: str, base_dir: str) -> dict:
    existing = {
        "ABS_TARGET_SERVER": "http://localhost:13378",
        "VOLUME_DIR": os.path.join(base_dir, "bookmarks"),
        "AUDIOBOOKS_PATH": "/path/to/your/audiobooks",
        "SIDECAR_PORT": "13380",
        "PORT": "13379",
        "WHISPER_MODEL": "base.en",
        "SNIPPET_DURATION": "60",
        "SNIPPET_PRE_ROLL": "30.0",
        "INTERCEPT_SNIPPET_DURATION": "60",
        "INTERCEPT_PRE_ROLL": "30.0",
    }
    if not os.path.exists(env_file):
        return existing

    print(f"[i] Found existing .env at {env_file}. Loading current values as defaults...\n")
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                _parse_env_line(line, existing)
    except Exception as e:
        print(f"Warning: Could not read existing .env: {e}")
    return existing

def _prompt_abs_target(default_target: str) -> str:
    print("1. Audiobookshelf Target Server URL")
    print("   The URL of your existing Audiobookshelf server (e.g. http://localhost:13378 or http://audiobookshelf:80)")
    print("   Note: 13378 is Audiobookshelf's default port. The manager connects to it; it does NOT listen on 13378.")
    raw_abs_target = get_input("   Target ABS URL", default_target)
    return sanitize_url(raw_abs_target, default_target)

def _prompt_volume_dir(existing_val: str, base_dir: str) -> str:
    print("\n2. Output Directory for Bookmarks & Transcripts (VOLUME_DIR)")
    print("   Where sliced audio (.mp3), markdown transcripts (.md), and JSON metadata will be saved.")
    raw_vol_dir = get_input("   Directory path", existing_val)
    return sanitize_directory_path(raw_vol_dir, existing_val, base_dir=base_dir)

def _prompt_audiobooks_path(existing_val: str, base_dir: str) -> str:
    print("\n3. Audiobooks Media Library Directory (Host Path)")
    print("   Host server path where audiobook files are located (used for read-only Docker mounts)")
    raw_audiobooks_path = get_input("   Audiobooks path", existing_val)
    return sanitize_directory_path(raw_audiobooks_path, existing_val, base_dir=base_dir)

def _prompt_network_ports(default_sidecar: str, default_web: str) -> tuple:
    print("\n4. Network Ports Configuration")
    print("   Reserved reference:")
    print("   - 13378: Used exclusively by Audiobookshelf (Upstream server).")
    print("   - 13380 (or 13377): Recommended for Python Sidecar & Interceptor Proxy.")
    print("   - 13379 (or 13376): Recommended for Web Dashboard & UI.")
    print("   (Ports 3000 and 8080 are not used on production servers to prevent conflicts).\n")

    sidecar_port = default_sidecar
    while True:
        raw_sidecar = get_input("   Python Sidecar Port", default_sidecar)
        sidecar_port = sanitize_port(raw_sidecar, default_sidecar)
        if sidecar_port == "13378":
            print("   [!] Port 13378 is reserved for Audiobookshelf. Please choose another port (e.g. 13380 or 13377).")
        else:
            break

    web_port = default_web
    while True:
        raw_web = get_input("   Web Dashboard Port", default_web)
        web_port = sanitize_port(raw_web, default_web)
        if web_port == "13378":
            print("   [!] Port 13378 is reserved for Audiobookshelf. Please choose another port (e.g. 13379 or 13376).")
        elif web_port == sidecar_port:
            print(f"   [!] Web port cannot be the same as Sidecar port ({sidecar_port}). Please choose a unique port.")
        else:
            break

    return sidecar_port, web_port

def _prompt_whisper_model(default_model: str) -> str:
    print("\n5. Speech-to-Text Model Selection")
    print("   1) base.en   (Recommended: ~140MB, accurate & fast on CPU / Raspberry Pi)")
    print("   2) tiny.en   (Fastest: ~75MB, minimal RAM usage)")
    print("   3) small.en  (High Accuracy: ~460MB, requires more CPU/RAM)")
    print("   4) medium.en (Highest Accuracy: ~1.5GB)")
    model_choice = get_input(f"   Select model [1-4, default for {default_model}]", "")
    model_map = {"1": "base.en", "2": "tiny.en", "3": "small.en", "4": "medium.en"}
    return model_map.get(model_choice, default_model)

def _write_env_file(env_file: str, cfg: dict) -> None:
    print(f"\n[*] Writing configuration to {env_file}...")
    env_content = f"""# Audiobookshelf Bookmarks Manager - Configuration
# Generated by setup.py

# Upstream Audiobookshelf Server (The sidecar connects to this; does NOT listen on 13378)
ABS_TARGET_SERVER="{cfg['abs_target']}"
ABS_SERVER_URL="{cfg['abs_target']}"

# Storage paths
VOLUME_DIR="{cfg['vol_dir']}"
SNIPPETS_DIR="{cfg['vol_dir']}"
AUDIOBOOKS_PATH="{cfg['audiobooks_path']}"

# Network Ports
PORT={cfg['web_port']}
SIDECAR_PORT={cfg['sidecar_port']}

# Audio & Transcription Settings
SNIPPET_DURATION={cfg['existing']['SNIPPET_DURATION']}
SNIPPET_PRE_ROLL={cfg['existing']['SNIPPET_PRE_ROLL']}
INTERCEPT_SNIPPET_DURATION={cfg['existing'].get('INTERCEPT_SNIPPET_DURATION', '60')}
INTERCEPT_PRE_ROLL={cfg['existing'].get('INTERCEPT_PRE_ROLL', '30.0')}
WHISPER_MODEL={cfg['whisper_model']}
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
VOSK_MODEL_NAME=vosk-model-small-en-us-0.15

# Runtime Flags
RELOAD=false
NODE_ENV=production
"""
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(env_content)
    try:
        os.chmod(env_file, 0o600)
    except Exception:
        pass

def _update_gitignore(base_dir: str) -> None:
    gitignore_path = os.path.join(base_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        return
    with open(gitignore_path, "r", encoding="utf-8") as f:
        gi_content = f.read()
    needs_write = False
    patterns = [".env", ".env.*", "venv/", "installation_date.json", ".installation_date.json", ".deleted_tombstones.json"]
    for ig_pattern in patterns:
        if ig_pattern not in gi_content:
            gi_content += f"\n{ig_pattern}\n"
            needs_write = True
    if needs_write:
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(gi_content)
    print("   [OK] .env, venv, and dynamic installation_date.json are strictly ignored in .gitignore.")

def _setup_virtualenv(base_dir: str) -> str:
    import subprocess
    venv_dir = os.path.join(base_dir, "venv")
    venv_py = os.path.join(venv_dir, "bin", "python3") if os.name != "nt" else os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        print(f"\n[*] Creating Python virtual environment in {venv_dir}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            print(f"   [OK] Virtual environment created at {venv_dir}")
        except Exception as err:
            print(f"   [!] Could not auto-create venv: {err}")

    if os.path.exists(venv_py):
        req_file = os.path.join(base_dir, "requirements.txt")
        if os.path.exists(req_file):
            print("   Installing requirements into virtual environment...")
            try:
                subprocess.run([venv_py, "-m", "pip", "install", "-r", req_file], check=False)
                print("   [OK] Requirements installed into venv.")
            except Exception as e:
                print(f"   Notice: pip install returned: {e}")
    return venv_py if os.path.exists(venv_py) else sys.executable

def _initialize_installation_date(base_dir: str, py_runner: str) -> None:
    init_script = os.path.join(base_dir, "init_installation_date.py")
    if os.path.exists(init_script):
        try:
            import subprocess
            subprocess.run([py_runner, init_script], check=False)
        except Exception as e:
            print(f"   [!] Note: installation date initialization deferred: {e}")

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base_dir, ".env")
    
    print("\n" + "=" * 65)
    print("   Audiobookshelf Bookmarks Manager - Server Configuration")
    print("=" * 65)
    print("This wizard configures your environment variables for local installation,")
    print("Docker Compose, or PM2 on your host server.\n")

    existing = _load_existing_env(env_file, base_dir)
    abs_target = _prompt_abs_target(existing["ABS_TARGET_SERVER"])
    vol_dir = _prompt_volume_dir(existing["VOLUME_DIR"], base_dir)
    audiobooks_path = _prompt_audiobooks_path(existing["AUDIOBOOKS_PATH"], base_dir)
    sidecar_port, web_port = _prompt_network_ports(existing["SIDECAR_PORT"], existing["PORT"])
    whisper_model = _prompt_whisper_model(existing["WHISPER_MODEL"])

    cfg = {
        "abs_target": abs_target,
        "vol_dir": vol_dir,
        "audiobooks_path": audiobooks_path,
        "sidecar_port": sidecar_port,
        "web_port": web_port,
        "whisper_model": whisper_model,
        "existing": existing
    }
    _write_env_file(env_file, cfg)
    _update_gitignore(base_dir)
    py_runner = _setup_virtualenv(base_dir)
    _initialize_installation_date(base_dir, py_runner)

    print("\n" + "=" * 65)
    print("   Configuration Complete!")
    print("=" * 65)
    print(f"Saved configuration to: {env_file}")
    print(f"  - Target ABS Server: {abs_target}")
    print(f"  - Bookmarks Directory: {vol_dir}")
    print(f"  - Audiobooks Directory: {audiobooks_path}")
    print(f"  - Python Venv: {py_runner}")
    print(f"  - Web Dashboard: http://localhost:{web_port}")
    print(f"  - Sidecar / Interceptor: http://localhost:{sidecar_port}")
    print(f"  - Whisper Model: {whisper_model}\n")
    print("Next steps to start:")
    print("  Docker: docker compose up -d")
    print("  PM2:    pm2 start ecosystem.config.cjs")
    print(f"  Direct: npm run build && npm start & {py_runner} main.py\n")

if __name__ == "__main__":
    main()
