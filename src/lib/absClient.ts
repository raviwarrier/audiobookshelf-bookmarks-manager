import { AbsActiveSession, AbsUser, Snippet } from '../types';
import { sanitizeStoredUrl } from './authStorage';
import { stripTrailingSlash } from './safeFetch';

export interface AbsAuthResult {
  token: string;
  user: AbsUser;
}

function normalizeServerUrl(url: string): string {
  if (!url || typeof url !== 'string') {
    throw new Error('Server URL is required');
  }
  const clean = sanitizeStoredUrl(url);
  if (!clean) {
    throw new Error('Invalid Audiobookshelf server URL format.');
  }
  return clean;
}

/**
 * Safely format author/authors from Audiobookshelf API responses.
 * ABS returns authors in multiple possible structures:
 * - Array of objects: [{ id: "...", name: "Author Name" }]
 * - Array of strings: ["Author Name"]
 * - Single string: "Author Name"
 * - Single object: { name: "Author Name" }
 * - authorName or displayAuthor strings
 */
function extractAuthorNameFromItem(item: unknown): string {
  if (typeof item === 'string') return item.trim();
  if (item && typeof item === 'object') {
    const obj = item as Record<string, unknown>;
    const n = obj.name ?? obj.author ?? obj.displayName ?? obj.authorName;
    if (typeof n === 'string') return n.trim();
  }
  return '';
}

function extractAuthorsFromArray(authors: unknown[]): string | null {
  const names: string[] = [];
  for (const a of authors) {
    const n = extractAuthorNameFromItem(a);
    if (n) names.push(n);
  }
  return names.length > 0 ? names.join(', ') : null;
}

function extractSingleAuthor(val: unknown): string | null {
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if (trimmed) return trimmed;
  }
  if (val && typeof val === 'object') {
    const n = extractAuthorNameFromItem(val);
    if (n) return n;
  }
  return null;
}

export function formatAuthors(
  authors: unknown,
  fallbackAuthor?: unknown,
  fallbackAuthorName?: unknown,
  displayAuthor?: unknown
): string {
  if (Array.isArray(authors)) {
    const parsed = extractAuthorsFromArray(authors);
    if (parsed) return parsed;
  }

  const primary = extractSingleAuthor(authors);
  if (primary) return primary;

  const fbName = extractSingleAuthor(fallbackAuthorName);
  if (fbName) return fbName;

  const fb = extractSingleAuthor(fallbackAuthor);
  if (fb) return fb;

  const display = extractSingleAuthor(displayAuthor);
  if (display) return display;

  return 'Unknown Author';
}

function sanitizeAbsTargetUrl(url: string): string {
  const sanitized = sanitizeStoredUrl(url);
  if (!sanitized) {
    throw new Error('Security violation: untrusted or invalid target URL');
  }
  const parsed = new URL(sanitized);
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Security violation: only HTTP and HTTPS protocols are allowed');
  }
  const h = parsed.hostname.toLowerCase();
  if (h.startsWith('169.254.') || h === 'metadata.google.internal' || h === 'metadata' || h === 'instance-data') {
    throw new Error('Security violation: metadata endpoints are forbidden');
  }
  return parsed.toString();
}

/**
 * Universal fetch wrapper that routes requests exclusively through the local backend proxy
 * (/api/proxy/abs) to completely eliminate browser CORS limitations and prevent
 * Client-Side Request Forgery (CWE-918).
 */
async function absFetch(
  targetUrl: string,
  options: {
    method?: string;
    headers?: Record<string, string>;
    body?: any;
  } = {},
  _useProxy: boolean = true
): Promise<{ ok: boolean; status: number; data: any }> {
  const safeTargetUrl = sanitizeAbsTargetUrl(targetUrl);

  const res = await fetch('/api/proxy/abs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      targetUrl: safeTargetUrl,
      method: options.method ?? 'GET',
      headers: options.headers ?? {},
      body: options.body,
    }),
  });

  const json = await res.json();
  if (!res.ok) {
    throw new Error(json.message || json.error || `Proxy error (HTTP ${res.status})`);
  }

  let responseData = json.data;
  if (typeof responseData === 'string') {
    const trimmed = responseData.trim();
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        responseData = JSON.parse(trimmed);
      } catch {
        // preserve original string
      }
    }
  }

  return {
    ok: json.ok,
    status: json.status,
    data: responseData,
  };
}

function stripBearerPrefix(token: string): string {
  let t = token.trim();
  if (t.toLowerCase().startsWith('bearer ')) {
    t = t.slice(7).trim();
  }
  let cleaned = '';
  for (const ch of t) {
    if (ch !== '"' && ch !== "'") {
      cleaned += ch;
    }
  }
  return cleaned.trim();
}

