import { sanitizeStoredUrl } from './authStorage';

export interface SafeRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
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
  const method = (options.method || 'GET').toUpperCase() as 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';

  const forwardHeaders: Record<string, string> = { ...(options.headers || {}) };
  if (options.token) {
    forwardHeaders['Authorization'] = `Bearer ${options.token}`;
  }
  if (options.serverUrl) {
    forwardHeaders['X-ABS-Server-Url'] = options.serverUrl;
  }

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
    const targetUrl = `${cleanSidecar.replace(/\/+$/, '')}${safePath}`;
    const proxyRes = await fetch('/api/proxy/abs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        targetUrl,
        method,
        headers: forwardHeaders,
        body: options.body,
      }),
    });

    let json: any = null;
    let text = '';
    try {
      text = await proxyRes.text();
      json = JSON.parse(text);
    } catch {
      json = { message: text };
    }

    const isOk = proxyRes.ok && (json?.ok !== false) && (json?.status ? json.status < 400 : true);
    const innerData = json?.data !== undefined ? json.data : json;

    return {
      ok: isOk,
      status: json?.status || proxyRes.status,
      data: innerData,
      json: async () => innerData,
      text: async () => (typeof innerData === 'string' ? innerData : JSON.stringify(innerData)),
    };
  }

  // Otherwise, invoke same-origin relative endpoint directly:
  const reqInit: RequestInit = {
    method,
    headers: forwardHeaders,
  };

  if (options.body !== undefined && options.body !== null && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    if (typeof options.body === 'string') {
      reqInit.body = options.body;
      if (!forwardHeaders['Content-Type'] && !forwardHeaders['content-type']) {
        forwardHeaders['Content-Type'] = 'application/json';
      }
    } else {
      reqInit.body = JSON.stringify(options.body);
      if (!forwardHeaders['Content-Type'] && !forwardHeaders['content-type']) {
        forwardHeaders['Content-Type'] = 'application/json';
      }
    }
  }

  const res = await fetch(safePath, reqInit);
  let parsedData: any = null;
  let rawText = '';
  try {
    rawText = await res.text();
    parsedData = JSON.parse(rawText);
  } catch {
    parsedData = rawText;
  }

  return {
    ok: res.ok,
    status: res.status,
    data: parsedData,
    json: async () => parsedData,
    text: async () => rawText,
  };
}
