import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Navbar } from './components/Navbar';
import { CaptureView } from './components/CaptureView';
import { SnippetsView } from './components/SnippetsView';
import { AuthModal } from './components/AuthModal';
import { AbsUser, AbsActiveSession, Snippet, SyncState } from './types';
import { wipeSessionKey } from './lib/crypto';
import { authenticateAbs, fetchActiveSession, formatAuthors } from './lib/absClient';
import { getStoredCredentials, saveStoredCredentials, clearStoredCredentials, sanitizeStoredUrl } from './lib/authStorage';
import { safeSidecarFetch, stripTrailingSlash } from './lib/safeFetch';
import { CheckCircle2, X, Bell } from 'lucide-react';

// Helper to determine initial default sidecar URL
function getDefaultSidecarUrl(): string {
  if (typeof window !== 'undefined' && window.location) {
    const protocol = window.location.protocol;
    const host = window.location.hostname;
    if (host && host !== 'localhost' && host !== '127.0.0.1' && !host.includes('run.app') && !host.includes('webcontainer')) {
      return `${protocol}//${host}:13380`;
    }
  }
  return '';
}

function stripHtmlChars(str: string): string {
  let res = '';
  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    if (ch !== '<' && ch !== '>' && ch !== '"' && ch !== "'") {
      res += ch;
    }
  }
  return res;
}

function cleanStringField(val: unknown, maxLen: number): string | null {
  if (typeof val !== 'string') return null;
  return stripHtmlChars(val.slice(0, maxLen));
}

function cleanFiniteNumber(val: unknown): number {
  return typeof val === 'number' && Number.isFinite(val) ? val : 0;
}

function isSafeDateChar(c: number): boolean {
  if (c >= 48 && c <= 57) return true;
  return c === 84 || c === 58 || c === 46 || c === 45;
}

function isValidDateString(val: unknown): val is string {
  if (typeof val !== 'string' || !val || val.length > 50) return false;
  for (let i = 0; i < val.length; i++) {
    if (!isSafeDateChar(val.charCodeAt(i))) {
      return false;
    }
  }
  return true;
}

function cleanDateField(val: unknown): string | undefined {
  if (isValidDateString(val)) {
    return val;
  }
  return undefined;
}

/**
 * Validates and sanitizes sync state received from remote endpoints,
 * preventing tainted data propagation and DOM injection from compromised servers.
 */
export function sanitizeSyncState(raw: any): SyncState | null {
  if (!raw || typeof raw !== 'object') return null;
  const allowedModes = ['from_start', 'custom_date', 'from_now'];
  return {
    is_syncing: Boolean(raw.is_syncing),
    last_synced_at: cleanStringField(raw.last_synced_at, 64),
    total_synced: cleanFiniteNumber(raw.total_synced),
    current_item: cleanStringField(raw.current_item, 200),
    last_error: cleanStringField(raw.last_error, 500),
    installation_date: cleanDateField(raw.installation_date),
    cutoff_datetime: cleanDateField(raw.cutoff_datetime),
    cutoff_mode: allowedModes.includes(raw.cutoff_mode) ? raw.cutoff_mode : undefined,
    custom_cutoff_date: cleanDateField(raw.custom_cutoff_date),
    installed_at: cleanDateField(raw.installed_at),
    skipped_before_cutoff: cleanFiniteNumber(raw.skipped_before_cutoff),
    skipped_tombstoned: cleanFiniteNumber(raw.skipped_tombstoned),
  };
}

function rewriteLocalhostUrlForExternalClient(rawUrl: string): string {
  if (typeof window === 'undefined' || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return rawUrl;
  }
  try {
    const parsed = new URL(rawUrl);
    if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
      return `${parsed.pathname}${parsed.search}`;
    }
  } catch {}
  return rawUrl;
}

// Resolves audio URL so that clients accessing externally or through a domain
// stream audio seamlessly via the dashboard server proxy instead of failing on client localhost
export function getPlayableAudioUrl(rawUrl?: string): string {
  if (!rawUrl) return '';
  const lowerUrl = rawUrl.toLowerCase();
  if (lowerUrl.startsWith('http://') || lowerUrl.startsWith('https://')) {
    return rewriteLocalhostUrlForExternalClient(rawUrl);
  }
  return rawUrl.startsWith('/') ? rawUrl : `/${rawUrl}`;
}

