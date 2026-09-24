#!/usr/bin/env bash
# ==============================================================================
# Audiobookshelf Bookmarks Manager - Interactive Setup Wizard
# Run anytime: ./setup.sh or npm run setup
# ==============================================================================

set -e

# ANSI Color codes for clean CLI feedback
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "\n${BOLD}${CYAN}=================================================================${NC}"
echo -e "${BOLD}${CYAN}   Audiobookshelf Bookmarks Manager - Server Configuration${NC}"
echo -e "${BOLD}${CYAN}=================================================================${NC}\n"
echo -e "This wizard configures your environment variables for local installation,"
echo -e "Docker Compose, or PM2 on your host server.\n"

# 1. Check existing .env file
ENV_FILE="$SCRIPT_DIR/.env"
EXISTING_ABS_SERVER="http://localhost:13378"
EXISTING_VOLUME_DIR="$SCRIPT_DIR/bookmarks"
EXISTING_AUDIOBOOKS_PATH="/path/to/your/audiobooks"
EXISTING_SIDECAR_PORT="13380"
EXISTING_WEB_PORT="13379"
EXISTING_WHISPER_MODEL="base.en"
EXISTING_DURATION="60"
EXISTING_PRE_ROLL="30"
EXISTING_INTERCEPT_DURATION="60"
EXISTING_INTERCEPT_PRE_ROLL="30"

if [ -f "$ENV_FILE" ]; then
    echo -e "${BLUE}[i] Found existing .env file. Loading current values as defaults...${NC}\n"
    # Safely source existing variables without executing arbitrary code
    while IFS='=' read -r key value || [ -n "$key" ]; do
        key=$(echo "$key" | tr -d '[:space:]')
        # Remove comments and empty lines
        [[ "$key" =~ ^#.* ]] && continue
        [ -z "$key" ] && continue
        # Strip surrounding quotes
        val=$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
        case "$key" in
            ABS_TARGET_SERVER|ABS_SERVER_URL|DEFAULT_ABS_URL) EXISTING_ABS_SERVER="$val" ;;
            VOLUME_DIR|SNIPPETS_DIR) EXISTING_VOLUME_DIR="$val" ;;
            AUDIOBOOKS_PATH) EXISTING_AUDIOBOOKS_PATH="$val" ;;
            SIDECAR_PORT) EXISTING_SIDECAR_PORT="$val" ;;
            PORT) EXISTING_WEB_PORT="$val" ;;
            WHISPER_MODEL) EXISTING_WHISPER_MODEL="$val" ;;
            SNIPPET_DURATION) EXISTING_DURATION="$val" ;;
            SNIPPET_PRE_ROLL) EXISTING_PRE_ROLL="$val" ;;
            INTERCEPT_SNIPPET_DURATION|INTERCEPT_DURATION) EXISTING_INTERCEPT_DURATION="$val" ;;
            INTERCEPT_PRE_ROLL) EXISTING_INTERCEPT_PRE_ROLL="$val" ;;
        esac
    done < "$ENV_FILE"
fi

if [ -n "$EXISTING_ABS_SERVER" ] && [[ "$EXISTING_ABS_SERVER" == *"abs.example.com"* ]]; then
    EXISTING_ABS_SERVER="http://localhost:13378"
fi