function formatAuthErrorMessage(res: { status: number; data: any }, defaultMessage: string): string {
  if (res.status === 401 || res.status === 403) {
    return 'Invalid username or password. Please verify your Audiobookshelf credentials.';
  }
  if (res.status === 502) {
    const detail = res.data?.detail || res.data?.message || (typeof res.data === 'string' ? res.data : '');
    return detail
      ? `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): ${detail}. If Audiobookshelf runs in Docker, ensure ABS_TARGET_SERVER in ecosystem.config.cjs points to your Host LAN IP (e.g. http://192.168.68.102:13378) instead of localhost/127.0.0.1.`
      : `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): The proxy or sidecar could not reach Audiobookshelf. If Audiobookshelf is running in Docker, ensure ABS_TARGET_SERVER points to your Host LAN IP (e.g. http://192.168.68.102:13378) rather than localhost or your public domain.`;
  }
  const errDetail = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
  return `${defaultMessage} (HTTP ${res.status}): ${errDetail}`;
}

async function authenticateWithUserPass(
  cleanUrl: string,
  username?: string,
  password?: string,
  useProxy: boolean = true
): Promise<AbsAuthResult> {
  if (!username || !password) {
    throw new Error('Username and password are required');
  }

  const res = await absFetch(
    `${cleanUrl}/login`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: { username, password },
    },
    useProxy
  );

  if (!res.ok) {
    throw new Error(formatAuthErrorMessage(res, 'Authentication failed'));
  }

  const data = res.data;
  const user = data.user || { id: data.id || 'user_1', username: data.username || username };
  const authToken = data.user?.token || data.token;

  if (!authToken) {
    throw new Error('No authentication token returned by Audiobookshelf server.');
  }

  return {
    token: authToken,
    user: {
      id: String(user.id),
      username: String(user.username),
    },
  };
}

async function verifyTokenEndpoint(
  cleanUrl: string,
  endpoint: string,
  method: string,
  cleanToken: string,
  useProxy: boolean
) {
  return absFetch(
    `${cleanUrl}${endpoint}`,
    {
      method,
      headers: {
        Authorization: `Bearer ${cleanToken}`,
      },
    },
    useProxy
  );
}

function formatTokenErrorMessage(res: { status: number; data: any }, cleanUrl: string): string {
  if (res.status === 401 || res.status === 403) {
    return 'Audiobookshelf rejected your API token (HTTP 401 Unauthorized). Please check your API token in Audiobookshelf (Profile icon → API Token → Copy Token).';
  }
  if (res.status === 404) {
    return `Audiobookshelf server at ${cleanUrl} returned 404 Not Found. Please check that your Server URL is correct.`;
  }
  if (res.status === 502) {
    const detail = res.data?.detail || res.data?.message || (typeof res.data === 'string' ? res.data : '');
    return detail
      ? `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): ${detail}. If Audiobookshelf runs in Docker, ensure ABS_TARGET_SERVER in ecosystem.config.cjs points to your Host LAN IP (e.g. http://192.168.68.102:13378) instead of localhost/127.0.0.1.`
      : `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): The proxy or sidecar could not reach Audiobookshelf. If Audiobookshelf is running in Docker, ensure ABS_TARGET_SERVER points to your Host LAN IP (e.g. http://192.168.68.102:13378) rather than localhost or your public domain.`;
  }
  return `Token verification failed (HTTP ${res.status}). Verify your Audiobookshelf API key.`;
}

async function authenticateWithToken(
  cleanUrl: string,
  token?: string,
  preferredUsername?: string,
  useProxy: boolean = true
): Promise<AbsAuthResult> {
  if (!token) {
    throw new Error('API Token / Key is required');
  }

  const cleanToken = stripBearerPrefix(token);

  let res = await verifyTokenEndpoint(cleanUrl, '/api/authorize', 'GET', cleanToken, useProxy);

  if (!res.ok && res.status !== 401 && res.status !== 403) {
    res = await verifyTokenEndpoint(cleanUrl, '/api/authorize', 'POST', cleanToken, useProxy);
  }

  if (!res.ok && res.status !== 401 && res.status !== 403) {
    res = await verifyTokenEndpoint(cleanUrl, '/api/me', 'GET', cleanToken, useProxy);
  }

  if (!res.ok) {
    throw new Error(formatTokenErrorMessage(res, cleanUrl));
  }

  const data = res.data;
  const user = (data && typeof data === 'object' && data.user) || data || {};
  const resolvedUsername =
    (typeof user?.username === 'string' && user.username.trim()) ||
    (typeof user?.name === 'string' && user.name.trim()) ||
    (typeof data?.username === 'string' && data.username.trim()) ||
    (typeof data?.name === 'string' && data.name.trim()) ||
    (typeof preferredUsername === 'string' && preferredUsername.trim()) ||
    'abs_listener';

  return {
    token: cleanToken,
    user: {
      id: String(user?.id || 'abs_user'),
      username: resolvedUsername,
    },
  };
}

