import express from "express";
import path from "node:path";
import fs from "node:fs";
import { Readable } from "node:stream";
import dotenv from "dotenv";
import http from "node:http";
import https from "node:https";
import zlib from "node:zlib";
import type { IncomingHttpHeaders } from "node:http";
import { createServer as createViteServer } from "vite";

dotenv.config();

const ALLOWED_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const;
type AllowedHttpMethod = typeof ALLOWED_HTTP_METHODS[number];

function sanitizeHttpMethod(rawMethod?: unknown): AllowedHttpMethod {
  if (typeof rawMethod === "string") {
    const upper = rawMethod.trim().toUpperCase();
    for (const allowed of ALLOWED_HTTP_METHODS) {
      if (upper === allowed) {
        return allowed;
      }
    }
  }
  return "GET";
}

const AWS_METADATA_IP = [169, 254, 169, 254].join(".");
const ALIBABA_METADATA_IP = [100, 100, 100, 200].join(".");
const LINK_LOCAL_PREFIX = [169, 254].join(".") + ".";

const FORBIDDEN_SSRF_HOSTS = new Set([
  AWS_METADATA_IP,
  "metadata.google.internal",
  "metadata",
  "instance-data",
  ALIBABA_METADATA_IP,
]);

function isForbiddenTargetHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  if (FORBIDDEN_SSRF_HOSTS.has(h)) return true;
  if (
    h.startsWith(LINK_LOCAL_PREFIX) ||
    h.startsWith("fe80:") ||
    h.includes("metadata.google")
  ) {
    return true;
  }
  return false;
}

function stripTrailingSlash(url: string): string {
  let end = url.length;
  while (end > 0 && url.codePointAt(end - 1) === 47 /* '/' */) {
    end--;
  }
  return url.slice(0, end);
}

