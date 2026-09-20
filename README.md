# Audiobookshelf Bookmarks Manager

**Version 2.0 (Zero-Proxy Event-Driven Architecture)**  
Automated bookmark audio clipper, Whisper speech-to-text transcriber, and web dashboard for [Audiobookshelf](https://www.audiobookshelf.org/).

GitHub: [https://github.com/raviwarrier/audiobookshelf-bookmarks-manager](https://github.com/raviwarrier/audiobookshelf-bookmarks-manager)

---

## What It Does

Audiobookshelf Bookmarks Manager automatically extracts audio clips corresponding to your bookmarks or listening positions in Audiobookshelf, transcribes them into searchable markdown notes using AI speech recognition (`faster-whisper` or `Vosk`), and neatly organizes the resulting `.mp3`, `.md`, and `.json` files per user (`{VOLUME_DIR}/{username}/bookmarks/{book_title}/`).

### Key Features at a Glance

- 🎧 **Works with Official ABS Apps**: Zero companion apps or modified APKs required. Keep using the official Audiobookshelf mobile apps (iOS & Android) and web client directly.
- ⚡ **Zero-Proxy Architecture**: Unlike v1, you **do not** need to route your Audiobookshelf traffic or mobile apps through a reverse proxy. Audiobookshelf apps connect directly to Audiobookshelf, completely eliminating WebSocket dropouts, socket disconnect toasts, and proxy bottlenecks.
- 📡 **Autonomous Real-Time Event Listener**: The Python sidecar connects directly to Audiobookshelf's WebSocket (`AbsSocketIoListener`) and listens for bookmark updates in real-time. Whenever you tap Bookmark on your phone, the sidecar detects it within seconds and starts clipping in the background.
- ⏱️ **Lightweight & Raspberry Pi Friendly**: Audio extraction with FFmpeg and speech recognition are run with low process priority (`os.nice(15)`) and strict CPU thread limits to ensure your Raspberry Pi or home server stays snappy and responsive.
- 🎙️ **AI Speech-to-Text Transcription**: Powered by `faster-whisper` with automatic background model pre-warming or on-demand lazy loading, plus lightweight `Vosk` fallback.
- 📁 **Direct Local Slicing & Docker Path Mapping**: Uses `ffmpeg` to slice lossless audio clips directly from source `.m4b`/`.mp3` files, with `PATH_MAPPINGS` support for translating ABS container paths to host paths.
- ⏱️ **Adjust Duration / Context**: Re-slice and re-transcribe existing snippets on the fly directly from the Web UI. Expand pre-roll or total duration to capture the full conversation while preserving the original bookmark anchor.
- 📚 **Book Filtering Tabs**: Instantly isolate bookmarks by book or view all snippets across your entire library with dynamic count badges.
- 📦 **Per-Book Bulk Export**: Directly download all bookmarks for any book from the snippet card dropdown as a full **ZIP package** (all audio MP3s + individual MD files) or a single **consolidated Markdown document** ready for Obsidian, Logseq, or Notion.
- 🔄 **Real-Time Live UI Updates**: Frontend automatically detects newly finished background extractions and updates the feed without requiring manual page reloads or re-authenticating.
- 🔒 **Zero-Disk Credential Security**: In-memory ephemeral encryption (AES-256) ensures API keys and passwords are never persisted to disk, cookies, or browser databases.

---

## Architecture: Zero-Proxy vs. The Old Way

### The v2 Zero-Proxy Architecture (Current)

In version 2.0, your mobile apps and browser clients connect **directly** to Audiobookshelf:

```
[ Mobile App / Browser ] ──────────────────► [ Audiobookshelf (Port 13378) ]
                                                        ▲
                                                        │ (WebSocket & REST)
                                                        ▼
                                            [ abs-manager-sidecar (Port 13380) ]
                                            - Listens for bookmark events
                                            - FFmpeg audio slice (low CPU priority)
                                            - Whisper / Vosk transcription
                                            - Saves clips to VOLUME_DIR
                                                        ▲
                                                        │
                                            [ abs-manager-web (Port 13379) ]
                                            - Interactive Web Dashboard
                                            - Player, Search & Book Export
```

### Why This Is Better:
1. **No Socket Disconnections**: Official ABS apps connect directly to ABS. Long-lived streaming and player sync sockets never drop.
2. **Instant Response Times**: Browsing libraries, streaming audio, and saving bookmarks happen at native Audiobookshelf speed.
3. **Low Resource Footprint**: Slicing and transcribing run in the background with `os.nice(15)` and bounded CPU threads, preventing Raspberry Pi CPU spikes.

---

## Security & Privacy Assurance

- **Zero Persistent Storage**: Your Audiobookshelf credentials, API tokens, and passwords are encrypted in-memory using an ephemeral AES-256 session key.
- **No Disk Storage**: Tokens and secrets are **never** written to `localStorage`, cookies, IndexedDB, or server-side database files.
- **Session-Only Lifetime**: All keys and credentials vanish immediately when you refresh the page, close the browser tab, or click Disconnect.

---

## Screenshots

### Main Screen
![Main Screen](public/app_screenshots/main_screen.png)

### Snippets View
![Snippets View](public/app_screenshots/snippets.png)

---

## Prerequisites

### General Requirements
- **Audiobookshelf Server** (v2.0+) up and running with audiobooks and bookmarks.
- **Audio Files Access**: Direct local directory access or Docker volume mount to your Audiobookshelf audio files so the manager can slice segments directly from source files.

### Host Prerequisites (NPM / PM2 / Linux)
- **Node.js**: `v18.0.0` or higher (with `npm`).
- **Python**: `v3.10` or higher (with `pip` and `venv`).
- **FFmpeg**: Mandatory system tool for audio slicing and extraction:
  - *Ubuntu / Debian*: `sudo apt-get install -y ffmpeg`
  - *macOS (Homebrew)*: `brew install ffmpeg`
  - *Arch Linux*: `sudo pacman -S ffmpeg`
  - *Fedora / RHEL*: `sudo dnf install -y ffmpeg`

---

## URL & Port Quick Reference

| Service Name | Default Port | Variable Name(s) | Address in Setup | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **1. Audiobookshelf Server** | **`13378`** | `ABS_TARGET_SERVER`<br>`ABS_SERVER_URL` | `http://192.168.68.102:13378`<br>`https://abs.example.com` | Your media server. Mobile app connects directly here. |
| **2. Sidecar & Event Listener** | **`13380`** | `SIDECAR_PORT`<br>`PORT: 13380` | `http://192.168.68.102:13380` | Listens to ABS events, slices audio, runs Whisper. |
| **3. Web Dashboard** | **`13379`** | `PORT: 13379` | `http://192.168.68.102:13379` | Browser interface for browsing, listening, and exporting. |

> **Docker Note (Host IP vs. Localhost):**
> When Audiobookshelf is running in Docker, set `ABS_TARGET_SERVER` to your host server's LAN IP (e.g. `http://192.168.68.102:13378`) rather than `localhost` so the sidecar process can communicate with the container published port.

---

## Installation & Setup

### 1. PM2 (Recommended for Bare-Metal / Raspberry Pi)

The project includes an `ecosystem.config.cjs` template designed for production hosting using PM2:

```bash
# 1. Clone repository
git clone https://github.com/raviwarrier/audiobookshelf-bookmarks-manager.git
cd audiobookshelf-bookmarks-manager

# 2. Run the automated installer & updater
# (This automatically verifies ffmpeg, sets up python venv, installs dependencies, and builds the frontend)
./update.sh

# 3. Configure your server settings
./setup.sh

# 4. Start services with PM2
pm2 start ecosystem.config.cjs
pm2 save
```

### 2. Docker

```bash
git clone https://github.com/raviwarrier/audiobookshelf-bookmarks-manager.git
cd audiobookshelf-bookmarks-manager

# Run interactive configuration
./setup.sh

# Start containers
docker compose up -d --build
```

---

## Interactive Setup Wizard (CLI)

The repository includes an interactive configuration wizard that guides you through setting your Audiobookshelf URL, storage directory (`VOLUME_DIR`), and network ports:

```bash
./setup.sh
# or: npm run setup
# or: python3 setup.py
```

The wizard prompts you for:
1. **Audiobookshelf Target Server URL** (e.g. `http://192.168.68.102:13378`)
2. **Bookmarks & Volume Storage Directory** (`VOLUME_DIR`, e.g. `/srv/ssd/Appdata/local/advplyr-bookshelf/bookmarks`)
3. **Audiobooks Media Library Directory** (Host path where audiobooks are stored)
4. **FastAPI Sidecar Port** (default `13380`)
5. **Web Dashboard Port** (default `13379`)
6. **Whisper Transcription Model** (`base.en`, `tiny.en`, `small.en`, or `medium.en`)

---

## Updating

To update everything in the future with one command:

```bash
cd audiobookshelf-bookmarks-manager
git pull origin main
./update.sh
```

The update script automatically:
- Checks system `ffmpeg`
- Updates Python dependencies inside `venv`
- Updates and rebuilds the Node.js frontend (`dist/server.cjs`)
- Restarts running PM2 processes (`abs-manager-web`, `abs-manager-sidecar`) with updated environment flags.

---

## Configuration & Environment Variables

All settings can be specified in a `.env` file, in `docker-compose.yml`, or in `ecosystem.config.cjs`:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `ABS_TARGET_SERVER` | `http://localhost:13378` | **Audiobookshelf Server**: The internal or LAN HTTP URL of your Audiobookshelf server (e.g. `http://192.168.68.102:13378`). |
| `ABS_SERVER_URL` | `http://localhost:13378` | Backward-compatible fallback for `ABS_TARGET_SERVER`. |
| `PORT` | `13379` (Web) / `13380` (Sidecar) | Port on which the respective service listens. |
| `SIDECAR_PORT` | `13380` | Explicit override port for the FastAPI sidecar. |
| `VOLUME_DIR` | `/data` | Root output directory where generated audio clips (`.mp3`), transcripts (`.md`), and metadata (`.json`) are stored. |
| `AUDIOBOOKS_PATH` | `/audiobooks` | Host server directory where audiobook media files are located. |
| `PATH_MAPPINGS` | *None* | Comma-separated pairs mapping ABS container paths to host paths (e.g. `/audiobooks:/srv/ssd/Bookshelf/Audiobooks,/summaries:/srv/ssd/Bookshelf/Summaries`). |
| `BOOKMARK_SYNC_INTERVAL` | `120` | Interval in seconds for the background daemon to ensure zero missed bookmarks. |
| `INTERCEPT_SNIPPET_DURATION` | `60` | Total length (seconds) of extracted audio clips for bookmarks captured via mobile/web apps. |
| `INTERCEPT_PRE_ROLL` | `30.0` | Seconds captured before the bookmark timestamp. |
| `WHISPER_MODEL` | `base.en` | Model size for faster-whisper (`tiny.en`, `base.en`, `small.en`, `medium.en`). |
| `WHISPER_THREADS` | `2` | Number of CPU threads allocated to Whisper to prevent overloading Raspberry Pis. |
| `PREWARM_WHISPER` | `false` | When `false`, loads Whisper on-demand during extraction to conserve idle RAM and CPU. |

---

## API Usage & Examples

### 1. Health Check
```bash
curl -X GET http://localhost:13380/api/health
```
Response:
```json
{
  "status": "healthy",
  "service": "Audiobookshelf Bookmarks Manager",
  "version": "2.0.0",
  "architecture_mode": "Zero-Proxy Event-Driven Sidecar (Port 13380)",
  "abs_target_server": "http://192.168.68.102:13378"
}
```

### 2. Extract Active Listening Bookmark
```bash
curl -X POST http://localhost:13380/api/snippet \
  -H "Authorization: Bearer <ABS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"duration": 60}'
```

### 3. List User Bookmarks
```bash
curl -X GET http://localhost:13380/api/bookmarks \
  -H "Authorization: Bearer <ABS_API_TOKEN>"
```

### 4. Adjust Snippet Duration & Pre-Roll (Re-clipping)
```bash
curl -X POST http://localhost:13380/api/snippets/expand \
  -H "Authorization: Bearer <ABS_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "snippet_id": "20260910_103000",
    "pre_roll": 45,
    "duration": 90
  }'
```

### 5. Export Book Archive (ZIP or Combined Markdown)
```bash
# Download complete ZIP package
curl -O -J -L "http://localhost:13380/api/export-book?book_title=Project%20Hail%20Mary&format=zip" \
  -H "Authorization: Bearer <ABS_API_TOKEN>"

# Download consolidated Markdown digest
curl -O -J -L "http://localhost:13380/api/export-book?book_title=Project%20Hail%20Mary&format=markdown" \
  -H "Authorization: Bearer <ABS_API_TOKEN>"
```

---

## License (MIT in Plain English)

You are completely free to use, copy, modify, merge, publish, distribute, and even sell copies of this software for personal, educational, or commercial purposes.

The only conditions are:
1. Keep the original copyright notice and permission notice in any copy you distribute.
2. The software is provided as-is, without warranty of any kind. If something breaks or doesn't work as expected, the authors and contributors are not liable.