# 2. Audiobookshelf Target Server URL
echo -e "${BOLD}1. Audiobookshelf Target Server URL${NC}"
echo -e "   This is the URL where your existing Audiobookshelf server is running."
echo -e "   ${YELLOW}Note: 13378 is Audiobookshelf's default port. The manager connects to it, but does not listen on it.${NC}"
read -rp "   Target ABS URL [${EXISTING_ABS_SERVER:-http://localhost:13378}]: " INPUT_ABS_SERVER
ABS_TARGET_SERVER="${INPUT_ABS_SERVER:-${EXISTING_ABS_SERVER:-http://localhost:13378}}"
ABS_TARGET_SERVER="${ABS_TARGET_SERVER%/}"
if [[ "$ABS_TARGET_SERVER" != http://* && "$ABS_TARGET_SERVER" != https://* ]]; then
    if [[ "$ABS_TARGET_SERVER" == localhost* || "$ABS_TARGET_SERVER" == 127.0.0.1* || "$ABS_TARGET_SERVER" == 192.168.* || "$ABS_TARGET_SERVER" == 10.* ]]; then
        ABS_TARGET_SERVER="http://$ABS_TARGET_SERVER"
    else
        ABS_TARGET_SERVER="https://$ABS_TARGET_SERVER"
    fi
fi

# 3. Output Bookmarks & Volume Directory
echo -e "\n${BOLD}2. Output Directory for Bookmarks & Transcripts (VOLUME_DIR)${NC}"
echo -e "   Where sliced audio (.mp3), transcripts (.md), and metadata (.json) will be stored."
echo -e "   Recommended: an absolute path on your SSD or storage array."
read -rp "   Directory path [$EXISTING_VOLUME_DIR]: " INPUT_VOLUME_DIR
VOLUME_DIR="${INPUT_VOLUME_DIR:-$EXISTING_VOLUME_DIR}"

# Create directory if it doesn't exist
if [ ! -d "$VOLUME_DIR" ]; then
    echo -e "   Directory '${VOLUME_DIR}' does not exist. Creating..."
    mkdir -p "$VOLUME_DIR" || {
        echo -e "   ${RED}Warning: Could not create ${VOLUME_DIR}. You may need sudo permissions.${NC}"
    }
fi

# 4. Audiobooks Library Path (For Docker mount)
echo -e "\n${BOLD}3. Audiobooks Media Library Directory (Host Path)${NC}"
echo -e "   The path where your audiobooks (.m4b, .mp3) are stored on the host server."
echo -e "   (Used by Docker to mount the audio files as read-only)."
read -rp "   Audiobooks host path [$EXISTING_AUDIOBOOKS_PATH]: " INPUT_AUDIOBOOKS_PATH
AUDIOBOOKS_PATH="${INPUT_AUDIOBOOKS_PATH:-$EXISTING_AUDIOBOOKS_PATH}"

# 5. Ports Configuration
echo -e "\n${BOLD}4. Network Ports Configuration${NC}"
echo -e "   ${CYAN}Reserved ports reference:${NC}"
echo -e "   - 13378: Used exclusively by Audiobookshelf (Upstream server)."
echo -e "   - 13380 (or 13377): Recommended for Python Sidecar & Interceptor Proxy."
echo -e "   - 13379 (or 13376): Recommended for Web Dashboard & UI."
echo -e "   ${YELLOW}(Default ports 3000 and 8080 are NOT used on your production server to avoid conflicts).${NC}\n"

# Sidecar Port Prompt with validation
while true; do
    read -rp "   Python Sidecar & Interceptor Port [$EXISTING_SIDECAR_PORT]: " INPUT_SIDECAR_PORT
    SIDECAR_PORT="${INPUT_SIDECAR_PORT:-$EXISTING_SIDECAR_PORT}"
    if [ "$SIDECAR_PORT" = "13378" ]; then
        echo -e "   ${RED}[!] Port 13378 is reserved for Audiobookshelf. Please choose a different port (e.g. 13380 or 13377).${NC}"
    else
        break
    fi
done

# Web Dashboard Port Prompt with validation
while true; do
    read -rp "   Web Dashboard & UI Port [$EXISTING_WEB_PORT]: " INPUT_WEB_PORT
    WEB_PORT="${INPUT_WEB_PORT:-$EXISTING_WEB_PORT}"
    if [ "$WEB_PORT" = "13378" ]; then
        echo -e "   ${RED}[!] Port 13378 is reserved for Audiobookshelf. Please choose a different port (e.g. 13379 or 13376).${NC}"
    elif [ "$WEB_PORT" = "$SIDECAR_PORT" ]; then
        echo -e "   ${RED}[!] Web port cannot be the same as Sidecar port ($SIDECAR_PORT). Please choose a unique port.${NC}"
    else
        break
    fi
done

# 6. Whisper Transcription Model
echo -e "\n${BOLD}5. Speech-to-Text Model Selection${NC}"
echo -e "   1) base.en   (Recommended: ~140MB, accurate & very fast on CPU/Raspberry Pi)"
echo -e "   2) tiny.en   (Fastest: ~75MB, minimal RAM usage)"
echo -e "   3) small.en  (High Accuracy: ~460MB, requires more CPU/RAM)"
echo -e "   4) medium.en (Highest Accuracy: ~1.5GB, requires powerful CPU or GPU)"
read -rp "   Select model [1-4, default for $EXISTING_WHISPER_MODEL]: " MODEL_CHOICE

case "$MODEL_CHOICE" in
    1) WHISPER_MODEL="base.en" ;;
    2) WHISPER_MODEL="tiny.en" ;;
    3) WHISPER_MODEL="small.en" ;;
    4) WHISPER_MODEL="medium.en" ;;
    *) WHISPER_MODEL="$EXISTING_WHISPER_MODEL" ;;
esac

# 7. Write to .env
echo -e "\n${BLUE}[*] Writing configuration to $ENV_FILE...${NC}"

cat > "$ENV_FILE" <<EOF
# Audiobookshelf Bookmarks Manager - Configuration
# Generated by ./setup.sh on $(date)

# Upstream Audiobookshelf Server (The sidecar connects to this; does NOT listen on 13378)
ABS_TARGET_SERVER="$ABS_TARGET_SERVER"
ABS_SERVER_URL="$ABS_TARGET_SERVER"
DEFAULT_ABS_URL="$ABS_TARGET_SERVER"

# Storage paths
VOLUME_DIR="$VOLUME_DIR"
SNIPPETS_DIR="$VOLUME_DIR"
AUDIOBOOKS_PATH="$AUDIOBOOKS_PATH"
PATH_MAPPINGS="/audiobooks:$AUDIOBOOKS_PATH,/summaries:${AUDIOBOOKS_PATH%/Audiobooks}/Summaries"

# Network Ports
# Web Dashboard & UI Port (Default 13379 or custom e.g. 13376)
PORT=$WEB_PORT