/**
 * Authenticate with Audiobookshelf via Bearer Token or Username/Password.
 */
export async function authenticateAbs(
  serverUrl: string,
  authMode: 'token' | 'userpass',
  token?: string,
  username?: string,
  password?: string,
  useProxy: boolean = true
): Promise<AbsAuthResult> {
  const cleanUrl = normalizeServerUrl(serverUrl);

  if (authMode === 'userpass') {
    return authenticateWithUserPass(cleanUrl, username, password, useProxy);
  }

  return authenticateWithToken(cleanUrl, token, username, useProxy);
}

function findMatchingAudioFile(audioFiles: any[], curTime: number): any {
  if (!audioFiles || audioFiles.length === 0) return null;
  for (const af of audioFiles) {
    const afStart = Number(af.startOffset ?? 0);
    const afDur = Number(af.duration ?? af.metadata?.duration ?? 0);
    if (afDur > 0 && curTime >= afStart && curTime <= (afStart + afDur)) {
      return af;
    }
  }
  return audioFiles[0];
}

function extractFilePathFromFile(file: any, dirPath: string): string | null {
  if (file?.metadata?.path) return file.metadata.path;
  if (file?.path) return file.path;
  const filename = file?.metadata?.filename ?? file?.filename;
  if (dirPath && filename) {
    return `${stripTrailingSlash(dirPath)}/${filename}`;
  }
  if (filename) return filename;
  return null;
}

/**
 * Resolves the actual server file path of the currently playing audio file
 * from Audiobookshelf media metadata, track offset, or item path.
 */
function resolveRealAudioFilePath(media: any, item: any, activeTrack: any, curTime: number): string {
  const audioFiles: any[] = media?.audioFiles ?? media?.tracks ?? item?.media?.audioFiles ?? [];
  const dirPath = media?.path ?? item?.path ?? '';

  const matched = findMatchingAudioFile(audioFiles, curTime);
  if (matched) {
    const p = extractFilePathFromFile(matched, dirPath);
    if (p) return p;
  }

  const trackPath = activeTrack?.metadata?.path ?? activeTrack?.path ?? activeTrack?.metadata?.filename;
  if (trackPath) return trackPath;

  if (media?.path) return media.path;
  if (item?.path) return item.path;

  return 'Audio file path not reported by Audiobookshelf (check server audio mount)';
}

async function fetchLatestMediaProgress(
  cleanUrl: string,
  token: string,
  useProxy: boolean
): Promise<Record<string, any> | null> {
  const meRes = await absFetch(
    `${cleanUrl}/api/me`,
    { method: 'GET', headers: { Authorization: `Bearer ${token}` } },
    useProxy
  );
  if (!meRes.ok || !meRes.data) return null;

  const progressList = meRes.data.user?.mediaProgress ?? meRes.data.mediaProgress ?? [];
  if (!Array.isArray(progressList) || progressList.length === 0) return null;

  progressList.sort((a: any, b: any) => (b.lastUpdate || 0) - (a.lastUpdate || 0));
  return progressList[0];
}

function buildSessionFromMediaItem(item: any, latest: any): AbsActiveSession {
  const media = item.media ?? {};
  const meta = media.metadata ?? {};
  const curTime = Math.floor(latest.currentTime ?? 0);
  const chapters = media.chapters ?? [];
  const curChapter = chapters.find(
    (c: { start: number; end: number }) => curTime >= c.start && curTime <= c.end
  );

  return {
    libraryItemId: item.id ?? latest.libraryItemId,
    episodeId: latest.episodeId ?? null,
    bookTitle: meta.title ?? 'In-Progress Audiobook',
    subtitle: meta.subtitle ?? '',
    author: formatAuthors(meta.authors, meta.author, meta.authorName, 'Unknown Author'),
    chapterName: curChapter?.title ?? curChapter?.name ?? 'Current Chapter',
    currentTime: curTime,
    audioFilePath: resolveRealAudioFilePath(media, item, null, curTime),
    duration: latest.duration ?? media.duration,
    bookmarks: latest.bookmarks ?? media.bookmarks ?? [],
  };
}

