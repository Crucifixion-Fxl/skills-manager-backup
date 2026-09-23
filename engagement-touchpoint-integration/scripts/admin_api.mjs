#!/usr/bin/env node
/**
 * Authenticated engagement-admin API client.
 *
 * Authentication is completed in the system default browser. The Admin
 * redirects a short-lived PKCE grant to a temporary 127.0.0.1 listener; the
 * helper exchanges it for an eight-hour CLI Bearer token and then calls the
 * Admin API directly. Browser cookies are never read or exported.
 *
 * Usage:
 *   node admin_api.mjs <prod|staging> <METHOD> <PATH> [JSON|@file]
 *     [--confirm-write=<environment>:<METHOD>:<normalized-path>]
 *     [--confirm-admin-staging-instance]
 *     [--confirm-publish]
 */

import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import http from "node:http";
import { homedir, platform } from "node:os";
import path from "node:path";

const ENV_BASE_URLS = {
  prod: "https://engagement-admin.addx.live",
  staging: "https://engagement-admin-staging.addx.live",
};

const AUTH_TIMEOUT_MS = 120_000;
const REQUEST_TIMEOUT_MS = 20_000;

function usage(exitCode = 0) {
  const out = exitCode === 0 ? process.stdout : process.stderr;
  out.write(
    [
      "Usage: node admin_api.mjs <prod|staging> <METHOD> <PATH> [JSON|@file]",
      "",
      "Environment:",
      "  prod     production Admin; use for normal touchpoint CRUD and PublishOrders",
      "  staging  Admin app pre-release only; writes require --confirm-admin-staging-instance",
      "",
      "Examples:",
      "  node admin_api.mjs prod GET '/api/touchpoints?q=vh_home_banner'",
      "  node admin_api.mjs prod POST '/api/touchpoints/vh_home_banner/experience-variants' @payload.json \\",
      "    --confirm-write='prod:POST:/api/touchpoints/vh_home_banner/experience-variants'",
    ].join("\n") + "\n",
  );
  process.exit(exitCode);
}

function parseBody(raw) {
  if (raw == null) return undefined;
  const text = raw.startsWith("@")
    ? readFileSync(path.resolve(raw.slice(1)), "utf8")
    : raw;
  return JSON.parse(text);
}

// Exact-match credential keys (case-insensitive) redacted from response
// bodies. Business fields such as `code` (voucher/promo codes operators must
// read) and `token_type` are intentionally NOT in this set.
const SENSITIVE_KEYS = new Set([
  "access_token",
  "admin_session",
  "api_key",
  "authorization",
  "cookie",
  "id_token",
  "password",
  "refresh_token",
  "secret",
  "set-cookie",
  "token",
]);

// String form of the same credential keys. Longest-first alternation keeps
// `set-cookie` from matching its `cookie` suffix. Business fields (`code`,
// `token_type`, `next_token`, ...) are not exact members of SENSITIVE_KEYS
// and therefore stay readable in string leaves.
const SENSITIVE_STRING_KEYS = [...SENSITIVE_KEYS]
  .sort((a, b) => b.length - a.length)
  .join("|");

