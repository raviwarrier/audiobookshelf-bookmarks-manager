FROM python:3.11-slim

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies: ffmpeg (audio slicing/transcoding), curl, and wget
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download transcription models into image cache so server admin does not need to install anything manually
# 1. Primary engine: faster-whisper base.en model
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

# 2. Backup engine: Vosk lightweight speech model
RUN python3 -c "from vosk import Model; Model(model_name='vosk-model-small-en-us-0.15')"

# Copy application source, initialization scripts, and dashboard templates
COPY main.py .
COPY init_installation_date.py .
COPY templates/ ./templates/

# Expose ports: 13380 (sidecar & proxy default) and 13379 (web dashboard)
EXPOSE 13380
EXPOSE 13379

# Default Environment Variables
ENV ABS_TARGET_SERVER="http://audiobookshelf:80"
ENV ABS_SERVER_URL="http://audiobookshelf:80"
ENV VOLUME_DIR="/data"
ENV WHISPER_MODEL="base.en"
ENV WHISPER_DEVICE="cpu"
ENV WHISPER_COMPUTE_TYPE="int8"
ENV VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"
ENV PORT=13380

# Execute installation date initializer before starting uvicorn
# (Preserves existing date in /data/installation_date.json across container updates)
CMD ["sh", "-c", "python3 init_installation_date.py || true; exec uvicorn main:app --host 0.0.0.0 --port 13380"]