# Python Sidecar & Interceptor Proxy Port (Default 13380 or custom e.g. 13377)
SIDECAR_PORT=$SIDECAR_PORT

# Audio & Transcription Settings
SNIPPET_DURATION=$EXISTING_DURATION
SNIPPET_PRE_ROLL=$EXISTING_PRE_ROLL
INTERCEPT_SNIPPET_DURATION=$EXISTING_INTERCEPT_DURATION
INTERCEPT_PRE_ROLL=$EXISTING_INTERCEPT_PRE_ROLL
WHISPER_MODEL=$WHISPER_MODEL
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
VOSK_MODEL_NAME=vosk-model-small-en-us-0.15

# Runtime Flags
RELOAD=false
EOF

chmod 600 "$ENV_FILE"

# 8. Verify .gitignore protection
echo -e "${BLUE}[*] Verifying Git privacy protection...${NC}"
GITIGNORE_FILE="$SCRIPT_DIR/.gitignore"
if [ -f "$GITIGNORE_FILE" ]; then
    for ig in ".env" ".env.*" "venv/" "installation_date.json" ".installation_date.json" ".deleted_tombstones.json"; do
        if ! grep -q "^$ig$" "$GITIGNORE_FILE"; then
            echo "$ig" >> "$GITIGNORE_FILE"
        fi
    done
    echo -e "   ${GREEN}[OK] .env, venv, and dynamic installation_date.json are strictly ignored in .gitignore.${NC}"
fi

# 9. Automatic Virtual Environment (venv is default mode) & Package Setup
echo -e "\n${BLUE}[*] Initializing Python Virtual Environment (Default Mode)...${NC}"
VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo -e "   Creating virtual environment at ${CYAN}$VENV_DIR${NC}..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo -e "   ${YELLOW}python3 -m venv failed. Attempting to install python3-venv via apt...${NC}"
        if command -v apt-get &>/dev/null; then
            sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
            python3 -m venv "$VENV_DIR"
        fi
    fi
fi

if [ -f "$VENV_DIR/bin/python3" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python3"
    echo -e "   ${GREEN}✓ Virtualenv ready:${NC} $VENV_PYTHON"
    echo -e "   Installing Python packages from requirements.txt..."
    $VENV_PYTHON -m pip install --upgrade pip 2>/dev/null || true
    $VENV_PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt"
    echo -e "   ${GREEN}✓ Python packages installed successfully in venv.${NC}"
else
    echo -e "   ${YELLOW}[!] Warning: Could not create venv. Falling back to system python3.${NC}"
    VENV_PYTHON="python3"
fi

# 10. Node Dependencies & Build
if command -v npm &>/dev/null; then
    echo -e "\n${BLUE}[*] Installing Node dependencies and building web dashboard...${NC}"
    npm install
    npm run build
    echo -e "   ${GREEN}✓ Web dashboard and server compiled to dist/server.cjs.${NC}"
fi

# 11. Record dynamic installation date & cutoff (first install only, before app start)
echo -e "\n${BLUE}[*] Verifying installation date cutoff configuration...${NC}"
if [ -f "$SCRIPT_DIR/init_installation_date.py" ]; then
    VOLUME_DIR="$VOLUME_DIR" $VENV_PYTHON "$SCRIPT_DIR/init_installation_date.py" || true
fi

# 12. Completion Summary
echo -e "\n${BOLD}${GREEN}=================================================================${NC}"
echo -e "${BOLD}${GREEN}   Configuration & Installation Complete!${NC}"
echo -e "${BOLD}${GREEN}=================================================================${NC}\n"
echo -e "Your configuration has been saved to: ${BOLD}$ENV_FILE${NC}"
echo -e "  - Target ABS Server:   ${CYAN}$ABS_TARGET_SERVER${NC}"
echo -e "  - Bookmarks Directory: ${CYAN}$VOLUME_DIR${NC}"
echo -e "  - Audiobooks Path:     ${CYAN}$AUDIOBOOKS_PATH${NC}"
echo -e "  - Python Venv (Abs):   ${CYAN}$VENV_PYTHON${NC}"
echo -e "  - Web Dashboard Port:  ${GREEN}$WEB_PORT${NC}"
echo -e "  - Sidecar Proxy Port:  ${GREEN}$SIDECAR_PORT${NC}"
echo -e "  - Whisper Model:       ${CYAN}$WHISPER_MODEL${NC}\n"

if command -v pm2 &>/dev/null && pm2 list | grep -q "abs-manager"; then
    echo -e "${BLUE}[*] Restarting active PM2 processes with updated environment...${NC}"
    pm2 restart ecosystem.config.cjs --update-env 2>/dev/null || true
    pm2 save 2>/dev/null || true
    echo -e "   ${GREEN}✓ PM2 services updated and running.${NC}\n"
else
    echo -e "${BOLD}To start the services with PM2:${NC}"
    echo -e "  ${GREEN}pm2 start ecosystem.config.cjs${NC}"
    echo -e "  ${GREEN}pm2 save${NC}\n"
fi
echo -e "To update everything in the future with one command, run ${BOLD}./update.sh${NC} (or ${BOLD}npm run update${NC}).\n"
