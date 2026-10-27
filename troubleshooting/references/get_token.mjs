#!/usr/bin/env node
/**
 * Troubleshooting Token 自动获取（飞书 OAuth 版）
 *
 * 通过飞书 OAuth 授权流程获取 Token，用户只需在浏览器中点击一次授权。
 * 1. 本地启动 HTTP 服务器接收 OAuth 回调
 * 2. 打开飞书授权页面
 * 3. 用户点击授权 → 飞书重定向到 localhost 带上 code
 * 4. 用 code 调微应用平台 prepare → confirm → 拿到 JWT
 *
 * 用法: node get_token.mjs [us|eu|staging-us|staging-eu] [--force-login]
 * stdout 仅返回通过目标身份校验的 JWT；不得裸运行或自动替换已注入的凭据。
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { homedir, platform } from 'node:os';
import path from 'node:path';

const REGION = process.argv[2] || 'us';
const FORCE_LOGIN = process.argv.slice(3).includes('--force-login');
const API_BASE_URL = `https://troubleshooting-${REGION}.addx.live`;
const PLATFORM_BASE_URL = 'https://micro-app-platform-us.addx.live';
const TOKEN_CACHE_FILE = path.join(homedir(), `.troubleshooting-token-${REGION}`);

// Micro App Platform/效能应用业务 OAuth；不得用于普通飞书资源操作
const FEISHU_APP_ID = 'cli_a818b4710778901c';
const OAUTH_CALLBACK_PORT = 9876;
const OAUTH_REDIRECT_URI = `http://localhost:${OAUTH_CALLBACK_PORT}/callback`;
const FEISHU_AUTH_URL = `https://open.feishu.cn/open-apis/authen/v1/index?client_id=${FEISHU_APP_ID}&response_type=code&redirect_uri=${encodeURIComponent(OAUTH_REDIRECT_URI)}`;
const TIMEOUT = 120_000;
const MAX_RESPONSE_BYTES = 256 * 1024;

// 只有本脚本生成的固定消息允许进入日志，不能转发上游正文/异常。
class AuthError extends Error {}
function safeErrorMessage(err) {
  return err instanceof AuthError ? err.message : '认证失败，请检查网络、权限或响应契约';
}

function readJsonResponse(res) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    res.on('error', () => reject(new AuthError('认证响应读取失败')));
    res.on('aborted', () => reject(new AuthError('认证响应中断')));
    res.on('data', (chunk) => {
      size += Buffer.byteLength(chunk);
      if (size > MAX_RESPONSE_BYTES) {
        reject(new AuthError('认证响应超过大小限制'));
        res.destroy();
        return;
      }
      chunks.push(Buffer.from(chunk));
    });
    res.on('end', () => {
      if (size > MAX_RESPONSE_BYTES) return;
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(new AuthError('认证响应不是有效 JSON')); }
    });
  });
}

function validIdentity(body) {
  if (!body || typeof body !== 'object') return false;
  // 兼容已有 success envelope 与 a4x APIResponse；显式失败不能被另一字段覆盖。
  const hasSuccess = Object.hasOwn(body, 'success');
  const hasErrorCode = Object.hasOwn(body, 'error_code');
  if (!hasSuccess && !hasErrorCode) return false;
  if (hasSuccess && body.success !== true) return false;
  if (hasErrorCode && body.error_code !== '') return false;
  if (Object.hasOwn(body, 'status_code') && body.status_code !== 200) return false;
  const userId = body.result?.data?.userid;
  return typeof userId === 'string' && userId.trim().length > 0;
}

// ─── Token 缓存 ─────────────────────────────────────────────
function readCachedToken() {
  try {
    if (existsSync(TOKEN_CACHE_FILE)) return readFileSync(TOKEN_CACHE_FILE, 'utf-8').trim();
  } catch {}
  return null;
}

function writeCachedToken(token) {
  try { writeFileSync(TOKEN_CACHE_FILE, token, 'utf-8'); } catch {}
}

/** 只有明确 HTTP 401 返回 false；其他失败抛错，不触发重新登录。 */
function validateToken(token) {
  return new Promise((resolve, reject) => {
    const req = https.get(`${API_BASE_URL}/api/current`, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: 5000,
    }, (res) => {
      if (res.statusCode === 401) {
        res.on('error', () => {});
        res.resume();
        resolve(false);
        return;
      }
      if (res.statusCode !== 200) {
        res.on('error', () => {});
        res.resume();
        reject(new AuthError(`身份校验 HTTP ${res.statusCode}；停止，不重新登录`));
        return;
      }
      readJsonResponse(res).then((body) => {
        if (!validIdentity(body)) throw new AuthError('身份校验业务失败或响应契约不匹配');
        resolve(true);
      }).catch(reject);
    });
    req.on('error', () => reject(new AuthError('身份校验网络错误；停止，不重新登录')));
    req.on('timeout', () => {
      reject(new AuthError('身份校验超时；停止，不重新登录'));
      req.destroy();
    });
  });
}

// ─── 在默认浏览器中打开 URL ────────────────────────────────
function openInDefaultBrowser(url) {
  const os = platform();
  if (os === 'darwin') {
    spawn('/usr/bin/open', [url], { stdio: 'ignore', detached: true }).unref();
  } else if (os === 'win32') {
    const rundll32 = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'rundll32.exe');
    spawn(rundll32, ['url.dll,FileProtocolHandler', url], { stdio: 'ignore', detached: true }).unref();
  } else {
    spawn('/usr/bin/xdg-open', [url], { stdio: 'ignore', detached: true }).unref();
  }
}