function sanitizeText(text) {
  return text
    // `key=value` and JSON-ish `"key":"value"` token forms: redact only the
    // token value, keeping the surrounding content readable.
    .replace(
      new RegExp(
        `("?\\b(${SENSITIVE_STRING_KEYS})"?\\s*[:=]\\s*"?)` +
          "[A-Za-z0-9._~+/=-]+",
        "gi",
      ),
      "$1[REDACTED]",
    )
    // Header/prose colon form (`cookie: ...`, `authorization: Bearer ...`):
    // redact the remainder of the line. Values that start with a quote are
    // JSON strings already handled by the token rule above.
    .replace(
      new RegExp(`\\b(${SENSITIVE_STRING_KEYS})("?\\s*:\\s*)[^"\\r\\n]+`, "gi"),
      "$1$2[REDACTED]",
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [REDACTED]");
}

function sanitizeErrorMessage(error) {
  const message = error instanceof Error ? error.message : String(error);
  return sanitizeText(message);
}

// The write-confirmation preview goes to stderr, which callers capture into
// transcripts and logs just like stdout. Credential values are redacted with
// the same sanitizeValue pass used for response bodies; business fields stay
// readable so operators can still verify what will be sent.
function renderWritePreview(environment, method, normalizedPath, body) {
  return (
    `WRITE PREVIEW: ${environment} ${method} ${normalizedPath}\n` +
    `${JSON.stringify(sanitizeValue(body ?? null), null, 2)}\n`
  );
}

function sanitizeValue(value) {
  if (typeof value === "string") return sanitizeText(value);
  if (Array.isArray(value)) return value.map(sanitizeValue);
  if (value !== null && typeof value === "object") {
    const sanitized = {};
    for (const [key, entry] of Object.entries(value)) {
      sanitized[key] = SENSITIVE_KEYS.has(key.toLowerCase())
        ? "[REDACTED]"
        : sanitizeValue(entry);
    }
    return sanitized;
  }
  return value;
}

// The CLI credential deliberately has no production-promotion capability.
// approve-prod, refresh-approval, rollback, approval callbacks, and audit
// export are NOT in this allowlist (see SKILL.md / admin-api-auth.md);
// production promotion must be performed by the user in the Admin web UI.
// Touchpoint CRUD/lookup plus publish-order creation are the full surface.
function apiPathAllowed(method, pathname) {
  if (
    pathname === "/api/touchpoints" ||
    pathname.startsWith("/api/touchpoints/")
  ) {
    return true;
  }
  if (pathname === "/api/publish-orders") return true;
  return method === "GET" && pathname.startsWith("/api/publish-orders/");
}

function ensurePrivateDirectory(directory) {
  if (!existsSync(directory)) {
    mkdirSync(directory, { recursive: true, mode: 0o700 });
  }
  const linkStat = lstatSync(directory);
  if (!linkStat.isDirectory() || linkStat.isSymbolicLink()) {
    throw new Error("token cache path must be a real directory, not a symlink");
  }
  const realPath = realpathSync(directory);
  const fileStat = statSync(realPath);
  if (typeof process.getuid === "function" && fileStat.uid !== process.getuid()) {
    throw new Error("token cache directory is not owned by the current user");
  }
  chmodSync(realPath, 0o700);
  return realPath;
}

function readCachedCredential(cacheFile) {
  if (!existsSync(cacheFile)) return null;
  const linkStat = lstatSync(cacheFile);
  if (!linkStat.isFile() || linkStat.isSymbolicLink()) {
    throw new Error("token cache file must be a regular file, not a symlink");
  }
  const fileStat = statSync(cacheFile);
  if (typeof process.getuid === "function" && fileStat.uid !== process.getuid()) {
    throw new Error("token cache file is not owned by the current user");
  }
  chmodSync(cacheFile, 0o600);

  try {
    const parsed = JSON.parse(readFileSync(cacheFile, "utf8"));
    if (
      typeof parsed?.accessToken !== "string" ||
      parsed.accessToken.length === 0 ||
      typeof parsed?.expiresAt !== "number"
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeCachedCredential(cacheFile, credential) {
  const tempFile = `${cacheFile}.${process.pid}.${randomBytes(6).toString("hex")}.tmp`;
  try {
    writeFileSync(tempFile, `${JSON.stringify(credential)}\n`, {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    renameSync(tempFile, cacheFile);
    chmodSync(cacheFile, 0o600);
  } catch (error) {
    try {
      if (existsSync(tempFile)) unlinkSync(tempFile);
    } catch {
      // Preserve the original write failure.
    }
    throw error;
  }
}

function base64Url(bytes) {
  return Buffer.from(bytes).toString("base64url");
}

function createPkce() {
  const verifier = base64Url(randomBytes(48));
  const challenge = base64Url(createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

function openInDefaultBrowser(url) {
  const os = platform();
  let command;
  let args;
  if (os === "darwin") {
    command = "/usr/bin/open";
    args = [url];
  } else if (os === "win32") {
    command = path.join(
      process.env.SystemRoot || "C:\\Windows",
      "System32",
      "rundll32.exe",
    );
    args = ["url.dll,FileProtocolHandler", url];
  } else {
    command = "/usr/bin/xdg-open";
    args = [url];
  }
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "ignore", detached: true });
    child.once("error", reject);
    child.once("spawn", () => {
      child.unref();
      resolve();
    });
  });
}

function startLoopbackServer(expectedState) {
  let settled = false;
  let resolveCode;
  let rejectCode;
  const codePromise = new Promise((resolve, reject) => {
    resolveCode = resolve;
    rejectCode = reject;
  });

  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    if (url.pathname !== "/callback") {
      res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      res.end("Not found");
      return;
    }

    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    if (!code || state !== expectedState) {
      res.writeHead(400, {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-security-policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        "referrer-policy": "no-referrer",
      });
      res.end("<h2>授权失败：回调参数无效</h2>");
      return;
    }

    res.writeHead(200, {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
    });
    res.end("<h2>Engagement Admin API 授权成功，可以关闭此标签页</h2>");
    if (!settled) {
      settled = true;
      resolveCode(code);
    }
  });

  const ready = new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("failed to allocate a loopback callback port"));
        return;
      }
      resolve(address.port);
    });
  });

  return {
    ready,
    codePromise,
    close() {
      if (!settled) {
        settled = true;
        rejectCode(new Error("authorization listener closed"));
      }
      server.close();
    },
  };
}

