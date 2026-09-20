const fs = require('fs');
const path = require('path');
const dotenv = require('dotenv');

// ==============================================================================
// PM2 Configuration for Audiobookshelf Bookmarks Manager (v2 Zero-Proxy Architecture)
// ==============================================================================
// Architecture Overview:
// - Audiobookshelf Mobile Apps & Web: Connect DIRECTLY to Audiobookshelf.
//   No traffic is proxied through the sidecar, eliminating socket drops, lag, and gateway latency.
// - abs-manager-sidecar (Port 13380): Runs alongside ABS as a passive, non-blocking service.
//   Listens to Audiobookshelf events and performs audio clipping & speech-to-text with low CPU priority.
// - abs-manager-web (Port 13379): Modern web UI for browsing, playing, and managing bookmarks & snippets.
// ==============================================================================

// Detect or fallback to app directory
const CANDIDATE_APP_DIRS = [
  '/srv/ssd/Appdata/local/audiobookshelf-bookmarks-manager',
  '/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Manager',
  '/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Extractor',
  process.cwd()
];
const APP_DIR = CANDIDATE_APP_DIRS.find(dir => fs.existsSync(dir)) || process.cwd();

// Load .env configuration from APP_DIR or current directory
const envPath = path.join(APP_DIR, '.env');
let envConfig = {};
if (fs.existsSync(envPath)) {
  try {
    envConfig = dotenv.parse(fs.readFileSync(envPath));
  } catch (err) {
    console.warn('[PM2] Warning: Could not parse .env:', err);
  }
}

const DEFAULT_VENV_PYTHON = path.join(APP_DIR, 'venv', 'bin', 'python3');
const PYTHON_PATH = fs.existsSync(DEFAULT_VENV_PYTHON) ? DEFAULT_VENV_PYTHON : `${APP_DIR}/venv/bin/python3`;

// Derive target ABS server and sidecar URLs dynamically from .env or fallback
const ABS_TARGET = (envConfig.ABS_TARGET_SERVER || envConfig.ABS_SERVER_URL || process.env.ABS_TARGET_SERVER || 'http://localhost:13378').trim();
const DEFAULT_ABS_URL = (envConfig.DEFAULT_ABS_URL || (ABS_TARGET && !ABS_TARGET.includes('abs.example.com') ? ABS_TARGET : 'http://localhost:13378')).trim();
const SIDECAR_URL_CONFIG = (envConfig.SIDECAR_URL || process.env.SIDECAR_URL || 'http://localhost:13380').trim();
const VOLUME_DIR = envConfig.VOLUME_DIR || envConfig.SNIPPETS_DIR || path.join(APP_DIR, 'bookmarks');
const AUDIOBOOKS_PATH = envConfig.AUDIOBOOKS_PATH || '/srv/ssd/Bookshelf/Audiobooks';
const PATH_MAPPINGS = envConfig.PATH_MAPPINGS || '/audiobooks:/srv/ssd/Bookshelf/Audiobooks,/summaries:/srv/ssd/Bookshelf/Summaries';

module.exports = {
  apps: [
    // 1. Web Dashboard & Server-Side Proxy
    {
      name: 'abs-manager-web',
      cwd: APP_DIR,
      script: `${APP_DIR}/dist/server.cjs`,
      exec_mode: 'fork',
      autorestart: true,
      env: {
        NODE_ENV: 'production',
        HOST: '0.0.0.0',
        PORT: Number(envConfig.PORT || process.env.PORT) || 13379,
        SIDECAR_URL: SIDECAR_URL_CONFIG,
        USE_BACKEND_PROXY: envConfig.USE_BACKEND_PROXY || 'true',
        DEFAULT_ABS_URL: DEFAULT_ABS_URL,
        ABS_TARGET_SERVER: ABS_TARGET,
        VOLUME_DIR: VOLUME_DIR
      }
    },
    // 2. Python Audio Slicing & Transcription Sidecar
    {
      name: 'abs-manager-sidecar',
      cwd: APP_DIR,
      script: `${APP_DIR}/main.py`,
      interpreter: PYTHON_PATH,
      exec_mode: 'fork',
      autorestart: true,
      env: {
        HOST: '0.0.0.0',
        PORT: Number(envConfig.SIDECAR_PORT || process.env.SIDECAR_PORT) || 13380,
        SIDECAR_PORT: Number(envConfig.SIDECAR_PORT || process.env.SIDECAR_PORT) || 13380,
        ABS_TARGET_SERVER: ABS_TARGET,
        ABS_SERVER_URL: ABS_TARGET,
        DEFAULT_ABS_URL: DEFAULT_ABS_URL,
        INTERCEPT_SNIPPET_DURATION: Number(envConfig.INTERCEPT_SNIPPET_DURATION) || 60,
        INTERCEPT_PRE_ROLL: Number(envConfig.INTERCEPT_PRE_ROLL) || 30,
        VOLUME_DIR: VOLUME_DIR,
        SNIPPETS_DIR: VOLUME_DIR,
        AUDIOBOOKS_PATH: AUDIOBOOKS_PATH,
        PATH_MAPPINGS: PATH_MAPPINGS,
        BOOKMARK_SYNC_INTERVAL: Number(envConfig.BOOKMARK_SYNC_INTERVAL) || 120,
        PREWARM_WHISPER: envConfig.PREWARM_WHISPER || 'false',
        WHISPER_THREADS: envConfig.WHISPER_THREADS || '2',
        OMP_NUM_THREADS: envConfig.OMP_NUM_THREADS || '2',
        OPENBLAS_NUM_THREADS: envConfig.OPENBLAS_NUM_THREADS || '2'
      }
    }
  ]
};
