import { AbsActiveSession, AbsUser, Snippet } from '../types';

export interface AbsAuthResult {
  token: string;
  user: AbsUser;
}

function normalizeServerUrl(url: string): string {
  let clean = url.trim().replace(/\/+$/, '');
  if (!clean.startsWith('http://') && !clean.startsWith('https://')) {
    clean = `https://${clean}`;
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
export function formatAuthors(
  authors: unknown,
  fallbackAuthor?: unknown,
  fallbackAuthorName?: unknown,
  displayAuthor?: unknown
): string {
  if (Array.isArray(authors) && authors.length > 0) {
    const names = authors
      .map((a: unknown) => {
        if (typeof a === 'string') return a.trim();
        if (a && typeof a === 'object' && a !== null) {
          const obj = a as Record<string, unknown>;
          const n = obj.name || obj.author || obj.displayName || obj.authorName;
          if (typeof n === 'string') return n.trim();
        }
        return '';
      })
      .filter(Boolean);
    if (names.length > 0) {
      return names.join(', ');
    }
  }

  if (typeof authors === 'string' && authors.trim()) {
    return authors.trim();
  }

  if (authors && typeof authors === 'object' && authors !== null) {
    const obj = authors as Record<string, unknown>;
    const n = obj.name || obj.author || obj.displayName || obj.authorName;
    if (typeof n === 'string' && n.trim()) return n.trim();
  }

  if (typeof fallbackAuthorName === 'string' && fallbackAuthorName.trim()) {
    return fallbackAuthorName.trim();
  }
  if (typeof fallbackAuthor === 'string' && fallbackAuthor.trim()) {
    return fallbackAuthor.trim();
  }
  if (fallbackAuthor && typeof fallbackAuthor === 'object' && fallbackAuthor !== null) {
    const obj = fallbackAuthor as Record<string, unknown>;
    const n = obj.name || obj.author || obj.displayName;
    if (typeof n === 'string' && n.trim()) return n.trim();
  }
  if (typeof displayAuthor === 'string' && displayAuthor.trim()) {
    return displayAuthor.trim();
  }

  return 'Unknown Author';
}

/**
 * Universal fetch wrapper that can route requests through the local backend proxy
 * (/api/proxy/abs) to completely bypass browser CORS limitations, or direct fetch.
 */
async function absFetch(
  targetUrl: string,
  options: {
    method?: string;
    headers?: Record<string, string>;
    body?: any;
  } = {},
  useProxy: boolean = true
): Promise<{ ok: boolean; status: number; data: any }> {
  // If target is localhost/127.0.0.1 and app is hosted on a cloud domain,
  // the cloud proxy cannot reach the user's local machine; use direct browser fetch instead.
  const isTargetLocal = targetUrl.includes('localhost') || targetUrl.includes('127.0.0.1') || targetUrl.includes('0.0.0.0');
  const isHosted = typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1';
  const effectiveUseProxy = useProxy && !(isTargetLocal && isHosted);

  if (effectiveUseProxy) {
    try {
      const res = await fetch('/api/proxy/abs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          targetUrl,
          method: options.method || 'GET',
          headers: options.headers || {},
          body: options.body,
        }),
      });

      const json = await res.json();
      if (!res.ok) {
        throw new Error(json.message || json.error || `Proxy error (HTTP ${res.status})`);
      }

      return {
        ok: json.ok,
        status: json.status,
        data: json.data,
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Backend proxy request failed';

      // Resilient fallback: If the backend proxy fails, attempt direct browser fetch
      // in case the target server allows CORS natively (e.g. Cloudflare / reverse proxy).
      if (!isTargetLocal) {
        try {
          const directRes = await fetch(targetUrl, {
            method: options.method || 'GET',
            headers: options.headers,
            body: options.body ? JSON.stringify(options.body) : undefined,
          });

          const isJson = directRes.headers.get('content-type')?.includes('application/json');
          const data = isJson ? await directRes.json() : await directRes.text();

          return {
            ok: directRes.ok,
            status: directRes.status,
            data,
          };
        } catch {
          // Fallback also failed; throw original proxy error
        }
      }

      throw new Error(`Proxy error contacting ${targetUrl}: ${msg}`);
    }
  }

  // Direct fetch (subject to browser CORS)
  try {
    const res = await fetch(targetUrl, {
      method: options.method || 'GET',
      headers: options.headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });

    const isJson = res.headers.get('content-type')?.includes('application/json');
    const data = isJson ? await res.json() : await res.text();

    return {
      ok: res.ok,
      status: res.status,
      data,
    };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : 'Network error';
    if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
      throw new Error(
        `Browser CORS restriction: Cannot reach ${targetUrl}. Enable 'Server Proxy' or configure CORS on your reverse proxy.`
      );
    }
    throw err;
  }
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
      if (res.status === 401 || res.status === 403) {
        throw new Error('Invalid username or password. Please verify your Audiobookshelf credentials.');
      }
      if (res.status === 502) {
        const detail = res.data?.detail || res.data?.message || (typeof res.data === 'string' ? res.data : '');
        throw new Error(
          detail
            ? `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): ${detail}. If Audiobookshelf runs in Docker, ensure ABS_TARGET_SERVER in ecosystem.config.cjs points to your Host LAN IP (e.g. http://192.168.68.102:13378) instead of localhost/127.0.0.1.`
            : `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): The proxy or sidecar could not reach Audiobookshelf. If Audiobookshelf is running in Docker, ensure ABS_TARGET_SERVER points to your Host LAN IP (e.g. http://192.168.68.102:13378) rather than localhost or your public domain.`
        );
      }
      const errDetail = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
      throw new Error(`Authentication failed (HTTP ${res.status}): ${errDetail}`);
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
  } else {
    if (!token) {
      throw new Error('API Token / Key is required');
    }

    const cleanToken = token.trim().replace(/^bearer\s+/i, '').replace(/["']/g, '').trim();

    // 1. Try POST /api/authorize (Audiobookshelf's official endpoint for Bearer tokens)
    let res = await absFetch(
      `${cleanUrl}/api/authorize`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${cleanToken}`,
        },
      },
      useProxy
    );

    // 2. If 404 or 405, fallback to GET /api/authorize
    if (!res.ok && res.status !== 401 && res.status !== 403) {
      res = await absFetch(
        `${cleanUrl}/api/authorize`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${cleanToken}`,
          },
        },
        useProxy
      );
    }

    // 3. Fallback to GET /api/me if authorize is not found
    if (!res.ok && res.status !== 401 && res.status !== 403) {
      res = await absFetch(
        `${cleanUrl}/api/me`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${cleanToken}`,
          },
        },
        useProxy
      );
    }

    if (!res.ok) {
      if (res.status === 401 || res.status === 403) {
        throw new Error('Audiobookshelf rejected your API token (HTTP 401 Unauthorized). Please check your API token in Audiobookshelf (Profile icon → API Token → Copy Token).');
      }
      if (res.status === 404) {
        throw new Error(`Audiobookshelf server at ${cleanUrl} returned 404 Not Found. Please check that your Server URL is correct.`);
      }
      if (res.status === 502) {
        const detail = res.data?.detail || res.data?.message || (typeof res.data === 'string' ? res.data : '');
        throw new Error(
          detail
            ? `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): ${detail}. If Audiobookshelf runs in Docker, ensure ABS_TARGET_SERVER in ecosystem.config.cjs points to your Host LAN IP (e.g. http://192.168.68.102:13378) instead of localhost/127.0.0.1.`
            : `Connection to Audiobookshelf failed (HTTP 502 Bad Gateway): The proxy or sidecar could not reach Audiobookshelf. If Audiobookshelf is running in Docker, ensure ABS_TARGET_SERVER points to your Host LAN IP (e.g. http://192.168.68.102:13378) rather than localhost or your public domain.`
        );
      }
      throw new Error(`Token verification failed (HTTP ${res.status}). Verify your Audiobookshelf API key.`);
    }

    const data = res.data;
    const user = data.user || data;

    return {
      token: cleanToken,
      user: {
        id: String(user.id || 'abs_user'),
        username: String(user.username || 'abs_listener'),
      },
    };
  }
}

