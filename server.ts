import express from "express";
import path from "path";
import fs from "fs";
import dotenv from "dotenv";
import http from "node:http";
import https from "node:https";
import zlib from "node:zlib";
import type { IncomingHttpHeaders } from "node:http";
import { createServer as createViteServer } from "vite";

dotenv.config();

/**
 * Resilient HTTP/HTTPS client that handles compression, stream decoding,
 * redirects, and chunked encoding without strict Undici Content-Length mismatches.
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

    const parsed = new URL(urlStr);
    const transport = parsed.protocol === "https:" ? https : http;
    const reqHeaders: Record<string, string> = { ...options.headers };

    // Request uncompressed identity stream by default to eliminate Content-Length / compression mismatches,
    // while still decoding gzip/deflate/br if an upstream proxy forces it.
    if (!reqHeaders["Accept-Encoding"] && !reqHeaders["accept-encoding"]) {
      reqHeaders["Accept-Encoding"] = "identity";
    }

    if (options.body && !reqHeaders["Content-Length"] && !reqHeaders["content-length"]) {
      reqHeaders["Content-Length"] = String(Buffer.byteLength(options.body, "utf-8"));
    } else if (
      !options.body &&
      ["POST", "PUT", "PATCH"].includes((options.method || "GET").toUpperCase()) &&
      !reqHeaders["Content-Length"] &&
      !reqHeaders["content-length"]
    ) {
      reqHeaders["Content-Length"] = "0";
    }

    const req = transport.request(
      parsed,
      {
        method: options.method || "GET",
        headers: reqHeaders,
        timeout: options.timeoutMs || 300000,
      },
      (res) => {
        // Handle HTTP redirects (301, 302, 303, 307, 308)
        if (
          res.statusCode &&
          [301, 302, 303, 307, 308].includes(res.statusCode) &&
          res.headers.location
        ) {
          const nextUrl = new URL(res.headers.location, parsed).toString();
          const nextMethod =
            res.statusCode === 303 ||
            ((res.statusCode === 301 || res.statusCode === 302) &&
              options.method === "POST")
              ? "GET"
              : options.method;
          return resolve(
            resilientProxyRequest(
              nextUrl,
              { ...options, method: nextMethod },
              redirectCount + 1
            )
          );
        }

        let stream: NodeJS.ReadableStream = res;
        const contentEncoding = (res.headers["content-encoding"] || "").toLowerCase();

        if (contentEncoding === "gzip") {
          stream = res.pipe(zlib.createGunzip());
        } else if (contentEncoding === "deflate") {
          stream = res.pipe(zlib.createInflate());
        } else if (contentEncoding === "br") {
          stream = res.pipe(zlib.createBrotliDecompress());
        }

        const chunks: Buffer[] = [];
        stream.on("data", (chunk: Buffer) => chunks.push(chunk));

        const finish = () => {
          const buf = Buffer.concat(chunks);
          const rawText = buf.toString("utf-8");
          const contentType = (res.headers["content-type"] || "").toLowerCase();
          let parsedData: any = rawText;

          if (contentType.includes("application/json")) {
            try {
              parsedData = JSON.parse(rawText);
            } catch {
              parsedData = rawText;
            }
          }

          resolve({
            ok: Boolean(res.statusCode && res.statusCode >= 200 && res.statusCode < 300),
            status: res.statusCode || 200,
            statusText: res.statusMessage || "OK",
            headers: res.headers,
            data: parsedData,
          });
        };

        stream.on("end", finish);
        stream.on("error", () => {
          finish();
        });
      }
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

function ensureInstallationDateConfig(): { installation_date: string; cutoff_datetime: string; cutoff_timestamp: number; installed_at: string } {
  const baseDir = process.cwd();
  const volumeDir = process.env.VOLUME_DIR || path.join(baseDir, "bookmarks");
  const candidates = [
    path.join(baseDir, "installation_date.json"),
    path.join(volumeDir, "installation_date.json"),
    path.join(baseDir, ".installation_date.json"),
    path.join(volumeDir, ".installation_date.json"),
  ];

  // 1. Check if an existing configuration exists (never overwrite on updates/restarts)
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

  // 2. File does not exist: dynamically compute values from system clock at first start
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  const dateStr = `${year}-${month}-${day}`;
  const cutoffDatetime = `${dateStr}T00:00:00`;
  const cutoffTimestamp = new Date(year, now.getMonth(), now.getDate(), 0, 0, 0).getTime() / 1000;

  const configData = {
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

  return configData;
}

async function startServer() {
  // Ensure installation_date.json exists before app start, without overwriting on updates
  const installationConfig = ensureInstallationDateConfig();

  const app = express();

  // Port configuration:
  // - In AI Studio container dev server, MUST bind to 3000 for ingress proxy routing.
  // - In production (bundled dist/server.cjs or PM2), default to 13379 or process.env.PORT.
  const isBundled = typeof __filename === "string" && __filename.endsWith(".cjs");
  const PORT = (process.env.NODE_ENV === "production" || isBundled)
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
      const { targetUrl, method = "GET", headers = {}, body } = req.body;
      if (!targetUrl || typeof targetUrl !== "string") {
        return res.status(400).json({ error: "targetUrl is required" });
      }

      // Auto-prefix protocol if omitted
      let cleanTargetUrl = targetUrl.trim();
      if (!cleanTargetUrl.startsWith("http://") && !cleanTargetUrl.startsWith("https://")) {
        cleanTargetUrl = `https://${cleanTargetUrl}`;
      }

      // Ensure valid URL
      const parsedUrl = new URL(cleanTargetUrl);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        return res.status(400).json({ error: "Invalid protocol. Only http and https are allowed." });
      }

      // Sanitize headers: remove hop-by-hop headers and host to avoid breaking upstream SNI/CORS
      const safeHeaders: Record<string, string> = {};
      if (headers && typeof headers === "object") {
        for (const [k, v] of Object.entries(headers)) {
          const lower = k.toLowerCase();
          if (!["host", "connection", "content-length", "keep-alive", "transfer-encoding"].includes(lower) && typeof v === "string") {
            safeHeaders[k] = v;
          }
        }
      }
      safeHeaders["User-Agent"] = safeHeaders["User-Agent"] || "Audiobookshelf-Bookmarks-Manager/2.0";
      safeHeaders["Accept"] = safeHeaders["Accept"] || "*/*";

      let requestBody: string | undefined = undefined;
      if (body !== undefined && body !== null && ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase())) {
        requestBody = typeof body === "string" ? body : JSON.stringify(body);
        if (!safeHeaders["Content-Type"]) {
          safeHeaders["Content-Type"] = "application/json";
        }
      }

      const proxyResult = await resilientProxyRequest(cleanTargetUrl, {
        method,
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
      const errObj = err as { name?: string; message?: string; cause?: { message?: string; code?: string } };
      const causeText = errObj?.cause?.message || errObj?.cause?.code || "";

      let msg = errObj?.message || "Failed to reach remote server";
      if (causeText) {
        msg = `${msg} (${causeText})`;
      }

      // Helpful context for localhost targets when running in cloud environments
      let hint = "";
      try {
        const parsed = new URL(req.body?.targetUrl || "");
        if (["localhost", "127.0.0.1", "0.0.0.0"].includes(parsed.hostname)) {
          hint = " Note: 'localhost' refers to this cloud container, not your client computer. Connect directly from your browser or use a public tunnel.";
        }
      } catch {}

      console.warn(`[ABS Proxy] Connection warning for ${req.body?.targetUrl || "unknown"}: ${msg}${hint}`);

      return res.status(502).json({
        ok: false,
        status: 502,
        error: "Proxy connection error",
        message: `${msg}${hint}`,
      });
    }
  });

  // Streaming Media Proxy for Bookmarks: Audio (.mp3), Markdown (.md), and JSON metadata
  // Seamlessly proxies static media requests from client browsers to the FastAPI sidecar service.
  // Supports HTTP Range headers for audio seeking and streaming playback in the web player.
  app.get(["/bookmarks/*", "/snippets/*"], async (req, res) => {
    try {
      const sidecarBase = (process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`).replace(/\/+$/, "");
      const targetUrl = `${sidecarBase}${req.originalUrl}`;
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
        const { Readable } = await import("stream");
        Readable.fromWeb(sidecarRes.body as any).pipe(res);
      } else {
        res.end();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Media proxy connection failure";
      res.status(502).json({ error: "Failed to stream media from sidecar", message: msg });
    }
  });

  // Export Proxy: streams ZIP and Markdown book exports from sidecar service
  app.get(["/api/export-book", "/api/user/bookmarks/export-book", "/api/snippets/export-book", "/api/book/export"], async (req, res) => {
    try {
      const sidecarBase = (process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`).replace(/\/+$/, "");
      const parsedUrl = new URL(req.url, 'http://localhost');
      // Forward to authoritative sidecar export route
      const targetUrl = `${sidecarBase}/api/user/bookmarks/export-book${parsedUrl.search}`;
      const forwardHeaders: Record<string, string> = {};
      if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
      if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;

      const sidecarRes = await fetch(targetUrl, { headers: forwardHeaders });
      res.status(sidecarRes.status);
      sidecarRes.headers.forEach((v, k) => res.setHeader(k, v));
      if (sidecarRes.body) {
        const { Readable } = await import("stream");
        Readable.fromWeb(sidecarRes.body as any).pipe(res);
      } else {
        res.end();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Book export proxy failed";
      res.status(502).json({ error: "Failed to stream book export from sidecar", message: msg });
    }
  });

  // Direct proxy for Snippet Expand/Re-clip
  app.post(["/api/snippet/expand", "/api/snippets/expand"], async (req, res) => {
    try {
      const sidecarBase = (process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`).replace(/\/+$/, "");
      const targetUrl = `${sidecarBase}/api/snippet/expand`;
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
  });

  // Direct proxy for Bookmarks Real-Time Status / Heartbeat
  app.get(["/api/user/bookmarks/status", "/api/snippets/status"], async (req, res) => {
    try {
      const sidecarBase = (process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`).replace(/\/+$/, "");
      const targetUrl = `${sidecarBase}/api/user/bookmarks/status`;
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
  });

  // Direct proxy for automated bookmark background sync & installation cutoff
  app.all(["/api/user/sync-bookmarks", "/api/sync-bookmarks", "/api/user/sync-status", "/api/sync-status", "/api/installation-date", "/api/user/installation-date", "/api/cutoff-config", "/api/user/cutoff-config"], async (req, res) => {
    try {
      const sidecarBase = (process.env.SIDECAR_URL || `http://127.0.0.1:${process.env.SIDECAR_PORT || 13380}`).replace(/\/+$/, "");
      const targetUrl = `${sidecarBase}${req.originalUrl}`;
      const forwardHeaders: Record<string, string> = {};
      if (req.headers.authorization) forwardHeaders["authorization"] = req.headers.authorization;
      if (req.headers["x-abs-server-url"]) forwardHeaders["x-abs-server-url"] = req.headers["x-abs-server-url"] as string;
      if (req.headers["content-type"]) forwardHeaders["content-type"] = req.headers["content-type"] as string;

      const sidecarRes = await fetch(targetUrl, {
        method: req.method,
        headers: forwardHeaders,
        body: ["POST", "PUT"].includes(req.method) ? JSON.stringify(req.body) : undefined,
      });
      const data = await sidecarRes.json();
      res.status(sidecarRes.status).json(data);
    } catch (err: unknown) {
      // If the sidecar is temporarily starting or offline and the client requests installation-date or cutoff-config, return cached config
      if (req.originalUrl.includes("installation-date") || req.originalUrl.includes("cutoff-config")) {
        return res.json({
          status: "success",
          cutoff_mode: (installationConfig as any).cutoff_mode || "from_now",
          custom_date: (installationConfig as any).custom_date || null,
          installation_date: installationConfig.installation_date,
          cutoff_datetime: installationConfig.cutoff_datetime,
          cutoff_timestamp: installationConfig.cutoff_timestamp,
          config: installationConfig,
          source: "server_cache",
        });
      }
      const msg = err instanceof Error ? err.message : "Sync proxy failed";
      res.status(502).json({ error: msg });
    }
  });

  // System configuration endpoint: provides detected ports & server URLs
  app.get("/api/config", (req, res) => {
    const sidecarPort = process.env.SIDECAR_PORT || 13380;
    const absServer = process.env.ABS_TARGET_SERVER || process.env.ABS_SERVER_URL || "";
    const defaultAbsUrl = (process.env.DEFAULT_ABS_URL || process.env.ABS_PUBLIC_URL || absServer || "").trim();
    const sidecarUrl = process.env.SIDECAR_URL || `http://localhost:${sidecarPort}`;
    const useBackendProxy = process.env.USE_BACKEND_PROXY ? process.env.USE_BACKEND_PROXY !== "false" : true;
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

  // Vite middleware for development
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