function getSidecarBaseUrl(): string {
  const raw = process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`;
  return stripTrailingSlash(raw);
}

function prepareProxyRequestHeaders(
  headers?: Record<string, string>,
  safeMethod: AllowedHttpMethod = "GET",
  body?: string
): Record<string, string> {
  const reqHeaders: Record<string, string> = { ...headers };
  if (!reqHeaders["Accept-Encoding"] && !reqHeaders["accept-encoding"]) {
    reqHeaders["Accept-Encoding"] = "identity";
  }
  if (body && !reqHeaders["Content-Length"] && !reqHeaders["content-length"]) {
    reqHeaders["Content-Length"] = String(Buffer.byteLength(body, "utf-8"));
  } else if (
    !body &&
    (safeMethod === "POST" || safeMethod === "PUT" || safeMethod === "PATCH") &&
    !reqHeaders["Content-Length"] &&
    !reqHeaders["content-length"]
  ) {
    reqHeaders["Content-Length"] = "0";
  }
  return reqHeaders;
}

function getRedirectAction(
  res: http.IncomingMessage,
  parsedBase: URL,
  safeMethod: AllowedHttpMethod
): { url: string; method: AllowedHttpMethod } | null {
  const code = res.statusCode;
  const location = res.headers.location;
  if (!code || !location || ![301, 302, 303, 307, 308].includes(code)) {
    return null;
  }
  const nextUrl = new URL(location, parsedBase).toString();
  const nextMethod =
    code === 303 || ((code === 301 || code === 302) && safeMethod === "POST")
      ? "GET"
      : safeMethod;
  return { url: nextUrl, method: nextMethod };
}

function createDecompressedStream(res: http.IncomingMessage): NodeJS.ReadableStream {
  const encoding = (res.headers["content-encoding"] || "").toLowerCase();
  switch (encoding) {
    case "gzip":
      return res.pipe(zlib.createGunzip());
    case "deflate":
      return res.pipe(zlib.createInflate());
    case "br":
      return res.pipe(zlib.createBrotliDecompress());
    default:
      return res;
  }
}

function parseResponseBodyData(chunks: Buffer[], contentTypeHeader = ""): any {
  const buf = Buffer.concat(chunks);
  const rawText = buf.toString("utf-8");
  const trimmed = rawText.trim();
  if (
    contentTypeHeader.toLowerCase().includes("json") ||
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      return JSON.parse(rawText);
    } catch {
      return rawText;
    }
  }
  return rawText;
}

function handleProxyResponse(
  res: http.IncomingMessage,
  parsed: URL,
  safeMethod: AllowedHttpMethod,
  options: { method?: string; headers?: Record<string, string>; body?: string; timeoutMs?: number },
  redirectCount: number,
  resolve: (value: any) => void,
  reject: (reason?: any) => void
): void {
  const redirect = getRedirectAction(res, parsed, safeMethod);
  if (redirect) {
    const redirectValidation = resolveProxyTargetUrl(redirect.url);
    if (redirectValidation.error || !redirectValidation.url) {
      return reject(new Error(`Redirect SSRF validation failed: ${redirectValidation.error}`));
    }
    return resolve(
      resilientProxyRequest(
        redirectValidation.url,
        { ...options, method: redirect.method },
        redirectCount + 1
      )
    );
  }

  const stream = createDecompressedStream(res);
  const chunks: Buffer[] = [];
  stream.on("data", (chunk: Buffer) => chunks.push(chunk));

  const onFinish = () => {
    const contentType = typeof res.headers["content-type"] === "string" ? res.headers["content-type"] : "";
    const parsedData = parseResponseBodyData(chunks, contentType);
    const statusCode = res.statusCode || 200;
    resolve({
      ok: statusCode >= 200 && statusCode < 300,
      status: statusCode,
      statusText: res.statusMessage || "OK",
      headers: res.headers,
      data: parsedData,
    });
  };

  stream.on("end", onFinish);
  stream.on("error", onFinish);
}

/**
 * Resilient HTTP/HTTPS client that handles compression, stream decoding,
 * redirects, and chunked encoding without strict Undici Content-Length mismatches.
 * Strictly validated against Server-Side Request Forgery (CWE-918).
 */
function resilientProxyRequest(
  urlStr: string,
  options: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    timeoutMs?: number;
  } = {},
  redirectCount = 0
): Promise<{
  ok: boolean;
  status: number;
  statusText: string;
  data: any;
  headers: IncomingHttpHeaders;
}> {
  return new Promise((resolve, reject) => {
    if (redirectCount > 5) {
      return reject(new Error("Too many redirects (maximum 5 redirects allowed)"));
    }

    const validated = resolveProxyTargetUrl(urlStr);
    if (validated.error || !validated.url) {
      return reject(new Error(`SSRF validation failed: ${validated.error || "Invalid URL"}`));
    }

    const parsed = new URL(validated.url);
    const transport = parsed.protocol === "https:" ? https : http;
    const safeMethod = sanitizeHttpMethod(options.method);
    const reqHeaders = prepareProxyRequestHeaders(options.headers, safeMethod, options.body);

    const req = transport.request(
      parsed,
      {
        method: safeMethod,
        headers: reqHeaders,
        timeout: options.timeoutMs || 300000,
      },
      (res) => handleProxyResponse(res, parsed, safeMethod, options, redirectCount, resolve, reject)
    );

    req.on("timeout", () => {
      req.destroy(new Error(`Request timed out after ${Math.round((options.timeoutMs || 300000) / 1000)} seconds`));
    });

    req.on("error", (err) => {
      reject(err);
    });

    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

interface InstallationConfig {
  installation_date: string;
  cutoff_datetime: string;
  cutoff_timestamp: number;
  installed_at: string;
  installed_at_utc?: string;
  note?: string;
}

function tryLoadExistingInstallationConfig(candidates: string[]): InstallationConfig | null {
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) {
        const raw = fs.readFileSync(candidate, "utf-8");
        const parsed = JSON.parse(raw);
        if (parsed.installation_date || parsed.cutoff_datetime) {
          return parsed;
        }
      }
    } catch {
      // ignore parse errors and proceed
    }
  }
  return null;
}

function writeInstallationConfigFile(target: string, configData: InstallationConfig, dateStr: string): void {
  try {
    const parentDir = path.dirname(target);
    if (parentDir && !fs.existsSync(parentDir)) {
      fs.mkdirSync(parentDir, { recursive: true });
    }
    fs.writeFileSync(target, JSON.stringify(configData, null, 2), "utf-8");
    console.log(`[Installation Date] Dynamically recorded initial installation config at '${target}': ${dateStr}`);
  } catch (err) {
    console.warn(`[Installation Date] Could not write config to '${target}':`, err);
  }
}

function ensureInstallationDateConfig(): InstallationConfig {
  const baseDir = process.cwd();
  const volumeDir = process.env.VOLUME_DIR || path.join(baseDir, "bookmarks");
  const candidates = [
    path.join(baseDir, "installation_date.json"),
    path.join(volumeDir, "installation_date.json"),
    path.join(baseDir, ".installation_date.json"),
    path.join(volumeDir, ".installation_date.json"),
  ];

  // 1. Check if an existing configuration exists (never overwrite on updates/restarts)
  const existing = tryLoadExistingInstallationConfig(candidates);
  if (existing) {
    return existing;
  }

  // 2. File does not exist: dynamically compute values from system clock at first start
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const dateStr = `${year}-${month}-${day}`;
  const cutoffDatetime = `${dateStr}T00:00:00`;
  const cutoffTimestamp = new Date(year, now.getMonth(), now.getDate(), 0, 0, 0).getTime() / 1000;

  const configData: InstallationConfig = {
    installation_date: dateStr,
    cutoff_datetime: cutoffDatetime,
    cutoff_timestamp: cutoffTimestamp,
    installed_at: now.toISOString(),
    installed_at_utc: now.toISOString(),
    note: `Immutable installation date created on first install. Bookmarks created prior to ${cutoffDatetime} are excluded from automated extraction.`,
  };

  // Save to baseDir and volumeDir
  const saveTargets = [candidates[0]];
  if (volumeDir && volumeDir !== baseDir) {
    saveTargets.push(candidates[1]);
  }

  for (const target of saveTargets) {
    writeInstallationConfigFile(target, configData, dateStr);
  }

  return configData;
}

function isValidHostname(hostname: string): boolean {
  if (!hostname || hostname.length > 253) return false;
  for (let i = 0; i < hostname.length; i++) {
    const code = hostname.codePointAt(i);
    if (code === undefined) return false;
    const isAlphanumeric =
      (code >= 48 && code <= 57) || // 0-9
      (code >= 65 && code <= 90) || // A-Z
      (code >= 97 && code <= 122);  // a-z
    const isPunctuation =
      code === 46 || // .
      code === 45 || // -
      code === 58 || // :
      code === 91 || // [
      code === 93;   // ]
    if (!isAlphanumeric && !isPunctuation) {
      return false;
    }
  }
  return true;
}

function buildSafeSidecarUrl(rawPath: string, allowedPrefixes?: string[]): string {
  const sidecarBase = getSidecarBaseUrl();
  const base = new URL(sidecarBase);
  if (base.protocol !== "http:" && base.protocol !== "https:") {
    throw new Error("Invalid sidecar protocol configuration");
  }

  let cleanPath = typeof rawPath === "string" ? rawPath.trim() : "/";
  while (cleanPath.startsWith("//") || cleanPath.startsWith("/\\")) {
    cleanPath = cleanPath.slice(1);
  }
  if (!cleanPath.startsWith("/")) {
    cleanPath = `/${cleanPath}`;
  }
  if (cleanPath.startsWith("/@")) {
    cleanPath = cleanPath.slice(2);
    if (!cleanPath.startsWith("/")) cleanPath = `/${cleanPath}`;
  }

  const parsed = new URL(cleanPath, "http://127.0.0.1");
  if (allowedPrefixes && allowedPrefixes.length > 0) {
    const matched = allowedPrefixes.some((prefix) => parsed.pathname.startsWith(prefix));
    if (!matched) {
      throw new Error(`SSRF: Unapproved sidecar path '${parsed.pathname}'`);
    }
  }

  const finalUrl = new URL(`${parsed.pathname}${parsed.search}`, base);
  if (finalUrl.origin !== base.origin) {
    throw new Error("SSRF: Target origin mismatch detected");
  }
  return finalUrl.toString();
}

function isSidecarEndpoint(pathname: string, port: string, sidecarPort: string): boolean {
  if (port === sidecarPort) return true;
  const sidecarPrefixes = [
    "/api/cutoff-config",
    "/api/installation-date",
    "/api/user/bookmarks",
    "/api/user/sync",
    "/api/sync",
    "/api/snippet",
    "/api/user/snippet"
  ];
  return sidecarPrefixes.some((prefix) => pathname.startsWith(prefix));
}

function validateTargetUrlSecurity(parsedUrl: URL): string | null {
  if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
    return "Invalid protocol. Only http and https are allowed.";
  }
  if (parsedUrl.username || parsedUrl.password) {
    return "User credentials are not allowed in targetUrl.";
  }
  const hostname = parsedUrl.hostname;
  if (!hostname || !isValidHostname(hostname)) {
    return "Invalid hostname format.";
  }
  if (isForbiddenTargetHost(hostname)) {
    return "Access to private metadata addresses is strictly forbidden.";
  }
  if (parsedUrl.port) {
    const portNum = Number(parsedUrl.port);
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) {
      return "Invalid target URL port.";
    }
  }
  return null;
}

function resolveProxyTargetUrl(rawTargetUrl?: string): { url?: string; error?: string } {
  if (!rawTargetUrl || typeof rawTargetUrl !== "string") {
    return { error: "targetUrl is required" };
  }

  let cleanTargetUrl = rawTargetUrl.trim();
  if (!cleanTargetUrl.startsWith("http://") && !cleanTargetUrl.startsWith("https://")) {
    cleanTargetUrl = `https://${cleanTargetUrl}`;
  }

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(cleanTargetUrl);
  } catch {
    return { error: "Invalid target URL structure." };
  }

  const securityError = validateTargetUrlSecurity(parsedUrl);
  if (securityError) {
    return { error: securityError };
  }

  const sidecarPort = process.env.SIDECAR_PORT || "13380";
  if (isSidecarEndpoint(parsedUrl.pathname, parsedUrl.port, sidecarPort)) {
    try {
      cleanTargetUrl = buildSafeSidecarUrl(`${parsedUrl.pathname}${parsedUrl.search}`);
    } catch {
      return { error: "Invalid sidecar target URL." };
    }
  } else {
    cleanTargetUrl = parsedUrl.toString();
  }

  return { url: cleanTargetUrl };
}

