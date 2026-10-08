import { StoredCredentials } from '../types';

const STORAGE_KEY = 'abs_user_connection_profile';

function isValidIpv4Host(host: string): boolean {
  const parts = host.split('.');
  if (parts.length !== 4) return false;
  for (const part of parts) {
    if (part.length === 0 || part.length > 3) return false;
    for (let i = 0; i < part.length; i++) {
      const code = part.codePointAt(i);
      if (code === undefined || code < 48 || code > 57) return false;
    }
    const num = Number(part);
    if (num < 0 || num > 255) return false;
  }
  return true;
}

/**
 * Checks whether a server URL matches an IP address (with or without port) or localhost.
 * Used to decide whether to show an editable text input or a verified domain badge with an Edit button.
 */
export function isIpPortUrl(url?: string): boolean {
  if (!url || typeof url !== 'string') return true;
  const trimmed = url.trim();
  if (!trimmed) return true;

  try {
    const formatted = trimmed.startsWith('http://') || trimmed.startsWith('https://')
      ? trimmed
      : `http://${trimmed}`;
    const parsed = new URL(formatted);
    const host = parsed.hostname.toLowerCase();

    // Check for standard loopback or IP patterns
    if (host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0' || host === '::1') {
      return true;
    }

    if (isValidIpv4Host(host)) {
      return true;
    }

    // If host has no dot or contains raw port notation like mypi:13378 without TLD
    if (!host.includes('.')) {
      return true;
    }

    return false;
  } catch {
    return true;
  }
}

/**
 * Formats a clean display label for a server URL.
 */
export function formatServerDisplay(url: string): { domain: string; protocol: string; full: string } {
  try {
    const formatted = url.startsWith('http://') || url.startsWith('https://') ? url : `https://${url}`;
    const parsed = new URL(formatted);
    return {
      domain: parsed.host,
      protocol: parsed.protocol.replace(':', '').toUpperCase(),
      full: url.trim(),
    };
  } catch {
    return { domain: url, protocol: 'HTTP', full: url };
  }
}

/**
 * Saves authenticated connection credentials to localStorage so the user doesn't
 * need to re-type server URL, auth mode, and token on every reload.
 */
export function saveStoredCredentials(creds: StoredCredentials): void {
  if (typeof window === 'undefined') return;
  try {
    if (creds.remember) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(creds));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (err) {
    console.warn('Failed to save connection credentials to localStorage:', err);
  }
}

const INVALID_URL_CHAR_SET = new Set(['<', '>', '"', "'", '{', '}', '|', '\\', '^', '`']);

function isInvalidUrlChars(str: string): boolean {
  for (let i = 0; i < str.length; i++) {
    const code = str.codePointAt(i);
    if (code === undefined || code <= 31 || code === 127 || INVALID_URL_CHAR_SET.has(str[i])) {
      return true;
    }
  }
  return false;
}

function isForbiddenHost(host: string): boolean {
  return (
    host.startsWith('169.254.') ||
    host === 'metadata.google.internal' ||
    host === 'metadata' ||
    host === 'instance-data'
  );
}

function isValidHostChars(host: string): boolean {
  for (let i = 0; i < host.length; i++) {
    const code = host.codePointAt(i);
    if (code === undefined) return false;
    const isAlphaNum =
      (code >= 48 && code <= 57) ||
      (code >= 65 && code <= 90) ||
      (code >= 97 && code <= 122);
    const isSpecial = code === 46 || code === 45 || code === 95 || code === 58;
    if (!isAlphaNum && !isSpecial) return false;
  }
  return true;
}

function isValidPort(portStr: string): boolean {
  if (!portStr) return true;
  const portNum = Number(portStr);
  return !Number.isNaN(portNum) && portNum >= 1 && portNum <= 65535;
}

function isValidParsedUrl(parsed: URL): boolean {
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  if (parsed.username || parsed.password) return false;
  const host = parsed.hostname.toLowerCase();
  if (isForbiddenHost(host) || !isValidHostChars(parsed.host) || !isValidPort(parsed.port)) {
    return false;
  }
  return true;
}

