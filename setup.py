#!/usr/bin/env python3
"""
Audiobookshelf Bookmarks Manager - Interactive Setup Assistant
Cross-platform interactive wizard for configuring environment, paths, and ports.
Can be executed anytime via: python3 setup.py or ./setup.sh
"""

import os
import sys
import shutil

def get_input(prompt: str, default: str = "") -> str:
    default_hint = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{default_hint}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup aborted by user.")
        sys.exit(1)
    return val if val else default

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_file = os.path.join(base_dir, ".env")
    
    print("\n" + "=" * 65)
    print("   Audiobookshelf Bookmarks Manager - Server Configuration")
    print("=" * 65)
    print("This wizard configures your environment variables for local installation,")
    print("Docker Compose, or PM2 on your host server.\n")

    # Read existing values if present
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

    if os.path.exists(env_file):
        print(f"[i] Found existing .env at {env_file}. Loading current values as defaults...\n")
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k in existing:
                            existing[k] = v
                        elif k == "ABS_SERVER_URL":
                            existing["ABS_TARGET_SERVER"] = v
                        elif k == "SNIPPETS_DIR":
                            existing["VOLUME_DIR"] = v
        except Exception as e:
            print(f"Warning: Could not read existing .env: {e}")

    # 1. ABS Target Server
    print("1. Audiobookshelf Target Server URL")
    print("   The URL of your existing Audiobookshelf server (e.g. http://localhost:13378 or http://audiobookshelf:80)")
    print("   Note: 13378 is Audiobookshelf's default port. The manager connects to it; it does NOT listen on 13378.")
    abs_target = get_input("   Target ABS URL", existing["ABS_TARGET_SERVER"]).rstrip("/")

    # 2. Output directory
    print("\n2. Output Directory for Bookmarks & Transcripts (VOLUME_DIR)")
    print("   Where sliced audio (.mp3), markdown transcripts (.md), and JSON metadata will be saved.")
    vol_dir = get_input("   Directory path", existing["VOLUME_DIR"])
    try:
        os.makedirs(vol_dir, exist_ok=True)
    except Exception as e:
        print(f"   Warning: Could not create directory {vol_dir}: {e}")

    # 3. Audiobooks path
    print("\n3. Audiobooks Media Library Directory (Host Path)")
    print("   Host server path where audiobook files are located (used for read-only Docker mounts)")
    audiobooks_path = get_input("   Audiobooks path", existing["AUDIOBOOKS_PATH"])

    # 4. Ports
    print("\n4. Network Ports Configuration")
    print("   Reserved reference:")
    print("   - 13378: Used exclusively by Audiobookshelf (Upstream server).")
    print("   - 13380 (or 13377): Recommended for Python Sidecar & Interceptor Proxy.")
    print("   - 13379 (or 13376): Recommended for Web Dashboard & UI.")
    print("   (Ports 3000 and 8080 are not used on production servers to prevent conflicts).\n")

    while True:
        sidecar_port = get_input("   Python Sidecar Port", existing["SIDECAR_PORT"])
        if sidecar_port == "13378":
            print("   [!] Port 13378 is reserved for Audiobookshelf. Please choose another port (e.g. 13380 or 13377).")
        else:
            break

    while True:
        web_port = get_input("   Web Dashboard Port", existing["PORT"])
        if web_port == "13378":
            print("   [!] Port 13378 is reserved for Audiobookshelf. Please choose another port (e.g. 13379 or 13376).")
        elif web_port == sidecar_port:
            print(f"   [!] Web port cannot be the same as Sidecar port ({sidecar_port}). Please choose a unique port.")
        else:
            break

    # 5. Whisper model
    print("\n5. Speech-to-Text Model Selection")
    print("   1) base.en   (Recommended: ~140MB, accurate & fast on CPU / Raspberry Pi)")
    print("   2) tiny.en   (Fastest: ~75MB, minimal RAM usage)")
    print("   3) small.en  (High Accuracy: ~460MB, requires more CPU/RAM)")
    print("   4) medium.en (Highest Accuracy: ~1.5GB)")
    model_choice = get_input(f"   Select model [1-4, default for {existing['WHISPER_MODEL']}]", "")
    model_map = {"1": "base.en", "2": "tiny.en", "3": "small.en", "4": "medium.en"}
    whisper_model = model_map.get(model_choice, existing["WHISPER_MODEL"])

    # Write .env
    print(f"\n[*] Writing configuration to {env_file}...")
    env_content = f"""# Audiobookshelf Bookmarks Manager - Configuration
# Generated by setup.py

# Upstream Audiobookshelf Server (The sidecar connects to this; does NOT listen on 13378)
ABS_TARGET_SERVER="{abs_target}"
ABS_SERVER_URL="{abs_target}"

# Storage paths
VOLUME_DIR="{vol_dir}"
SNIPPETS_DIR="{vol_dir}"
AUDIOBOOKS_PATH="{audiobooks_path}"

# Network Ports
PORT={web_port}
SIDECAR_PORT={sidecar_port}

# Audio & Transcription Settings
SNIPPET_DURATION={existing["SNIPPET_DURATION"]}
SNIPPET_PRE_ROLL={existing["SNIPPET_PRE_ROLL"]}
INTERCEPT_SNIPPET_DURATION={existing.get("INTERCEPT_SNIPPET_DURATION", "60")}
INTERCEPT_PRE_ROLL={existing.get("INTERCEPT_PRE_ROLL", "30.0")}
WHISPER_MODEL={whisper_model}
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

    # Verify .gitignore
    gitignore_path = os.path.join(base_dir, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as f:
            gi_content = f.read()
        needs_write = False
        for ig_pattern in [".env", ".env.*", "venv/", "installation_date.json", ".installation_date.json", ".deleted_tombstones.json"]:
            if ig_pattern not in gi_content:
                gi_content += f"\n{ig_pattern}\n"
                needs_write = True
        if needs_write:
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write(gi_content)
        print("   [OK] .env, venv, and dynamic installation_date.json are strictly ignored in .gitignore.")

    # Initialize venv (default mode)
    venv_dir = os.path.join(base_dir, "venv")
    venv_py = os.path.join(venv_dir, "bin", "python3") if os.name != "nt" else os.path.join(venv_dir, "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        print(f"\n[*] Creating Python virtual environment in {venv_dir}...")
        import subprocess
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

    # Initialize installation_date.json dynamically if not already present
    # Run after successful installation, before app start; never overwrites on updates
    init_script = os.path.join(base_dir, "init_installation_date.py")
    if os.path.exists(init_script):
        py_runner = venv_py if os.path.exists(venv_py) else sys.executable
        try:
            import subprocess
            subprocess.run([py_runner, init_script], check=False)
        except Exception as e:
            print(f"   [!] Note: installation date initialization deferred: {e}")

    print("\n" + "=" * 65)
    print("   Configuration Complete!")
    print("=" * 65)
    print(f"Saved configuration to: {env_file}")
    print(f"  - Target ABS Server: {abs_target}")
    print(f"  - Bookmarks Directory: {vol_dir}")
    print(f"  - Audiobooks Directory: {audiobooks_path}")
    print(f"  - Python Venv: {venv_py if os.path.exists(venv_py) else sys.executable}")
    print(f"  - Web Dashboard: http://localhost:{web_port}")
    print(f"  - Sidecar / Interceptor: http://localhost:{sidecar_port}")
    print(f"  - Whisper Model: {whisper_model}\n")
    print("Next steps to start:")
    print("  Docker: docker compose up -d")
    print("  PM2:    pm2 start ecosystem.config.cjs")
    print(f"  Direct: npm run build && npm start & {venv_py} main.py\n")

if __name__ == "__main__":
    main()