/**
 * Resolves the actual server file path of the currently playing audio file
 * from Audiobookshelf media metadata, track offset, or item path.
 */
function resolveRealAudioFilePath(media: any, item: any, activeTrack: any, curTime: number): string {
  const audioFiles: any[] = media?.audioFiles || media?.tracks || item?.media?.audioFiles || [];

  if (audioFiles.length > 0) {
    let selectedFile = audioFiles[0];
    for (const af of audioFiles) {
      const afStart = Number(af.startOffset ?? 0);
      const afDur = Number(af.duration ?? af.metadata?.duration ?? 0);
      if (afDur > 0 && curTime >= afStart && curTime <= (afStart + afDur)) {
        selectedFile = af;
        break;
      }
    }

    if (selectedFile?.metadata?.path) return selectedFile.metadata.path;
    if (selectedFile?.path) return selectedFile.path;

    const dirPath = media?.path || item?.path || '';
    const filename = selectedFile?.metadata?.filename || selectedFile?.filename;
    if (dirPath && filename) {
      return `${dirPath.replace(/\/+$/, '')}/${filename}`;
    }
    if (filename) return filename;
  }

  if (activeTrack?.metadata?.path) return activeTrack.metadata.path;
  if (activeTrack?.path) return activeTrack.path;
  if (activeTrack?.metadata?.filename) return activeTrack.metadata.filename;

  if (media?.path) return media.path;
  if (item?.path) return item.path;

  return 'Audio file path not reported by Audiobookshelf (check server audio mount)';
}