interface StatusFetchParams {
  activeToken: string;
  serverUrl: string;
  sidecarUrl: string;
  useProxy: boolean;
}

async function fetchBookmarkStatus(params: StatusFetchParams): Promise<any> {
  try {
    const res = await safeSidecarFetch('/api/user/bookmarks/status', {
      method: 'GET',
      token: params.activeToken,
      serverUrl: params.serverUrl,
      sidecarUrl: params.sidecarUrl,
      useProxy: params.useProxy,
    });
    if (!res.ok) return null;
    const json = await res.json();
    return json?.data || json;
  } catch {
    return null;
  }
}

interface RecentBookmarkEvent {
  timestamp?: string;
  extraction_method?: string;
  book_title?: string;
}

function isSafeTimestampChar(c: number): boolean {
  if (c >= 48 && c <= 57) return true;
  if (c >= 65 && c <= 90) return true;
  if (c >= 97 && c <= 122) return true;
  return c === 95 || c === 45;
}

function isValidTimestampString(rawTs: unknown): rawTs is string {
  if (typeof rawTs !== 'string' || !rawTs || rawTs.length > 100) return false;
  for (let i = 0; i < rawTs.length; i++) {
    if (!isSafeTimestampChar(rawTs.charCodeAt(i))) {
      return false;
    }
  }
  return true;
}

function parseRecentEventToast(recentList?: any[]): { id: string; message: string } | null {
  if (!Array.isArray(recentList) || recentList.length === 0) {
    return null;
  }
  const latestEvent: RecentBookmarkEvent = recentList[recentList.length - 1];
  const rawTs = latestEvent?.timestamp;
  if (!isValidTimestampString(rawTs)) {
    return null;
  }
  const methodLabel = latestEvent.extraction_method === 'intercepted' ? 'mobile bookmark' : 'snippet';
  const safeBookTitle = typeof latestEvent.book_title === 'string'
    ? stripHtmlChars(latestEvent.book_title).slice(0, 100)
    : 'Audiobook';

  return {
    id: rawTs,
    message: `New ${methodLabel} ready: "${safeBookTitle}" (${rawTs})`,
  };
}

function isValidTimestampDateChars(str: string): boolean {
  for (let i = 0; i < 15; i++) {
    const c = str.charCodeAt(i);
    if (i === 8) {
      if (c !== 95) return false;
    } else if (c < 48 || c > 57) {
      return false;
    }
  }
  return true;
}

function parseTimestampDate(timestampStr: string): number | null {
  if (typeof timestampStr !== 'string' || timestampStr.length < 15) return null;
  if (!isValidTimestampDateChars(timestampStr)) return null;
  const y = timestampStr.slice(0, 4);
  const m = timestampStr.slice(4, 6);
  const d = timestampStr.slice(6, 8);
  const hr = timestampStr.slice(9, 11);
  const min = timestampStr.slice(11, 13);
  const sec = timestampStr.slice(13, 15);
  const dateObj = new Date(`${y}-${m}-${d}T${hr}:${min}:${sec}`);
  const t = dateObj.getTime();
  return Number.isNaN(t) ? null : t;
}

function parseDateCandidate(raw: unknown): number | null {
  if (!raw) return null;
  const str = String(raw);
  const t = new Date(str).getTime();
  if (!Number.isNaN(t)) return t;
  return parseTimestampDate(str);
}

export function parseBookmarkCreatedAt(b: any): number {
  if (!b || typeof b !== 'object') return Date.now();
  const fromCreated = parseDateCandidate(b.created_at);
  if (fromCreated !== null) return fromCreated;
  const fromTimestamp = parseDateCandidate(b.timestamp);
  if (fromTimestamp !== null) return fromTimestamp;
  return Date.now();
}