/**
 * Sanitizes and validates a URL loaded from untrusted browser storage,
 * preventing DOM-based injection and Client-Side Request Forgery (CWE-79 / CWE-918).
 */
export function sanitizeStoredUrl(url?: string | null): string {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (!trimmed || trimmed.length > 2048 || isInvalidUrlChars(trimmed)) {
    return '';
  }

  try {
    const formatted = trimmed.startsWith('http://') || trimmed.startsWith('https://')
      ? trimmed
      : `http://${trimmed}`;
    const parsed = new URL(formatted);
    if (!isValidParsedUrl(parsed)) {
      return '';
    }
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return '';
  }
}

/**
 * Safely constructs and validates a destination URL for API requests,
 * strictly preventing Client-Side Request Forgery (CSRF / CWE-918) and open redirection.
 */
export function buildSafeApiUrl(baseUrl: string, apiPath: string): string {
  const cleanPath = apiPath.startsWith('/') ? apiPath : `/${apiPath}`;
  if (!cleanPath.startsWith('/api/') || cleanPath.includes('..') || cleanPath.includes('\\')) {
    throw new Error('Security violation: invalid API path requested.');
  }

  const trimmedBase = (baseUrl || '').trim();
  if (
    !trimmedBase ||
    trimmedBase.includes('[your ip:port') ||
    trimmedBase === 'http://localhost:13380' ||
    trimmedBase === 'http://127.0.0.1:13380'
  ) {
    // When using local/default sidecar, return relative path directly on current origin
    return cleanPath;
  }

  const sanitizedBase = sanitizeStoredUrl(trimmedBase);
  if (!sanitizedBase) {
    return cleanPath;
  }

  try {
    const resolved = new URL(cleanPath, sanitizedBase);
    if (resolved.protocol !== 'http:' && resolved.protocol !== 'https:') {
      return cleanPath;
    }
    return resolved.toString();
  } catch {
    return cleanPath;
  }
}

function parseAndSanitizeCredentials(parsed: any): StoredCredentials | null {
  if (!parsed || typeof parsed !== 'object') return null;

  const sanitizedServer = sanitizeStoredUrl(parsed.serverUrl);
  const sanitizedSidecar = sanitizeStoredUrl(parsed.sidecarUrl);
  const cleanToken = typeof parsed.token === 'string' && /^[a-zA-Z0-9_\-.~+/=]{1,512}$/.test(parsed.token.trim())
    ? parsed.token.trim()
    : undefined;
  const cleanUsername = typeof parsed.username === 'string'
    ? parsed.username.trim().replace(/[\x00-\x1F\x7F<>"'{}|\\^`]/g, '').slice(0, 100)
    : undefined;

  if (!sanitizedServer && !cleanToken && !sanitizedSidecar) {
    return null;
  }

  return {
    serverUrl: sanitizedServer || '',
    sidecarUrl: sanitizedSidecar || '',
    token: cleanToken,
    username: cleanUsername,
    authMode: parsed.authMode === 'token' ? 'token' : 'userpass',
    remember: Boolean(parsed.remember)
  };
}

/**
 * Retrieves saved credentials from localStorage if present.
 * Strictly sanitizes raw storage data against DOM-based attacks and CSRF.
 */
export function getStoredCredentials(): StoredCredentials | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;

    // Defense against oversized or contaminated browser storage payload
    if (raw.length > 4096 || /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/.test(raw)) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }

    return parseAndSanitizeCredentials(JSON.parse(raw));
  } catch (err) {
    console.warn('Failed to parse stored credentials from localStorage:', err);
    return null;
  }
}

/**
 * Clears saved credentials from localStorage upon explicit disconnect or wipe.
 */
export function clearStoredCredentials(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (err) {
    console.warn('Failed to clear stored credentials:', err);
  }
}