async function fetchWithTimeout(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    redirect: "manual",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const text = await response.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = text;
  }
  return { response, body };
}

async function validateCachedCredential(baseUrl, credential) {
  if (credential.expiresAt <= Date.now() + 30_000) return null;
  const { response, body } = await fetchWithTimeout(
    `${baseUrl}/api/auth/cli/me`,
    {
      headers: {
        accept: "application/json",
        authorization: `Bearer ${credential.accessToken}`,
      },
    },
  );
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new Error(`CLI token validation failed: HTTP ${response.status}`);
  }
  return body;
}

async function assertCliAuthAvailable(baseUrl) {
  const { response } = await fetchWithTimeout(`${baseUrl}/api/auth/cli/me`, {
    headers: { accept: "application/json" },
  });
  if (response.status !== 401) {
    throw new Error(
      `Admin CLI authorization is not available on this deployment (probe HTTP ${response.status}); ` +
        "deploy the engagement-admin CLI auth endpoints before retrying",
    );
  }
}

async function exchangeGrant(baseUrl, code, verifier, redirectUri) {
  const { response, body } = await fetchWithTimeout(
    `${baseUrl}/api/auth/cli/exchange`,
    {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        code,
        code_verifier: verifier,
        redirect_uri: redirectUri,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(`CLI authorization exchange failed: HTTP ${response.status}`);
  }
  if (
    typeof body?.access_token !== "string" ||
    body.token_type !== "Bearer" ||
    typeof body.expires_in !== "number" ||
    !Number.isFinite(body.expires_in) ||
    body.expires_in <= 0 ||
    typeof body.user !== "object" ||
    body.user === null
  ) {
    throw new Error("CLI authorization exchange returned an invalid response");
  }
  return {
    accessToken: body.access_token,
    expiresAt: Date.now() + body.expires_in * 1000,
    user: body.user,
  };
}

async function acquireCredential(baseUrl) {
  const state = base64Url(randomBytes(32));
  const { verifier, challenge } = createPkce();
  const listener = startLoopbackServer(state);
  const port = await listener.ready;
  const redirectUri = `http://127.0.0.1:${port}/callback`;
  const authorizeUrl = new URL("/api/auth/cli/authorize", baseUrl);
  authorizeUrl.searchParams.set("redirect_port", String(port));
  authorizeUrl.searchParams.set("state", state);
  authorizeUrl.searchParams.set("code_challenge", challenge);
  authorizeUrl.searchParams.set("code_challenge_method", "S256");

  let timeout;
  try {
    process.stderr.write(
      "INFO: 正在使用系统默认浏览器完成 Engagement Admin 授权；会复用默认浏览器的登录状态\n",
    );
    await openInDefaultBrowser(authorizeUrl.toString());
    const code = await Promise.race([
      listener.codePromise,
      new Promise((_, reject) => {
        timeout = setTimeout(
          () => reject(new Error("Feishu OAuth timed out after 120 seconds")),
          AUTH_TIMEOUT_MS,
        );
      }),
    ]);
    return await exchangeGrant(baseUrl, code, verifier, redirectUri);
  } finally {
    clearTimeout(timeout);
    listener.close();
  }
}

function resolveOperatorIdentity(operator) {
  const candidates = [operator?.email, operator?.openId, operator?.name];
  const identity = candidates.find(
    (value) =>
      typeof value === "string" && /^[A-Za-z0-9._@+-]{1,64}$/.test(value),
  );
  if (!identity) {
    throw new Error(
      "CLI authorization returned no ASCII-safe email, openId, or name for the operator header",
    );
  }
  return identity;
}

async function getCredential(environment, baseUrl) {
  const cacheDirectory = ensurePrivateDirectory(
    path.join(homedir(), ".engagement-admin-api", environment),
  );
  const cacheFile = path.join(cacheDirectory, "cli-token.json");
  const cached = readCachedCredential(cacheFile);
  if (cached) {
    const user = await validateCachedCredential(baseUrl, cached);
    if (user) return { ...cached, user };
    process.stderr.write("INFO: 缓存的 CLI Token 已过期，重新授权\n");
  }

  await assertCliAuthAvailable(baseUrl);
  const credential = await acquireCredential(baseUrl);
  writeCachedCredential(cacheFile, credential);
  return credential;
}

async function main() {
  if (process.argv.includes("--help") || process.argv.includes("-h")) usage(0);

  const args = process.argv.slice(2);
  const flags = args.filter((arg) => arg.startsWith("--"));
  const positionals = args.filter((arg) => !arg.startsWith("--"));
  const [environment, rawMethod, requestPath, rawBody] = positionals;
  if (!environment || !rawMethod || !requestPath) usage(2);

  const baseUrl = ENV_BASE_URLS[environment];
  if (!baseUrl) throw new Error("environment must be prod or staging");

  const method = rawMethod.toUpperCase();
  if (!["GET", "POST", "PATCH", "DELETE"].includes(method)) {
    throw new Error("METHOD must be GET, POST, PATCH, or DELETE");
  }
  const requestUrl = new URL(requestPath, baseUrl);
  const expectedOrigin = new URL(baseUrl).origin;
  if (
    requestUrl.origin !== expectedOrigin ||
    !requestUrl.pathname.startsWith("/api/") ||
    requestUrl.username ||
    requestUrl.password ||
    requestUrl.hash
  ) {
    throw new Error(
      "PATH must normalize to a same-origin /api/... URL without credentials or hash",
    );
  }
  if (requestUrl.pathname.startsWith("/api/auth/cli/")) {
    throw new Error("PATH is reserved for helper-managed CLI authorization");
  }
  if (!apiPathAllowed(method, requestUrl.pathname)) {
    throw new Error(
      "PATH is outside the CLI helper's API allowlist: only touchpoint CRUD/lookup " +
        "and publish-order creation (POST /api/publish-orders) are allowed. " +
        "Production promotion (approve-prod, refresh-approval), rollback, approval " +
        "callbacks, and audit export must be performed by the user in the Admin web UI",
    );
  }

  const body = parseBody(rawBody);
  if (method === "GET" && body !== undefined) {
    throw new Error(`${method} requests must not include a JSON body`);
  }
  if (["POST", "PATCH"].includes(method) && body === undefined) {
    throw new Error(`${method} requests require a JSON body`);
  }

  const isWrite = ["POST", "PATCH", "DELETE"].includes(method);
  const normalizedPath = `${requestUrl.pathname}${requestUrl.search}`;
  if (isWrite) {
    if (
      environment === "staging" &&
      !flags.includes("--confirm-admin-staging-instance")
    ) {
      throw new Error(
        "Admin staging instance write blocked; normal touchpoint operations use the prod Admin host, " +
          "which creates staging rules through PublishOrders. Pass --confirm-admin-staging-instance " +
          "only when the user explicitly requests testing the Admin app staging deployment",
      );
    }
    const requiredConfirmation =
      `--confirm-write=${environment}:${method}:${normalizedPath}`;
    if (!flags.includes(requiredConfirmation)) {
      throw new Error(
        `write blocked; after GET preflight and user payload approval, pass ${requiredConfirmation}`,
      );
    }
    if (
      requestUrl.pathname.startsWith("/api/publish-orders") &&
      !flags.includes("--confirm-publish")
    ) {
      throw new Error(
        "publish-order write blocked; pass --confirm-publish after separate user approval",
      );
    }
    process.stderr.write(
      renderWritePreview(environment, method, normalizedPath, body),
    );
  }

  const credential = await getCredential(environment, baseUrl);
  const operatorIdentity = resolveOperatorIdentity(credential.user);
  const headers = {
    accept: "application/json",
    authorization: `Bearer ${credential.accessToken}`,
    "content-type": "application/json",
    "x-requested-with": "XMLHttpRequest",
    "x-engagement-admin-operator": operatorIdentity,
  };
  const { response, body: responseBody } = await fetchWithTimeout(
    requestUrl.toString(),
    {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    },
  );

  process.stdout.write(
    JSON.stringify(
      {
        ok: response.ok,
        status: response.status,
        method,
        path: normalizedPath,
        operator: operatorIdentity,
        body: sanitizeValue(responseBody),
      },
      null,
      2,
    ) + "\n",
  );
  if (!response.ok) process.exitCode = 1;
}

const invokedAsScript =
  typeof process.argv[1] === "string" &&
  typeof import.meta.filename === "string" &&
  realpathSync(process.argv[1]) === import.meta.filename;

if (invokedAsScript) {
  main().catch((error) => {
    process.stderr.write(`ERROR: ${sanitizeErrorMessage(error)}\n`);
    process.exit(1);
  });
}

export {
  apiPathAllowed,
  renderWritePreview,
  sanitizeErrorMessage,
  sanitizeText,
  sanitizeValue,
};