export function mapRawBookmarkToSnippet(
  b: any,
  targetSidecar: string,
  proxyEnabled: boolean,
  fallbackUsername: string
): Snippet {
  return {
    id: b.id || `b-${b.timestamp}`,
    bookTitle: b.book_title || 'Unknown Book',
    author: formatAuthors(b.author, b.authors, b.authorName),
    chapterName: b.chapter || 'N/A',
    timestamp: b.timestamp,
    startTime: b.start_time ?? 0,
    currentTime: b.current_time ?? 0,
    libraryItemId: b.library_item_id,
    duration: b.duration ?? 0,
    audioUrl: b.audio_url ? getPlayableAudioUrl(b.audio_url) : '',
    transcript: b.transcript || '',
    markdownContent: `# ${b.book_title || 'Bookmark'}\n\n${b.transcript || ''}`,
    createdAt: parseBookmarkCreatedAt(b),
    username: b.username || fallbackUsername,
    extractionStatus: b.extraction_status === 'unavailable' ? 'unavailable' : (b.audio_url ? 'success' : 'unavailable')
  };
}

export interface InitialConfig {
  initialServer: string;
  initialSidecar: string;
  initialProxy: boolean;
}

function isAbsPlaceholder(url: string): boolean {
  return url.includes('abs.example.com') || url.includes('localhost:13378') || url.includes('127.0.0.1:13378');
}

export function detectServerFromConfig(_cfg: any): string {
  return '';
}

export async function fetchInitialServerConfig(defaultSidecar: string): Promise<InitialConfig> {
  const result: InitialConfig = {
    initialServer: '',
    initialSidecar: defaultSidecar,
    initialProxy: true,
  };

  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    if (cfg?.ok) {
      if (cfg.sidecarUrl) {
        const cleanSidecar = sanitizeStoredUrl(cfg.sidecarUrl);
        if (cleanSidecar) result.initialSidecar = cleanSidecar;
      }
      if (cfg.useBackendProxy !== undefined) {
        result.initialProxy = Boolean(cfg.useBackendProxy);
      }
    }
  } catch (err) {
    console.warn('Could not load /api/config, falling back to defaults:', err);
  }

  return result;
}

export interface AutoConnectTarget {
  server: string;
  sidecar: string;
  proxy: boolean;
  authMode: 'token' | 'userpass';
}

export function resolveAutoConnectTarget(
  saved: NonNullable<ReturnType<typeof getStoredCredentials>>,
  initialConfig: InitialConfig
): AutoConnectTarget {
  const savedServer = saved.serverUrl && !isLocalOrPlaceholder(saved.serverUrl)
    ? sanitizeStoredUrl(saved.serverUrl)
    : '';

  const server = savedServer ? sanitizeStoredUrl(savedServer) : '';
  const sidecar = sanitizeStoredUrl(saved.sidecarUrl || initialConfig.initialSidecar) || initialConfig.initialSidecar;
  const proxy = saved.useProxy ?? initialConfig.initialProxy;
  const authMode = saved.token ? 'token' : (saved.authMode ?? 'token');

  return { server, sidecar, proxy, authMode };
}

interface AutoConnectActions {
  setDefaultServerUrl: (url: string) => void;
  setServerUrl: React.Dispatch<React.SetStateAction<string>>;
  setSidecarUrl: React.Dispatch<React.SetStateAction<string>>;
  setUseProxy: React.Dispatch<React.SetStateAction<boolean>>;
  setIsAuthModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  setUser: React.Dispatch<React.SetStateAction<AbsUser | null>>;
  setActiveToken: React.Dispatch<React.SetStateAction<string | null>>;
  serverUrlRef: React.MutableRefObject<string>;
  userRef: React.MutableRefObject<AbsUser | null>;
  loadActiveSession: (server: string, token: string, proxy: boolean) => Promise<void>;
  syncUserBookmarks: (sidecar: string, token: string, user: string, proxy: boolean) => Promise<void>;
}

function isLocalOrPlaceholder(url?: string | null): boolean {
  if (!url || typeof url !== 'string') return true;
  const trimmed = url.trim().toLowerCase();
  return (
    trimmed === '' ||
    trimmed.includes('localhost:13378') ||
    trimmed.includes('127.0.0.1:13378') ||
    trimmed.includes('abs.example.com')
  );
}

function sanitizeInitialServer(url?: string | null): string {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (isLocalOrPlaceholder(trimmed)) return '';
  return trimmed;
}