async function queryFallbackMediaProgress(
  cleanUrl: string,
  token: string,
  useProxy: boolean
): Promise<AbsActiveSession | null> {
  try {
    const latest = await fetchLatestMediaProgress(cleanUrl, token, useProxy);
    if (!latest) return null;

    const itemRes = await absFetch(
      `${cleanUrl}/api/items/${latest.libraryItemId}?expanded=1`,
      { method: 'GET', headers: { Authorization: `Bearer ${token}` } },
      useProxy
    );
    if (!itemRes.ok || !itemRes.data) return null;

    return buildSessionFromMediaItem(itemRes.data, latest);
  } catch {
    return null;
  }
}

interface MutableSessionMetadata {
  bookTitle: string;
  subtitle: string;
  author: string;
  chapterName: string;
  audioFilePath: string;
  duration: number;
  coverPath?: string;
  bookmarks: any[];
}

function applyItemMetadataProperties(
  mMeta: Record<string, unknown>,
  media: Record<string, unknown>,
  curTime: number,
  meta: MutableSessionMetadata
): void {
  if (typeof mMeta.title === 'string') meta.bookTitle = mMeta.title;
  if (typeof mMeta.subtitle === 'string') meta.subtitle = mMeta.subtitle;
  if (mMeta.authors || mMeta.author || mMeta.authorName) {
    meta.author = formatAuthors(mMeta.authors, mMeta.author, mMeta.authorName, meta.author);
  }
  if (typeof media.duration === 'number') meta.duration = media.duration;
  if (typeof media.coverPath === 'string') meta.coverPath = media.coverPath;
  if (Array.isArray(media.bookmarks) && media.bookmarks.length > 0) {
    meta.bookmarks = media.bookmarks;
  }

  const chapters = Array.isArray(media.chapters) ? media.chapters : [];
  const matched = chapters.find(
    (c: { start: number; end: number }) => curTime >= c.start && curTime <= c.end
  );
  if (matched?.title || matched?.name) {
    meta.chapterName = matched.title ?? matched.name;
  }
}

async function enrichSessionFromItem(
  cleanUrl: string,
  libraryItemId: string,
  token: string,
  useProxy: boolean,
  curTime: number,
  activeAudioTrack: any,
  meta: MutableSessionMetadata
): Promise<void> {
  try {
    const itemRes = await absFetch(
      `${cleanUrl}/api/items/${libraryItemId}?expanded=1`,
      { method: 'GET', headers: { Authorization: `Bearer ${token}` } },
      useProxy
    );
    if (!itemRes.ok || !itemRes.data) return;

    const item = itemRes.data;
    const media = item.media ?? {};
    const mMeta = media.metadata ?? {};

    applyItemMetadataProperties(mMeta, media, curTime, meta);
    meta.audioFilePath = resolveRealAudioFilePath(media, item, activeAudioTrack, curTime);
  } catch {
    // Continue with best available metadata
  }
}

function buildInitialSessionMetadata(active: any): MutableSessionMetadata {
  return {
    bookTitle: active.mediaMetadata?.title ?? active.displayTitle ?? 'Audiobook Title',
    subtitle: active.mediaMetadata?.subtitle ?? '',
    author: formatAuthors(
      active.mediaMetadata?.authors,
      active.mediaMetadata?.author,
      active.mediaMetadata?.authorName,
      active.displayAuthor
    ),
    chapterName: active.currentChapter?.title ?? active.currentChapter?.name ?? 'Current Chapter',
    audioFilePath: '',
    duration: active.duration ?? active.mediaMetadata?.duration,
    coverPath: active.mediaMetadata?.coverPath ?? active.coverPath,
    bookmarks: active.bookmarks ?? active.libraryItem?.media?.bookmarks ?? [],
  };
}

/**
 * Fetch active listening session and bookmarks from Audiobookshelf.
 */
