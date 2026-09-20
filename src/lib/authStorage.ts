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
 * Retrieves saved credentials from localStorage if present.
 */
export function getStoredCredentials(): StoredCredentials | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && (parsed.serverUrl || parsed.token)) {
      return parsed as StoredCredentials;
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
