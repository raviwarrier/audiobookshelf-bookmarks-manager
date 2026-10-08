import { sanitizeStoredUrl } from './authStorage';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';

export interface SafeRequestOptions {
  method?: HttpMethod;
  headers?: Record<string, string>;
  body?: any;
  token?: string | null;
  serverUrl?: string;
  sidecarUrl?: string;
  useProxy?: boolean;
}

export interface SafeApiResponse {
  ok: boolean;
  status: number;
  data: any;
  json: () => Promise<any>;
  text: () => Promise<string>;
}

/**
 * Validates that an API path is safe and strictly relative.
 * Rejects path traversal, null bytes, backslashes, scheme characters, or cross-origin prefixes.
 */
export function sanitizeApiPath(path: string): string {
  if (!path || typeof path !== 'string') {
    throw new Error('Invalid API path: must be a non-empty string');
  }
  const trimmed = path.trim();
  // Must start with '/' and not contain '..' or '\' or '//'
  if (!trimmed.startsWith('/') || trimmed.includes('..') || trimmed.includes('\\') || trimmed.startsWith('//')) {
    throw new Error('Security violation: illegal API path traversal');
  }
  // Must strictly start with /api/, /bookmarks/, or /snippets/
  if (!trimmed.startsWith('/api/') && !trimmed.startsWith('/bookmarks/') && !trimmed.startsWith('/snippets/')) {
    throw new Error('Security violation: path must start with /api/, /bookmarks/, or /snippets/');
  }
  // Remove control characters or dangerous injection tokens
  return trimmed.replace(/[\x00-\x1F\x7F<>"'{}|^`]/g, '');
}

export function stripTrailingSlash(url: string): string {
  let end = url.length;
  while (end > 0 && url.codePointAt(end - 1) === 47 /* '/' */) {
    end--;
  }
  return url.slice(0, end);
}

function buildForwardHeaders(options: SafeRequestOptions): Record<string, string> {
  const headers: Record<string, string> = { ...options.headers };
  if (options.token) {
    headers['Authorization'] = `Bearer ${options.token}`;
  }
  if (options.serverUrl) {
    headers['X-ABS-Server-Url'] = options.serverUrl;
  }
  return headers;
}

function parseResponseBody(rawText: string): any {
  try {
    return JSON.parse(rawText);
  } catch {
    return rawText;
  }
}

async function dispatchViaProxy(
  cleanSidecar: string,
  safePath: string,
  method: string,
  headers: Record<string, string>,
  body: any
): Promise<SafeApiResponse> {
  const targetUrl = `${stripTrailingSlash(cleanSidecar)}${safePath}`;
  const proxyRes = await fetch('/api/proxy/abs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ targetUrl, method, headers, body }),
  });

  const text = await proxyRes.text();
  const parsed = parseResponseBody(text);
  const json = typeof parsed === 'object' && parsed !== null ? parsed : { message: text };

  const isOk = proxyRes.ok && json.ok !== false && (!json.status || json.status < 400);
  const innerData = json.data !== undefined ? json.data : json;

  return {
    ok: isOk,
    status: json.status || proxyRes.status,
    data: innerData,
    json: () => Promise.resolve(innerData),
    text: () => Promise.resolve(typeof innerData === 'string' ? innerData : JSON.stringify(innerData)),
  };
}

async function dispatchSameOrigin(
  safePath: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body: any
): Promise<SafeApiResponse> {
  const reqInit: RequestInit = { method, headers };

  if (body !== undefined && body !== null && method !== 'GET') {
    reqInit.body = typeof body === 'string' ? body : JSON.stringify(body);
    if (!headers['Content-Type'] && !headers['content-type']) {
      headers['Content-Type'] = 'application/json';
    }
  }

  const res = await fetch(safePath, reqInit);
  const rawText = await res.text();
  const parsedData = parseResponseBody(rawText);

  return {
    ok: res.ok,
    status: res.status,
    data: parsedData,
    json: () => Promise.resolve(parsedData),
    text: () => Promise.resolve(rawText),
  };
}

/**
 * Dispatches an API request safely without exposing the browser to Client-Side Request Forgery (CWE-918),
 * DOM-based storage injection, or bearer token exfiltration.
 *
 * All requests are strictly dispatched to:
 * 1) Same-origin local relative paths `/api/...` (proxied by the server to the local sidecar).
 * 2) The trusted server-side proxy `/api/proxy/abs` (when targeting an external sidecar or when proxying is forced).
 *
 * At NO point is fetch() ever invoked with an untrusted or unsanitized external URL.
 */
export async function safeSidecarFetch(
  apiPath: string,
  options: SafeRequestOptions = {}
): Promise<SafeApiResponse> {
  const safePath = sanitizeApiPath(apiPath);
  const method = (options.method || 'GET').toUpperCase() as HttpMethod;
  const forwardHeaders = buildForwardHeaders(options);

  const useProxy = options.useProxy !== false;
  const cleanSidecar = options.sidecarUrl ? sanitizeStoredUrl(options.sidecarUrl) : '';

  // Check if sidecar is pointing to an external domain / host
  const isTargetRemote = Boolean(
    cleanSidecar &&
    !cleanSidecar.includes('localhost') &&
    !cleanSidecar.includes('127.0.0.1') &&
    !cleanSidecar.includes('0.0.0.0')
  );

  // If proxy is enabled and target is remote, dispatch through server-side proxy
  if (useProxy && isTargetRemote) {
    return dispatchViaProxy(cleanSidecar, safePath, method, forwardHeaders, options.body);
  }

  // Otherwise, invoke same-origin relative endpoint directly:
  return dispatchSameOrigin(safePath, method, forwardHeaders, options.body);
}