async function attemptSavedAutoConnect(
  saved: NonNullable<ReturnType<typeof getStoredCredentials>>,
  config: InitialConfig,
  isCancelled: () => boolean,
  actions: AutoConnectActions
) {
  try {
    const target = resolveAutoConnectTarget(saved, config);
    if (!target.server) {
      if (!isCancelled() && !actions.userRef.current) {
        actions.setIsAuthModalOpen(true);
      }
      return;
    }
    actions.setServerUrl(target.server);
    actions.serverUrlRef.current = target.server;
    actions.setSidecarUrl(target.sidecar);
    actions.setUseProxy(target.proxy);

    const authResult = await authenticateAbs(
      target.server,
      target.authMode,
      saved.token,
      saved.username,
      saved.password,
      target.proxy
    );

    if (isCancelled()) return;

    actions.setUser(authResult.user);
    actions.userRef.current = authResult.user;
    actions.setActiveToken(authResult.token);
    actions.setIsAuthModalOpen(false);

    actions.loadActiveSession(target.server, authResult.token, target.proxy);
    actions.syncUserBookmarks(target.sidecar, authResult.token, authResult.user.username, target.proxy);
  } catch (autoErr) {
    console.warn('Auto-reconnect with saved credentials notice:', autoErr);
    if (!isCancelled() && !actions.userRef.current) {
      actions.setIsAuthModalOpen(true);
    }
  }
}

function handleUnauthenticatedFallback(
  config: InitialConfig,
  isCancelled: () => boolean,
  actions: AutoConnectActions
) {
  if (isCancelled() || actions.userRef.current) return;
  actions.setServerUrl((prev) => (!prev || isLocalOrPlaceholder(prev) ? config.initialServer : prev));
  actions.setSidecarUrl((prev) => (prev.includes('[your ip:port') ? config.initialSidecar : prev));
  actions.setUseProxy(config.initialProxy);
  actions.setIsAuthModalOpen(true);
}

async function runAutoConnectStartup(
  config: InitialConfig,
  isCancelled: () => boolean,
  actions: AutoConnectActions
) {
  if (config.initialServer) {
    actions.setDefaultServerUrl(config.initialServer);
    actions.setServerUrl((prev) => (!prev || isLocalOrPlaceholder(prev) ? config.initialServer : prev));
    actions.serverUrlRef.current = config.initialServer;
  }

  if (isCancelled() || actions.userRef.current) return;

  const saved = getStoredCredentials();
  const hasSavedAuth = Boolean(saved?.token || (saved?.username && saved?.password));

  if (hasSavedAuth && saved) {
    await attemptSavedAutoConnect(saved, config, isCancelled, actions);
  } else {
    handleUnauthenticatedFallback(config, isCancelled, actions);
  }
}

async function executeStatusPollingCycle(
  endpointParams: StatusFetchParams,
  callbacks: {
    username: string;
    isSyncing: boolean;
    handleSyncStatus: (rawSync: any) => Promise<void>;
    handleRecentToast: (recentList: any[]) => Promise<void>;
  }
): Promise<number> {
  if (typeof document !== 'undefined' && document.hidden) {
    return 45000;
  }

  const statusData = await fetchBookmarkStatus(endpointParams);
  if (statusData) {
    await callbacks.handleSyncStatus(statusData.sync_state);
    await callbacks.handleRecentToast(statusData.recent);
  }

  return callbacks.isSyncing ? 6000 : 18000;
}

function buildSnippetDeleteParams(target?: Snippet): string {
  if (!target) return '';
  const queryParams = new URLSearchParams();
  if (target.libraryItemId) queryParams.set('library_item_id', target.libraryItemId);
  const timeVal = target.currentTime ?? target.startTime;
  if (typeof timeVal === 'number') queryParams.set('time', String(timeVal));
  if (typeof target.startTime === 'number') queryParams.set('start_time', String(target.startTime));
  if (target.bookTitle) queryParams.set('book_title', target.bookTitle);
  if (target.timestamp) queryParams.set('timestamp', target.timestamp);
  if (target.createdAt) queryParams.set('created_at', String(target.createdAt));
  const qs = queryParams.toString();
  return qs ? `?${qs}` : '';
}

