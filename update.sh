#!/usr/bin/env bash
# ==============================================================================
# Audiobookshelf Bookmarks Manager - Full Install & Update Script
# ==============================================================================
# Ensures all system tools (ffmpeg, python3, etc.), Python packages,
# Node dependencies, and production builds are fully installed and up to date.
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}   Audiobookshelf Bookmarks Manager - Complete Update & Setup   ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. System Package Verification & Installation (ffmpeg, curl, python3)
echo -e "\n${BLUE}[1/4] Checking system dependencies (ffmpeg, python3)...${NC}"

if command -v ffmpeg &>/dev/null; then
    echo -e "   ${GREEN}✓ ffmpeg is installed:${NC} $(command -v ffmpeg)"
else
    echo -e "   ${YELLOW}ffmpeg not found on PATH. Attempting automatic installation...${NC}"
    if command -v apt-get &>/dev/null; then
        echo -e "   Running: sudo apt-get update && sudo apt-get install -y ffmpeg"
        sudo apt-get update && sudo apt-get install -y ffmpeg
    elif command -v brew &>/dev/null; then
        echo -e "   Running: brew install ffmpeg"
        brew install ffmpeg
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm ffmpeg
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y ffmpeg
    else
        echo -e "   ${RED}[!] Unable to auto-install ffmpeg. Please install ffmpeg manually for your OS.${NC}"
        exit 1
    fi
fi

# 2. Python Virtual Environment (venv is default mode) & Dependencies
echo -e "\n${BLUE}[2/4] Setting up Python virtual environment (venv is default)...${NC}"
VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo -e "   Creating Python virtual environment in ${CYAN}$VENV_DIR${NC}..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo -e "   ${YELLOW}python3 -m venv failed. Checking for python3-venv package...${NC}"
        if command -v apt-get &>/dev/null; then
            echo -e "   Running: sudo apt-get update && sudo apt-get install -y python3-venv python3-pip"
            sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
            python3 -m venv "$VENV_DIR"
        else
            echo -e "   ${RED}[!] Could not create venv. Please ensure python3-venv is installed.${NC}"
        fi
    fi
fi

if [ -f "$VENV_DIR/bin/python3" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python3"
    echo -e "   ${GREEN}✓ Using virtual environment:${NC} $PYTHON_BIN"
else
    echo -e "   ${YELLOW}[!] Falling back to system python3.${NC}"
    PYTHON_BIN="python3"
fi

echo -e "   Installing Python packages from requirements.txt into venv..."
$PYTHON_BIN -m pip install --upgrade pip 2>/dev/null || true
$PYTHON_BIN -m pip install -r "$SCRIPT_DIR/requirements.txt"
echo -e "   ${GREEN}✓ Python packages installed successfully in venv.${NC}"

# 3. Node Dependencies & Production Build
echo -e "\n${BLUE}[3/4] Building Web Dashboard & Server Bundle...${NC}"
if [ -f "$SCRIPT_DIR/.env" ]; then
    # Vite warns if NODE_ENV is set in .env; production environment is managed by PM2/runtime
    sed -i '/^NODE_ENV=/d' "$SCRIPT_DIR/.env" 2>/dev/null || true
fi
if command -v npm &>/dev/null; then
    npm install
    npm run build
    echo -e "   ${GREEN}✓ Frontend and server built successfully (dist/server.cjs ready).${NC}"
else
    echo -e "   ${YELLOW}npm not found. Skipping web build step.${NC}"
fi

# Ensure existing installation_date.json is protected and never overwritten
if [ -f "$SCRIPT_DIR/init_installation_date.py" ]; then
    $PYTHON_BIN "$SCRIPT_DIR/init_installation_date.py" 2>/dev/null || true
fi

# 4. PM2 Service Reload (if active)
echo -e "\n${BLUE}[4/4] Checking PM2 service status...${NC}"
if command -v pm2 &>/dev/null; then
    if pm2 list | grep -q "ecosystem.config.cjs\|abs-manager\|abs-extractor"; then
        echo -e "   Restarting PM2 services with updated environment..."
        pm2 restart ecosystem.config.cjs --update-env 2>/dev/null || pm2 reload ecosystem.config.cjs || true
        pm2 save 2>/dev/null || true
        echo -e "   ${GREEN}✓ PM2 services restarted.${NC}"
    else
        echo -e "   PM2 is installed but not actively running this app. Run:${NC}"
        echo -e "   ${YELLOW}pm2 start ecosystem.config.cjs${NC}"
    fi
fi

echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}  Update completed successfully! All dependencies are in place.  ${NC}"
echo -e "${GREEN}================================================================${NC}\n"