/**
 * Fetch active listening session and bookmarks from Audiobookshelf.
 */
export async function fetchActiveSession(
  serverUrl: string,
  token: string,
  useProxy: boolean = true
): Promise<AbsActiveSession> {
  const cleanUrl = serverUrl.replace(/\/+$/, '');

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
  const sessions = Array.isArray(data) ? data : data.sessions || [];

  if (sessions.length === 0) {
    // Fallback: check /api/me for mediaProgress if listening-sessions is empty
    try {
      const meRes = await absFetch(
        `${cleanUrl}/api/me`,
        {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}` },
        },
        useProxy
      );
      if (meRes.ok && meRes.data) {
        const progressList = meRes.data.user?.mediaProgress || meRes.data.mediaProgress || [];
        if (progressList.length > 0) {
          progressList.sort((a: any, b: any) => (b.lastUpdate || 0) - (a.lastUpdate || 0));
          const latest = progressList[0];
          const itemRes = await absFetch(
            `${cleanUrl}/api/items/${latest.libraryItemId}?expanded=1`,
            {
              method: 'GET',
              headers: { Authorization: `Bearer ${token}` },
            },
            useProxy
          );
          if (itemRes.ok && itemRes.data) {
            const item = itemRes.data;
            const media = item.media || {};
            const meta = media.metadata || {};
            const curTime = Math.floor(latest.currentTime || 0);
            const chapters = media.chapters || [];
            const curChapter = chapters.find(
              (c: { start: number; end: number }) => curTime >= c.start && curTime <= c.end
            );
            return {
              libraryItemId: item.id || latest.libraryItemId,
              episodeId: latest.episodeId || null,
              bookTitle: meta.title || 'In-Progress Audiobook',
              subtitle: meta.subtitle || '',
              author: formatAuthors(meta.authors, meta.author, meta.authorName, 'Unknown Author'),
              chapterName: curChapter?.title || curChapter?.name || 'Current Chapter',
              currentTime: curTime,
              audioFilePath: resolveRealAudioFilePath(media, item, null, curTime),
              duration: latest.duration || media.duration,
              bookmarks: latest.bookmarks || media.bookmarks || [],
            };
          }
        }
      }
    } catch {
      // Fallback attempt failed, will throw original descriptive error below
    }

    throw new Error('No active listening sessions found on Audiobookshelf. Start playing an audiobook on Audiobookshelf first.');
  }

  const active = sessions[0];
  const libraryItemId = active.libraryItemId || active.id;
  const curTime = Math.floor(active.currentTime || 0);

  let bookTitle = active.mediaMetadata?.title || active.displayTitle || 'Audiobook Title';
  let subtitle = active.mediaMetadata?.subtitle || '';
  let author = formatAuthors(
    active.mediaMetadata?.authors,
    active.mediaMetadata?.author,
    active.mediaMetadata?.authorName,
    active.displayAuthor
  );
  let chapterName = active.currentChapter?.title || active.currentChapter?.name || 'Current Chapter';
  let audioFilePath = '';
  let duration = active.duration || active.mediaMetadata?.duration;
  let bookmarks = active.bookmarks || active.libraryItem?.media?.bookmarks || [];
  let coverPath = active.mediaMetadata?.coverPath || active.coverPath;

  // Always query item details (/api/items/{id}?expanded=1) to retrieve the real file path on disk and chapter metadata
  if (libraryItemId) {
    try {
      const itemRes = await absFetch(
        `${cleanUrl}/api/items/${libraryItemId}?expanded=1`,
        {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}` },
        },
        useProxy
      );
      if (itemRes.ok && itemRes.data) {
        const item = itemRes.data;
        const media = item.media || {};
        const meta = media.metadata || {};

        if (meta.title) bookTitle = meta.title;
        if (meta.subtitle) subtitle = meta.subtitle;
        if (meta.authors || meta.author || meta.authorName) {
          author = formatAuthors(meta.authors, meta.author, meta.authorName, author);
        }

        if (media.duration) duration = media.duration;
        if (media.coverPath) coverPath = media.coverPath;
        if (media.bookmarks?.length) bookmarks = media.bookmarks;

        const chapters = media.chapters || [];
        const matchedChapter = chapters.find(
          (c: { start: number; end: number }) => curTime >= c.start && curTime <= c.end
        );
        if (matchedChapter?.title || matchedChapter?.name) {
          chapterName = matchedChapter.title || matchedChapter.name;
        }

        audioFilePath = resolveRealAudioFilePath(media, item, active.audioTrack, curTime);
      }
    } catch {
      // Continue with best available metadata
    }
  }

  if (!audioFilePath) {
    audioFilePath = resolveRealAudioFilePath(active.media, active.libraryItem, active.audioTrack, curTime);
  }

  return {
    libraryItemId: libraryItemId || 'item_default',
    episodeId: active.episodeId || null,
    bookTitle,
    subtitle,
    author,
    chapterName,
    currentTime: curTime,
    audioFilePath,
    duration,
    coverPath,
    bookmarks,
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
  const cleanUrl = sidecarUrl.replace(/\/+$/, '');

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

/**
 * Resolves an audio URL so that clients accessing externally or through a domain
 * stream audio seamlessly via the dashboard server proxy instead of failing on client localhost.
 */
export function getPlayableAudioUrl(
  rawUrl?: string,
  targetSidecar?: string,
  proxyEnabled: boolean = true
): string {
  if (!rawUrl) return '';
  if (rawUrl.startsWith('http://') || rawUrl.startsWith('https://')) {
    // If an external client received a URL pointing to localhost:13380, strip host to route relatively via web server
    if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      try {
        const parsed = new URL(rawUrl);
        if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
          return `${parsed.pathname}${parsed.search}`;
        }
      } catch {}
    }
    return rawUrl;
  }
  const cleanPath = rawUrl.startsWith('/') ? rawUrl : `/${rawUrl}`;
  // Prefer relative URL handled by dashboard proxy whenever proxy is enabled or sidecar is localhost
  if (proxyEnabled || !targetSidecar || targetSidecar.includes('localhost') || targetSidecar.includes('127.0.0.1')) {
    return cleanPath;
  }
  return `${targetSidecar.replace(/\/+$/, '')}${cleanPath}`;
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
  const cleanUrl = serverUrl.replace(/\/+$/, '');
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

