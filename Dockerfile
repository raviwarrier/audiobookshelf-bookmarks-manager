FROM python:3.11-slim

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Install system dependencies (sorted alphanumerically) and create non-root app user (docker:S7018, docker:S7031)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app /data /app/.cache \
    && chown -R appuser:appuser /app /data

WORKDIR /app

# Install Python requirements with locked versions and pre-download transcription models (docker:S8541, docker:S8544)
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt \
    && python3 -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')" \
    && python3 -c "from vosk import Model; Model(model_name='vosk-model-small-en-us-0.15')" \
    && chown -R appuser:appuser /app /data

# Copy application source, initialization scripts, and dashboard templates
COPY --chown=appuser:appuser main.py init_installation_date.py ./
COPY --chown=appuser:appuser templates/ ./templates/

# Switch to non-root user (docker:S6471)
USER appuser

# Expose ports: 13380 (sidecar & proxy default) and 13379 (web dashboard)
EXPOSE 13380
EXPOSE 13379

# Default Environment Variables
ENV ABS_TARGET_SERVER="http://audiobookshelf:80" \
    ABS_SERVER_URL="http://audiobookshelf:80" \
    VOLUME_DIR="/data" \
    WHISPER_MODEL="base.en" \
    WHISPER_DEVICE="cpu" \
    WHISPER_COMPUTE_TYPE="int8" \
    VOSK_MODEL_NAME="vosk-model-small-en-us-0.15" \
    PORT=13380

# Execute installation date initializer before starting uvicorn
# (Preserves existing date in /data/installation_date.json across container updates)
CMD ["sh", "-c", "python3 init_installation_date.py || true; exec uvicorn main:app --host 0.0.0.0 --port 13380"]