export function App() {
  const [activeView, setActiveView] = useState<'capture' | 'library'>('capture');
  
  // Connection and Authentication State
  const savedInitial = typeof window !== 'undefined' ? getStoredCredentials() : null;

  const [user, setUser] = useState<AbsUser | null>(null);
  const [activeToken, setActiveToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState<string>(
    sanitizeInitialServer(savedInitial?.serverUrl) || ''
  );
  const [defaultServerUrl, setDefaultServerUrl] = useState<string>('');
  const [sidecarUrl, setSidecarUrl] = useState<string>(savedInitial?.sidecarUrl || getDefaultSidecarUrl());
  const [useProxy, setUseProxy] = useState<boolean>(savedInitial?.useProxy ?? true);

  const serverUrlRef = useRef<string>(serverUrl);
  serverUrlRef.current = serverUrl;
  const userRef = useRef<AbsUser | null>(user);
  userRef.current = user;

  // Active Listening Session
  const [session, setSession] = useState<AbsActiveSession | null>(null);
  const [isLoadingSession, setIsLoadingSession] = useState<boolean>(false);
  const [sessionError, setSessionError] = useState<string | null>(null);

  // Auth modal control: If credentials were saved previously, keep modal closed while auto-connecting
  const [isAuthModalOpen, setIsAuthModalOpen] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    const saved = getStoredCredentials();
    return !(saved?.token || (saved?.username && saved?.password));
  });

  // Snippets library state
  const [snippets, setSnippets] = useState<Snippet[]>([]);
  const [isLoadingBookmarks, setIsLoadingBookmarks] = useState<boolean>(false);

  // Background Sync state
  const [syncState, setSyncState] = useState<SyncState | null>(null);
  const [isTriggeringSync, setIsTriggeringSync] = useState<boolean>(false);

  // Notification toast for automatic background detection
  const [notification, setNotification] = useState<{ message: string; id: string } | null>(null);
  const lastKnownTimestampRef = useRef<string | null>(null);
  const notifiedIdsRef = useRef<Set<string>>(new Set<string>());
  const isSyncingRef = useRef<boolean>(false);
  const notificationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync user's bookmarks from sidecar's {username}/bookmarks directory
  const syncUserBookmarks = useCallback(async (
    targetSidecar: string,
    token: string,
    username: string,
    proxyEnabled: boolean
  ) => {
    if (isSyncingRef.current) return;
    isSyncingRef.current = true;
    setIsLoadingBookmarks(true);

    try {
      let bookmarksList: any[] = [];
      const res = await safeSidecarFetch('/api/user/bookmarks', {
        method: 'GET',
        token,
        serverUrl: serverUrlRef.current,
        sidecarUrl: targetSidecar,
        useProxy: proxyEnabled,
      });

      if (res.ok) {
        const json = await res.json();
        const rawList = json?.bookmarks || json?.data?.bookmarks || (Array.isArray(json) ? json : []);
        if (Array.isArray(rawList)) {
          bookmarksList = rawList;
        }
      }

      if (bookmarksList.length > 0) {
        const sidecarBookmarks: Snippet[] = bookmarksList.map((b: any) =>
          mapRawBookmarkToSnippet(b, targetSidecar, proxyEnabled, username)
        );
        setSnippets(sidecarBookmarks);

        // Update latest known timestamp
        if (sidecarBookmarks[0]?.timestamp) {
          lastKnownTimestampRef.current = sidecarBookmarks[0].timestamp;
        }
      }
    } catch (err) {
      console.warn('Sidecar bookmarks sync notice:', err);
    } finally {
      setIsLoadingBookmarks(false);
      isSyncingRef.current = false;
    }
  }, [serverUrl]);

  // Automatically fetch active listening session once user connects
  const loadActiveSession = useCallback(async (
    targetServer: string,
    token: string,
    proxyEnabled: boolean
  ) => {
    setIsLoadingSession(true);
    setSessionError(null);
    try {
      const active = await fetchActiveSession(targetServer, token, proxyEnabled);
      setSession(active);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Could not fetch active listening session';
      setSessionError(msg);
    } finally {
      setIsLoadingSession(false);
    }
  }, []);

  // Handle connection from Auth Modal
  const handleConnect = async (params: {
    serverUrl: string;
    sidecarUrl: string;
    useProxy: boolean;
    authMode: 'token' | 'userpass';
    token?: string;
    username?: string;
    password?: string;
    isMock?: boolean;
    remember?: boolean;
  }) => {
    const cleanServerUrl = params.serverUrl.trim();
    serverUrlRef.current = cleanServerUrl;
    setServerUrl(cleanServerUrl);
    setSidecarUrl(params.sidecarUrl.trim());
    setUseProxy(params.useProxy);

    if (params.isMock) {
      const mockUser = { id: 'usr_mock', username: 'bookworm_user' };
      setUser(mockUser);
      userRef.current = mockUser;
      setActiveToken('mock_token_demo');
      setSession({
        libraryItemId: 'li_sample_project_hail_mary',
        bookTitle: 'Project Hail Mary',
        author: 'Andy Weir',
        chapterName: 'Chapter 04 - Laboratory Discovery',
        currentTime: 1420,
        duration: 36000,
        audioFilePath: '/audiobooks/Andy Weir/Project Hail Mary/Project_Hail_Mary_Part01.m4b',
      });
      setSessionError(null);
      setIsAuthModalOpen(false);
      return;
    }

    const authResult = await authenticateAbs(
      cleanServerUrl,
      params.authMode,
      params.token,
      params.username,
      params.password,
      params.useProxy
    );

    setUser(authResult.user);
    userRef.current = authResult.user;
    setActiveToken(authResult.token);
    setIsAuthModalOpen(false);

    // Save or clear credentials based on the user's "remember" choice
    if (params.remember) {
      saveStoredCredentials({
        serverUrl: cleanServerUrl,
        sidecarUrl: params.sidecarUrl.trim(),
        useProxy: params.useProxy,
        authMode: params.authMode,
        token: params.authMode === 'token' ? (params.token || authResult.token) : authResult.token,
        username: params.username || authResult.user.username,
        remember: true,
      });
    } else {
      clearStoredCredentials();
    }

    loadActiveSession(cleanServerUrl, authResult.token, params.useProxy);
    syncUserBookmarks(params.sidecarUrl.trim(), authResult.token, authResult.user.username, params.useProxy);
  };

  // On App Mount: Auto-login from persistent saved credentials if available
  useEffect(() => {
    let isCancelled = false;

    const initializeConnection = async () => {
      const config = await fetchInitialServerConfig(getDefaultSidecarUrl());
      await runAutoConnectStartup(config, () => isCancelled, {
        setDefaultServerUrl,
        setServerUrl,
        setSidecarUrl,
        setUseProxy,
        setIsAuthModalOpen,
        setUser,
        setActiveToken,
        serverUrlRef,
        userRef,
        loadActiveSession,
        syncUserBookmarks,
      });
    };

    initializeConnection();

    return () => {
      isCancelled = true;
    };
  }, [loadActiveSession, syncUserBookmarks]);

  const handleSyncStatusUpdate = useCallback(async (
    rawSyncState: any,
    targetSidecar: string,
    token: string,
    username: string,
    proxy: boolean
  ) => {
    if (!rawSyncState) return;
    const cleanSync = sanitizeSyncState(rawSyncState);
    if (!cleanSync) return;

    const wasSyncing = syncState?.is_syncing;
    setSyncState(cleanSync);
    if (wasSyncing && !cleanSync.is_syncing) {
      await syncUserBookmarks(targetSidecar, token, username, proxy);
    }
  }, [syncState?.is_syncing, syncUserBookmarks]);

  const handleRecentToast = useCallback(async (
    recentList: any[],
    targetSidecar: string,
    token: string,
    username: string,
    proxy: boolean
  ) => {
    const toast = parseRecentEventToast(recentList);
    if (!toast || notifiedIdsRef.current.has(toast.id)) return;

    notifiedIdsRef.current.add(toast.id);
    lastKnownTimestampRef.current = toast.id;

    await syncUserBookmarks(targetSidecar, token, username, proxy);

    setNotification(toast);
    if (notificationTimeoutRef.current) {
      clearTimeout(notificationTimeoutRef.current);
    }
    notificationTimeoutRef.current = setTimeout(() => {
      setNotification((curr) => (curr?.id === toast.id ? null : curr));
    }, 6000);
  }, [syncUserBookmarks]);

  // Automated Real-Time Background Polling:
  // Detects newly completed manual or intercepted bookmarks with adaptive, visibility-aware intervals
  useEffect(() => {
    if (!activeToken || !user) return;

    let isSubscribed = true;
    let timerId: ReturnType<typeof setTimeout> | null = null;

    const pollStatus = async () => {
      if (!isSubscribed) return;
      const delay = await executeStatusPollingCycle(
        {
          activeToken,
          serverUrl,
          sidecarUrl,
          useProxy,
        },
        {
          username: user.username,
          isSyncing: Boolean(syncState?.is_syncing),
          handleSyncStatus: async (rawSync) => {
            await handleSyncStatusUpdate(rawSync, sidecarUrl, activeToken, user.username, useProxy);
          },
          handleRecentToast: async (recentList) => {
            await handleRecentToast(recentList, sidecarUrl, activeToken, user.username, useProxy);
          },
        }
      );
      if (isSubscribed) {
        timerId = setTimeout(pollStatus, delay);
      }
    };

    // Initial poll after short delay
    timerId = setTimeout(pollStatus, 2500);

    // Resume immediately when user focuses back on the tab
    const handleVisibilityChange = () => {
      if (typeof document !== 'undefined' && !document.hidden && isSubscribed) {
        if (timerId) clearTimeout(timerId);
        pollStatus();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      isSubscribed = false;
      if (timerId) clearTimeout(timerId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [activeToken, user, sidecarUrl, serverUrl, useProxy, handleSyncStatusUpdate, handleRecentToast, syncState?.is_syncing]);

  // Autonomous / Manual Trigger for Audiobookshelf Background Bookmark Sync
  const handleTriggerSync = useCallback(async () => {
    if (!activeToken || !user || isTriggeringSync) return;
    setIsTriggeringSync(true);
    try {
      await safeSidecarFetch('/api/user/sync-bookmarks', {
        method: 'POST',
        token: activeToken,
        serverUrl,
        sidecarUrl,
        useProxy,
      });
      // Refresh local snippet list
      await syncUserBookmarks(sidecarUrl, activeToken, user.username, useProxy);
    } catch (e) {
      console.warn('Sync trigger notice:', e);
    } finally {
      setIsTriggeringSync(false);
    }
  }, [activeToken, user, isTriggeringSync, sidecarUrl, useProxy, serverUrl, syncUserBookmarks]);

  // Re-sync session playback position on demand
  const handleRefreshSession = async () => {
    if (!activeToken) return;
    await loadActiveSession(serverUrl, activeToken, useProxy);
  };

  // Wipes in-memory session and clears saved credentials from local storage
  const handleWipeSession = () => {
    setUser(null);
    setActiveToken(null);
    setSession(null);
    setSessionError(null);
    clearStoredCredentials();
    wipeSessionKey();
    setIsAuthModalOpen(true);
  };

  // When a snippet is manually created:
  // 1. Instantly adds it to state
  // 2. Re-syncs full library from server
  // 3. Switches active view to library so user immediately sees the snippet without refreshing!
  const handleSnippetCreated = async (newSnippet: Snippet) => {
    setSnippets((prev) => [newSnippet, ...prev]);
    setNotification({
      id: newSnippet.timestamp,
      message: `Snippet created: "${newSnippet.bookTitle}" (${newSnippet.duration}s)!`,
    });
    setTimeout(() => setNotification(null), 5000);
    
    // Switch to library view immediately
    setActiveView('library');

    // Sync from server in background to ensure all metadata is uniform
    if (activeToken && user) {
      await syncUserBookmarks(sidecarUrl, activeToken, user.username, useProxy);
    }
  };

  const handleDeleteSnippet = async (id: string, snippetToDelete?: Snippet) => {
    // Find snippet metadata before removal to pass rich tombstone identifiers
    const target = snippetToDelete || snippets.find((s) => s.id === id);
    setSnippets((prev) => prev.filter((s) => s.id !== id));

    if (!activeToken) return;
    try {
      const qs = buildSnippetDeleteParams(target);
      await safeSidecarFetch(`/api/user/bookmarks/${encodeURIComponent(id)}${qs}`, {
        method: 'DELETE',
        token: activeToken,
        serverUrl,
        sidecarUrl,
        useProxy,
      });
    } catch (err) {
      console.warn('Notice deleting bookmark from disk:', err);
    }
  };

  return (
    <div className="min-h-screen bg-[#050505] text-neutral-200 font-mono flex flex-col relative">
      
      {/* Real-time Notification Banner for Newly Extracted Bookmarks */}
      {notification && (
        <aside 
          aria-label="New bookmark notification"
          className="fixed top-4 right-4 z-50 bg-[#121212] border border-emerald-500/80 text-white px-4 py-3 shadow-2xl flex items-center gap-3 animate-in fade-in slide-in-from-top-2 duration-300 max-w-md"
        >
          <div className="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0">
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <div className="text-xs space-y-0.5 flex-1">
            <div className="font-semibold text-emerald-300 flex items-center gap-1.5">
              <Bell className="w-3 h-3" />
              <span>Bookmark Detected & Transcribed</span>
            </div>
            <p className="text-neutral-300 truncate">{notification.message}</p>
          </div>
          <button
            onClick={() => setActiveView('library')}
            className="text-[11px] underline text-neutral-300 hover:text-white px-1.5 py-0.5"
          >
            View
          </button>
          <button
            onClick={() => setNotification(null)}
            className="text-neutral-400 hover:text-white p-1"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </aside>
      )}

      {/* Navigation Header */}
      <Navbar
        activeView={activeView}
        onViewChange={setActiveView}
        user={user}
        snippetCount={snippets.length}
        onOpenAuthModal={() => setIsAuthModalOpen(true)}
        onWipeSession={handleWipeSession}
      />

      {/* Main View Container */}
      <main className="flex-1 max-w-6xl w-full mx-auto p-4 md:p-8">
        {activeView === 'capture' ? (
          <CaptureView
            user={user}
            activeToken={activeToken}
            serverUrl={serverUrl}
            sidecarUrl={sidecarUrl}
            useProxy={useProxy}
            session={session}
            isLoadingSession={isLoadingSession}
            sessionError={sessionError}
            onRefreshSession={handleRefreshSession}
            onOpenAuthModal={() => setIsAuthModalOpen(true)}
            onSnippetCreated={handleSnippetCreated}
            onNavigateToLibrary={() => setActiveView('library')}
            onUseMockSession={() => {
              handleConnect({
                serverUrl,
                sidecarUrl,
                useProxy,
                authMode: 'token',
                token: 'mock_token',
                isMock: true,
              });
            }}
          />
        ) : (
          <SnippetsView
            snippets={snippets}
            user={user}
            activeToken={activeToken}
            serverUrl={serverUrl}
            sidecarUrl={sidecarUrl}
            useProxy={useProxy}
            syncState={syncState}
            isTriggeringSync={isTriggeringSync}
            onTriggerSync={handleTriggerSync}
            onDeleteSnippet={handleDeleteSnippet}
            onNavigateToCapture={() => setActiveView('capture')}
            onRefreshSnippets={async () => {
              if (activeToken && user) {
                await handleTriggerSync();
              }
            }}
            isLoadingSnippets={isLoadingBookmarks || isTriggeringSync}
            onCutoffUpdated={(newSyncState) => {
              setSyncState(newSyncState);
            }}
          />
        )}
      </main>

      {/* Auth & User Switching Modal */}
      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        user={user}
        currentServerUrl={serverUrl}
        defaultServerUrl={defaultServerUrl}
        currentSidecarUrl={sidecarUrl}
        currentUseProxy={useProxy}
        onConnect={handleConnect}
        onDisconnect={handleWipeSession}
      />

      {/* Minimal Footer */}
      <footer className="border-t border-neutral-900 px-6 py-4 text-center text-xs text-neutral-600 font-mono flex items-center justify-center gap-2">
        <span>Audiobookshelf Bookmarks Manager</span>
        <span>•</span>
        <span className="text-neutral-500">v2.1.0</span>
      </footer>
    </div>
  );
}

export default App;