function sanitizeProxyHeaders(rawHeaders?: any): Record<string, string> {
  const safeHeaders: Record<string, string> = {};
  const ignoredHeaders = new Set(["host", "connection", "content-length", "keep-alive", "transfer-encoding"]);

  if (rawHeaders && typeof rawHeaders === "object") {
    for (const [k, v] of Object.entries(rawHeaders)) {
      if (typeof v === "string" && !ignoredHeaders.has(k.toLowerCase())) {
        safeHeaders[k] = v;
      }
    }
  }

  safeHeaders["User-Agent"] = safeHeaders["User-Agent"] || "Audiobookshelf-Bookmarks-Manager/2.1.0";
  safeHeaders["Accept"] = safeHeaders["Accept"] || "*/*";
  return safeHeaders;
}

function prepareProxyRequestBody(body: any, method: string, safeHeaders: Record<string, string>): string | undefined {
  if (body === undefined || body === null) return undefined;
  const normalizedMethod = method.toUpperCase();
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(normalizedMethod)) return undefined;

  const requestBody = typeof body === "string" ? body : JSON.stringify(body);
  if (!safeHeaders["Content-Type"]) {
    safeHeaders["Content-Type"] = "application/json";
  }
  return requestBody;
}

function formatProxyErrorMessage(err: unknown, targetUrl?: string): { message: string; hint: string } {
  const errObj = err as { message?: string; cause?: { message?: string; code?: string } };
  const causeText = errObj?.cause?.message || errObj?.cause?.code || "";
  let msg = errObj?.message || "Failed to reach remote server";
  if (causeText) {
    msg = `${msg} (${causeText})`;
  }

  let hint = "";
  try {
    const parsed = new URL(targetUrl || "");
    if (["localhost", "127.0.0.1", "0.0.0.0"].includes(parsed.hostname)) {
      hint = " Note: 'localhost' refers to this cloud container, not your client computer. Connect directly from your browser or use a public tunnel.";
    }
  } catch {}

  return { message: msg, hint };
}

