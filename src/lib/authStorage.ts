import { StoredCredentials } from '../types';

const STORAGE_KEY = 'abs_user_connection_profile';

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

    // IPv4 pattern: 4 numeric segments separated by dots
    const isIpv4 = /^(\d{1,3}\.){3}\d{1,3}$/.test(host);
    if (isIpv4) {
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

/**
 * Sanitizes and validates a URL loaded from untrusted browser storage,
 * preventing DOM-based injection and Client-Side Request Forgery (CWE-79 / CWE-918).
 */
export function sanitizeStoredUrl(url?: string | null): string {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (!trimmed || trimmed.length > 2048) return '';
  
  // Reject control characters, newlines, quotation marks, or HTML characters
  if (/[\x00-\x1F\x7F<>"'{}|\\^`]/g.test(trimmed)) {
    return '';
  }

  try {
    const formatted = trimmed.startsWith('http://') || trimmed.startsWith('https://')
      ? trimmed
      : `http://${trimmed}`;
    const parsed = new URL(formatted);

    // Strictly allow only http: and https: protocols
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return '';
    }

    // Prohibit embedded credentials (e.g. http://user:pass@host)
    if (parsed.username || parsed.password) {
      return '';
    }

    const host = parsed.hostname.toLowerCase();

    // Prohibit cloud metadata endpoints and internal IP leakage vectors
    if (
      host === '169.254.169.254' ||
      host.startsWith('169.254.') ||
      host === 'metadata.google.internal' ||
      host === 'metadata' ||
      host === 'instance-data'
    ) {
      return '';
    }

    // Ensure hostname contains only valid domain/IP characters
    if (!/^[a-zA-Z0-9.\-_:]+$/.test(parsed.host)) {
      return '';
    }

    // Validate port range if specified
    if (parsed.port) {
      const portNum = Number(parsed.port);
      if (isNaN(portNum) || portNum < 1 || portNum > 65535) {
        return '';
      }
    }

    // Return clean origin (protocol + host/port) without path, query, or hash
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

    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const sanitizedServer = sanitizeStoredUrl(parsed.serverUrl);
      const sanitizedSidecar = sanitizeStoredUrl(parsed.sidecarUrl);
      const cleanToken = typeof parsed.token === 'string' && /^[a-zA-Z0-9_\-.~+/=]{1,512}$/.test(parsed.token.trim())
        ? parsed.token.trim()
        : undefined;
      const cleanUsername = typeof parsed.username === 'string'
        ? parsed.username.trim().replace(/[\x00-\x1F\x7F<>"'{}|\\^`]/g, '').slice(0, 100)
        : undefined;

      if (sanitizedServer || cleanToken || sanitizedSidecar) {
        return {
          serverUrl: sanitizedServer || '',
          sidecarUrl: sanitizedSidecar || '',
          token: cleanToken,
          username: cleanUsername,
          authMode: parsed.authMode === 'token' ? 'token' : 'userpass',
          remember: Boolean(parsed.remember)
        };
      }
    }
  } catch (err) {
    console.warn('Failed to parse stored credentials from localStorage:', err);
  }
  return null;
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