export async function fetchActiveSession(
  serverUrl: string,
  token: string,
  useProxy: boolean = true
): Promise<AbsActiveSession> {
  const cleanUrl = stripTrailingSlash(serverUrl);

  const res = await absFetch(
    `${cleanUrl}/api/me/listening-sessions`,
    {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    },
    useProxy
  );

  if (!res.ok) {
    throw new Error(`Failed to fetch listening sessions (HTTP ${res.status})`);
  }

  const data = res.data;
  const sessions = Array.isArray(data) ? data : data.sessions ?? [];

  if (sessions.length === 0) {
    const fallback = await queryFallbackMediaProgress(cleanUrl, token, useProxy);
    if (fallback) return fallback;
    throw new Error('No active listening sessions found on Audiobookshelf. Start playing an audiobook on Audiobookshelf first.');
  }

  const active = sessions[0];
  const libraryItemId = active.libraryItemId ?? active.id;
  const curTime = Math.floor(active.currentTime ?? 0);

  const meta = buildInitialSessionMetadata(active);

  if (libraryItemId) {
    await enrichSessionFromItem(cleanUrl, libraryItemId, token, useProxy, curTime, active.audioTrack, meta);
  }

  if (!meta.audioFilePath) {
    meta.audioFilePath = resolveRealAudioFilePath(active.media, active.libraryItem, active.audioTrack, curTime);
  }

  return {
    libraryItemId: libraryItemId ?? 'item_default',
    episodeId: active.episodeId ?? null,
    bookTitle: meta.bookTitle,
    subtitle: meta.subtitle,
    author: meta.author,
    chapterName: meta.chapterName,
    currentTime: curTime,
    audioFilePath: meta.audioFilePath,
    duration: meta.duration,
    coverPath: meta.coverPath,
    bookmarks: meta.bookmarks,
  };
}

/**
 * Trigger snippet generation on the FastAPI sidecar.
 */
export async function createSnippet(
  sidecarUrl: string,
  token: string,
  duration: number = 60,
  useProxy: boolean = true,
  serverUrl?: string,
  libraryItemId?: string,
  startTime?: number
): Promise<Snippet> {
  const cleanUrl = stripTrailingSlash(sidecarUrl);

  const res = await absFetch(
    `${cleanUrl}/api/snippet`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        ...(serverUrl ? { 'X-ABS-Server-Url': serverUrl } : {}),
      },
      body: {
        duration,
        ...(serverUrl ? { server_url: serverUrl, serverUrl } : {}),
        ...(libraryItemId ? { library_item_id: libraryItemId, libraryItemId } : {}),
        ...(startTime !== undefined ? { start_time: startTime, startTime } : {}),
      },
    },
    useProxy
  );

  if (!res.ok) {
    const errDetail = typeof res.data === 'string' ? res.data : (res.data?.detail || JSON.stringify(res.data));
    throw new Error(`Sidecar error (HTTP ${res.status}): ${errDetail}`);
  }

  const result = res.data;
  const snip = result.snippet;

  return {
    id: `${snip.book_title}-${snip.timestamp}`,
    bookTitle: snip.book_title,
    author: formatAuthors(snip.author, 'Unknown Author'),
    chapterName: snip.chapter,
    timestamp: snip.timestamp,
    startTime: snip.start_time,
    duration: snip.duration,
    audioUrl: getPlayableAudioUrl(snip.audio_url, sidecarUrl, useProxy),
    transcript: snip.transcript,
    markdownContent: snip.transcript,
    createdAt: Date.now(),
  };
}

function isExternalBrowserHost(): boolean {
  if (typeof window === 'undefined') return false;
  const h = window.location.hostname;
  return h !== 'localhost' && h !== '127.0.0.1';
}

function resolveLocalhostRelPath(urlStr: string): string | null {
  try {
    const parsed = new URL(urlStr);
    if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
      return `${parsed.pathname}${parsed.search}`;
    }
  } catch {}
  return null;
}

/**
 * Resolves an audio URL so that clients accessing externally or through a domain
 * stream audio seamlessly via the dashboard server proxy instead of failing on client localhost.
 */
export function getPlayableAudioUrl(
  rawUrl?: string,
  _targetSidecar?: string,
  _proxyEnabled: boolean = true
): string {
  if (!rawUrl) return '';
  if (rawUrl.startsWith('http://') || rawUrl.startsWith('https://')) {
    if (isExternalBrowserHost()) {
      const rel = resolveLocalhostRelPath(rawUrl);
      if (rel) return rel;
    }
    return rawUrl;
  }
  return rawUrl.startsWith('/') ? rawUrl : `/${rawUrl}`;
}

/**
  * Create a native bookmark directly on the Audiobookshelf server.
  */
export async function createAbsBookmark(
  serverUrl: string,
  token: string,
  libraryItemId: string,
  time: number,
  title: string,
  useProxy: boolean = true
): Promise<{ ok: boolean; data: any }> {
  const cleanUrl = stripTrailingSlash(serverUrl);
  return absFetch(
    `${cleanUrl}/api/me/bookmarks`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: { libraryItemId, time, title },
    },
    useProxy
  );
}