// ─── HTTPS 请求工具 ─────────────────────────────────────────
function httpsPost(url, data) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(data);
    const urlObj = new URL(url);
    const req = https.request({
      hostname: urlObj.hostname,
      path: urlObj.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 10_000,
    }, (res) => {
      if (res.statusCode !== 200) {
        res.on('error', () => {});
        res.resume();
        reject(new AuthError(`登录接口 HTTP ${res.statusCode}`));
        return;
      }
      readJsonResponse(res).then(resolve, reject);
    });
    req.on('error', () => reject(new AuthError('登录接口网络错误')));
    req.on('timeout', () => {
      reject(new AuthError('登录接口超时'));
      req.destroy();
    });
    req.write(body);
    req.end();
  });
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 飞书 OAuth 流程
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/** 用 OAuth code 通过微应用平台的 prepare + confirm 换取 JWT */
async function exchangeCodeForToken(code) {
  // Step 1: prepare
  const prepareResp = await httpsPost(`${PLATFORM_BASE_URL}/api/auth/login/prepare`, {
    code,
    redirect_uri: OAUTH_REDIRECT_URI,
  });

  const prepareToken = prepareResp?.data?.prepare_token;
  if (typeof prepareToken !== 'string' || !prepareToken.trim() || prepareResp.success === false) {
    throw new AuthError('prepare 失败：响应缺少有效登录凭据');
  }

  // Step 2: confirm
  const confirmResp = await httpsPost(`${PLATFORM_BASE_URL}/api/auth/login/confirm`, {
    identity_type: 'user',
    prepare_token: prepareToken,
  });

  const token = confirmResp?.data?.token;
  if (typeof token !== 'string' || !token.trim() || token.startsWith('tmt_') || confirmResp.success === false) {
    throw new AuthError('confirm 失败：响应缺少有效 JWT');
  }

  return token;
}

/** 启动本地服务器，打开飞书授权页，等待 OAuth 回调，自动换取 Token */
async function acquireTokenViaOAuth() {
  return new Promise((resolve) => {
    let timer;

    const done = (token) => {
      clearTimeout(timer);
      server.close();
      resolve(token);
    };

    const server = http.createServer(async (req, res) => {
      const url = new URL(req.url, `http://localhost:${OAUTH_CALLBACK_PORT}`);

      // 处理 OAuth 回调
      if (url.pathname === '/callback') {
        const code = url.searchParams.get('code');
        if (!code) {
          res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end('<h2>授权失败：未收到 code</h2>');
          return;
        }

        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end('<h2>授权成功，可以关闭此页面</h2>');

        try {
          process.stderr.write('INFO: 收到授权回调，正在换取 Token...\n');
          const token = await exchangeCodeForToken(code);
          process.stderr.write('INFO: Token 获取成功\n');
          done(token);
        } catch (err) {
          process.stderr.write(`ERROR: Token 换取失败: ${safeErrorMessage(err)}\n`);
          done(null);
        }
        return;
      }

      res.writeHead(404);
      res.end();
    });

    server.on('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        process.stderr.write(`ERROR: 端口 ${OAUTH_CALLBACK_PORT} 被占用\n`);
        resolve(null);
      }
    });

    server.listen(OAUTH_CALLBACK_PORT, '127.0.0.1', () => {
      process.stderr.write('INFO: 正在打开飞书授权页面...\n');
      openInDefaultBrowser(FEISHU_AUTH_URL);
    });

    timer = setTimeout(() => {
      server.close();
      process.stderr.write('ERROR: 授权超时\n');
      resolve(null);
    }, TIMEOUT);
  });
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 主流程
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function main() {
  if (!['us', 'eu', 'staging-us', 'staging-eu'].includes(REGION) ||
      process.argv.slice(3).some((arg) => arg !== '--force-login')) {
    throw new AuthError('用法：get_token.mjs [us|eu|staging-us|staging-eu] [--force-login]');
  }
  if (process.env.TROUBLESHOOTING_TOKEN) {
    throw new AuthError('已有注入凭据；本脚本不会自动将其替换为 JWT，请先确认凭据来源和登录授权');
  }
  // 第 1 步：检查缓存 Token
  const cached = FORCE_LOGIN ? null : readCachedToken();
  if (cached) {
    if (cached.startsWith('tmt_')) throw new AuthError('JWT 缓存中存在 PAT；停止，不发送凭据');
    const valid = await validateToken(cached);
    if (valid) {
      process.stdout.write(cached);
      return;
    }
    process.stderr.write('INFO: 缓存 JWT 返回 401，尝试一次重新登录\n');
  }

  // 第 2 步：飞书 OAuth 授权流程
  const token = await acquireTokenViaOAuth();

  if (token) {
    if (!await validateToken(token)) throw new AuthError('新 JWT 未通过目标身份校验；停止，不重试');
    writeCachedToken(token);
    process.stdout.write(token);
  } else {
    process.stderr.write('ERROR: Token 获取失败\n');
    process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`ERROR: ${safeErrorMessage(err)}\n`);
  process.exit(1);
});