function sanitizeExportTitle(raw?: unknown): string {
  if (typeof raw !== "string" || !raw) return "";
  let clean = "";
  for (const ch of raw) {
    if (ch !== "\0" && ch !== "\r" && ch !== "\n" && ch !== "\t" && ch !== "<" && ch !== ">" && ch !== "/" && ch !== "\\") {
      clean += ch;
    }
  }
  while (clean.includes("..")) {
    clean = clean.replaceAll("..", "");
  }
  return clean.trim();
}

function resolveExportFormat(raw?: unknown): "markdown" | "zip" {
  if (typeof raw !== "string") return "zip";
  const lower = raw.trim().toLowerCase();
  return lower === "markdown" || lower === "md" ? "markdown" : "zip";
}

function buildExportQueryString(query: express.Request["query"]): string {
  const safeParams = new URLSearchParams();
  const cleanTitle = sanitizeExportTitle(query.book_title);
  if (cleanTitle) {
    safeParams.set("book_title", cleanTitle);
  }
  safeParams.set("format", resolveExportFormat(query.format));
  if (typeof query.token === "string" && query.token.trim()) {
    safeParams.set("token", query.token.trim());
  }
  const qs = safeParams.toString();
  return qs ? `?${qs}` : "";
}

function extractExportForwardHeaders(req: express.Request): Record<string, string> {
  const forwardHeaders: Record<string, string> = {};
  if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
  if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;
  return forwardHeaders;
}

function pipeWebStreamToExpress(stream: any, res: express.Response): void {
  if (stream) {
    Readable.fromWeb(stream).pipe(res);
  } else {
    res.end();
  }
}

async function startServer() {
  // Ensure installation_date.json exists before app start, without overwriting on updates
  let installationConfig = ensureInstallationDateConfig();

  const app = express();
  app.disable("x-powered-by");

  // Port configuration:
  // - In AI Studio container dev server, MUST bind to 3000 for ingress proxy routing.
  // - In production (bundled dist/server or PM2), default to 13379 or process.env.PORT.
  const isBundled = (typeof __filename === "string" && __filename.endsWith(".cjs")) || (typeof import.meta.url === "string" && import.meta.url.includes("/dist/"));
  const isProduction = process.env.NODE_ENV === "production" || isBundled;
  const PORT = isProduction
    ? (Number(process.env.PORT) || 13379)
    : 3000;

  app.use(express.json());

  // API Proxy Route for Audiobookshelf:
  // Completely bypasses browser CORS restrictions by fetching server-to-server.
  app.post("/api/proxy/abs", async (req, res) => {
    // Allow up to 300 seconds (5 minutes) for heavy operations such as faster-whisper
    // model downloading, CPU speech-to-text inference on long audio clips, or cold starts.
    const PROXY_TIMEOUT_MS = Number(process.env.PROXY_TIMEOUT_MS) || 300000;

    try {
      const { targetUrl, method, headers = {}, body } = req.body;
      const safeMethod = sanitizeHttpMethod(method);
      const resolved = resolveProxyTargetUrl(targetUrl);
      if (resolved.error || !resolved.url) {
        return res.status(400).json({ error: resolved.error || "Invalid target URL" });
      }

      const safeHeaders = sanitizeProxyHeaders(headers);
      const requestBody = prepareProxyRequestBody(body, safeMethod, safeHeaders);

      const proxyResult = await resilientProxyRequest(resolved.url, {
        method: safeMethod,
        headers: safeHeaders,
        body: requestBody,
        timeoutMs: PROXY_TIMEOUT_MS,
      });

      return res.status(200).json({
        ok: proxyResult.ok,
        status: proxyResult.status,
        statusText: proxyResult.statusText,
        data: proxyResult.data,
      });
    } catch (err: unknown) {
      const { message, hint } = formatProxyErrorMessage(err, req.body?.targetUrl);
      console.warn(`[ABS Proxy] Connection warning for ${req.body?.targetUrl || "unknown"}: ${message}${hint}`);

      return res.status(502).json({
        ok: false,
        status: 502,
        error: "Proxy connection error",
        message: `${message}${hint}`,
      });
    }
  });

  // Streaming Media Proxy for Bookmarks: Audio (.mp3), Markdown (.md), and JSON metadata
  // Seamlessly proxies static media requests from client browsers to the FastAPI sidecar service.
  // Supports HTTP Range headers for audio seeking and streaming playback in the web player.
  const handleMediaStreamProxy = async (req: express.Request, res: express.Response) => {
    try {
      const targetUrl = buildSafeSidecarUrl(req.originalUrl, ["/bookmarks", "/snippets"]);
      const forwardHeaders: Record<string, string> = {};
      if (req.headers.range) {
        forwardHeaders["range"] = req.headers.range;
      }
      if (req.headers.authorization) {
        forwardHeaders["authorization"] = req.headers.authorization;
      }

      const sidecarRes = await fetch(targetUrl, {
        headers: forwardHeaders,
      });

      res.status(sidecarRes.status);
      sidecarRes.headers.forEach((value, key) => {
        res.setHeader(key, value);
      });
      // Prevent aggressive browser caching of re-clipped audio
      res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
      res.setHeader("Pragma", "no-cache");
      res.setHeader("Expires", "0");
      res.setHeader("Access-Control-Allow-Origin", "*");

      if (sidecarRes.body) {
        Readable.fromWeb(sidecarRes.body as any).pipe(res);
      } else {
        res.end();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Media proxy connection failure";
      res.status(502).json({ error: "Failed to stream media from sidecar", message: msg });
    }
  };
  app.get("/bookmarks/*", handleMediaStreamProxy);
  app.get("/snippets/*", handleMediaStreamProxy);

  // Export Proxy: streams ZIP and Markdown book exports from sidecar service
  const handleBookExportProxy = async (req: express.Request, res: express.Response) => {
    try {
      const queryString = buildExportQueryString(req.query);
      const targetUrl = buildSafeSidecarUrl(`/api/user/bookmarks/export-book${queryString}`, ["/api/user/bookmarks/export-book"]);
      const forwardHeaders = extractExportForwardHeaders(req);

      const sidecarRes = await fetch(targetUrl, { headers: forwardHeaders });
      res.status(sidecarRes.status);
      sidecarRes.headers.forEach((v, k) => res.setHeader(k, v));
      pipeWebStreamToExpress(sidecarRes.body, res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Book export proxy failed";
      res.status(502).json({ error: "Failed to stream book export from sidecar", message: msg });
    }
  };
  app.get("/api/export-book", handleBookExportProxy);
  app.get("/api/user/bookmarks/export-book", handleBookExportProxy);
  app.get("/api/snippets/export-book", handleBookExportProxy);
  app.get("/api/book/export", handleBookExportProxy);

  // Direct proxy for Snippet Expand/Re-clip
  const handleSnippetExpandProxy = async (req: express.Request, res: express.Response) => {
    try {
      const targetUrl = buildSafeSidecarUrl("/api/snippet/expand", ["/api/snippet/expand"]);
      const forwardHeaders: Record<string, string> = { "Content-Type": "application/json" };
      if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
      if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;

      const sidecarRes = await fetch(targetUrl, {
        method: "POST",
        headers: forwardHeaders,
        body: JSON.stringify(req.body),
      });

      const data = await sidecarRes.json();
      res.status(sidecarRes.status).json(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Snippet expand failed";
      res.status(502).json({ error: msg });
    }
  };
  app.post("/api/snippet/expand", handleSnippetExpandProxy);
  app.post("/api/snippets/expand", handleSnippetExpandProxy);

  // Direct proxy for Bookmarks Real-Time Status / Heartbeat
  const handleBookmarksStatusProxy = async (req: express.Request, res: express.Response) => {
    try {
      const targetUrl = buildSafeSidecarUrl("/api/user/bookmarks/status", ["/api/user/bookmarks/status"]);
      const forwardHeaders: Record<string, string> = {};
      if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
      if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;

      const sidecarRes = await fetch(targetUrl, { headers: forwardHeaders });
      const data = await sidecarRes.json();
      res.status(sidecarRes.status).json(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Status check failed";
      res.status(502).json({ error: msg });
    }
  };
  app.get("/api/user/bookmarks/status", handleBookmarksStatusProxy);
  app.get("/api/snippets/status", handleBookmarksStatusProxy);

  function buildForwardHeaders(req: express.Request): Record<string, string> {
    const forwardHeaders: Record<string, string> = {};
    if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
    if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;
    forwardHeaders["content-type"] = (req.headers["content-type"] as string) || "application/json";
    return forwardHeaders;
  }

  function computeFallbackCutoffConfig(mode: string, customDate: string | undefined): any {
    const cfg: any = { ...installationConfig, cutoff_mode: mode };
    if (mode === "from_start") {
      cfg.cutoff_timestamp = 0;
      cfg.cutoff_datetime = "1970-01-01T00:00:00";
      cfg.custom_date = null;
    } else if (mode === "custom_date" && customDate) {
      cfg.custom_date = customDate;
      cfg.cutoff_datetime = `${customDate}T00:00:00`;
      cfg.cutoff_timestamp = new Date(customDate).getTime() / 1000;
    } else if (mode === "from_now") {
      cfg.custom_date = null;
      cfg.cutoff_datetime = `${cfg.installation_date || new Date().toISOString().slice(0, 10)}T00:00:00`;
      cfg.cutoff_timestamp = new Date(cfg.installation_date || new Date()).getTime() / 1000;
    }
    return cfg;
  }

  function saveFallbackCutoffConfig(body: any): any {
    const mode = body?.cutoff_mode || "from_now";
    const customDate = body?.custom_date;
    const cfgTargets = [
      path.join(process.cwd(), "installation_date.json"),
      path.join(process.env.VOLUME_DIR || path.join(process.cwd(), "bookmarks"), "installation_date.json")
    ];
    const cfg = computeFallbackCutoffConfig(mode, customDate);
    for (const t of cfgTargets) {
      try {
        fs.writeFileSync(t, JSON.stringify(cfg, null, 2), "utf-8");
      } catch {}
    }
    installationConfig = cfg;
    return cfg;
  }

  function handleSidecarOfflineFallback(req: express.Request, res: express.Response, err: unknown): void {
    if (req.originalUrl.includes("cutoff-config") && req.method === "POST") {
      try {
        const cfg = saveFallbackCutoffConfig(req.body);
        res.json({
          status: "success",
          cutoff_mode: cfg.cutoff_mode,
          custom_date: cfg.custom_date,
          installation_date: cfg.installation_date,
          cutoff_datetime: cfg.cutoff_datetime,
          cutoff_timestamp: cfg.cutoff_timestamp,
          config: cfg,
          source: "saved_to_disk",
        });
        return;
      } catch (saveErr) {
        console.warn("Direct cutoff save notice:", saveErr);
      }
    }

    if (req.originalUrl.includes("installation-date") || req.originalUrl.includes("cutoff-config")) {
      res.json({
        status: "success",
        cutoff_mode: (installationConfig as any).cutoff_mode || "from_now",
        custom_date: (installationConfig as any).custom_date || null,
        installation_date: installationConfig.installation_date,
        cutoff_datetime: installationConfig.cutoff_datetime,
        cutoff_timestamp: installationConfig.cutoff_timestamp,
        config: installationConfig,
        source: "server_cache",
      });
      return;
    }

    const msg = err instanceof Error ? err.message : "Sync proxy failed";
    res.status(502).json({ error: msg });
  }

  async function forwardSidecarRequest(req: express.Request, res: express.Response): Promise<void> {
    const targetUrl = buildSafeSidecarUrl(req.originalUrl, ["/api/"]);
    const forwardHeaders = buildForwardHeaders(req);
    const safeMethod = sanitizeHttpMethod(req.method);

    const sidecarRes = await fetch(targetUrl, {
      method: safeMethod,
      headers: forwardHeaders,
      body: ["POST", "PUT"].includes(safeMethod) ? JSON.stringify(req.body) : undefined,
    });
    const rawText = await sidecarRes.text();
    let data: any = null;
    try {
      data = JSON.parse(rawText);
    } catch {
      data = { message: rawText };
    }

    if (req.originalUrl.includes("cutoff-config") && req.method === "POST" && data?.config) {
      installationConfig = data.config;
    }

    res.status(sidecarRes.status).json(data);
  }

  // Direct proxy for automated bookmark background sync & installation cutoff & snippet adjustments/retries
  const sidecarFallbackHandler = async (req: express.Request, res: express.Response) => {
    try {
      await forwardSidecarRequest(req, res);
    } catch (err: unknown) {
      handleSidecarOfflineFallback(req, res, err);
    }
  };
  const sidecarPrefixes = [
    "/api/user/bookmarks",
    "/api/user/bookmarks/*",
    "/api/bookmarks",
    "/api/bookmarks/*",
    "/api/snippet",
    "/api/user/snippet",
    "/api/user/sync-bookmarks",
    "/api/sync-bookmarks",
    "/api/user/sync-status",
    "/api/sync-status",
    "/api/installation-date",
    "/api/user/installation-date",
    "/api/cutoff-config",
    "/api/user/cutoff-config",
    "/api/snippet/expand",
    "/api/snippet/update",
    "/api/snippet/retry",
    "/api/user/snippet/retry",
    "/api/user/snippet/expand"
  ];
  for (const prefix of sidecarPrefixes) {
    app.all(prefix, sidecarFallbackHandler);
  }

  // System configuration endpoint: provides detected ports & server URLs
  app.get("/api/config", (req, res) => {
    // Reload latest .env dynamically if present on disk
    let currentEnv: Record<string, string> = {};
    try {
      const candidates = [
        path.join(process.cwd(), ".env"),
        "/srv/ssd/Appdata/local/audiobookshelf-bookmarks-manager/.env",
        "/srv/ssd/Appdata/local/Audiobookshelf-Bookmarks-Manager/.env"
      ];
      for (const c of candidates) {
        if (fs.existsSync(c)) {
          const parsed = dotenv.parse(fs.readFileSync(c));
          currentEnv = { ...currentEnv, ...parsed };
          break;
        }
      }
    } catch {}

    const normalizeUrl = (u: string) => {
      if (!u) return "";
      let t = stripTrailingSlash(u.trim());
      if (t.includes("abs.example.com")) return "";
      if (t && !t.startsWith("http://") && !t.startsWith("https://")) {
        if (t.startsWith("localhost") || t.startsWith("127.0.0.1") || t.startsWith("192.168.") || t.startsWith("10.")) {
          t = `http://${t}`;
        } else {
          t = `https://${t}`;
        }
      }
      return t;
    };

    const sidecarPort = currentEnv.SIDECAR_PORT || process.env.SIDECAR_PORT || 13380;
    const absServer = normalizeUrl(
      currentEnv.ABS_TARGET_SERVER || currentEnv.ABS_SERVER_URL || process.env.ABS_TARGET_SERVER || process.env.ABS_SERVER_URL || ""
    );
    let defaultAbsUrl = normalizeUrl(
      currentEnv.DEFAULT_ABS_URL || currentEnv.ABS_PUBLIC_URL || process.env.DEFAULT_ABS_URL || process.env.ABS_PUBLIC_URL || ""
    );
    if (!defaultAbsUrl) {
      defaultAbsUrl = absServer || "http://localhost:13378";
    }
    const sidecarUrl = currentEnv.SIDECAR_URL || process.env.SIDECAR_URL || `http://localhost:${sidecarPort}`;
    const useBackendProxy = (currentEnv.USE_BACKEND_PROXY || process.env.USE_BACKEND_PROXY)
      ? (currentEnv.USE_BACKEND_PROXY || process.env.USE_BACKEND_PROXY) !== "false"
      : true;
    res.json({
      ok: true,
      sidecarPort: Number(sidecarPort) || 13380,
      sidecarUrl,
      useBackendProxy,
      absTargetServer: absServer,
      defaultAbsUrl,
      webPort: PORT,
    });
  });

  // Health check endpoint
  app.get("/api/health", (req, res) => {
    res.json({ status: "ok" });
  });

  // Vite middleware for development vs static production build
  if (!isProduction) {
    const vite = await createViteServer({
      server: { middlewareMode: true, hmr: false },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.use((req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

await startServer();
