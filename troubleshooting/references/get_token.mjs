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
 * 用法: node get_token.mjs [us|eu]
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { homedir, platform } from 'node:os';
import path from 'node:path';

const REGION = process.argv[2] || 'us';
const API_BASE_URL = `https://troubleshooting-${REGION}.addx.live`;
const PLATFORM_BASE_URL = 'https://micro-app-platform-us.addx.live';
const TOKEN_CACHE_FILE = path.join(homedir(), `.troubleshooting-token-${REGION}`);

// Micro App Platform/效能应用业务 OAuth；不得用于普通飞书资源操作
const FEISHU_APP_ID = 'cli_a818b4710778901c';
const OAUTH_CALLBACK_PORT = 9876;
const OAUTH_REDIRECT_URI = `http://localhost:${OAUTH_CALLBACK_PORT}/callback`;
const FEISHU_AUTH_URL = `https://open.feishu.cn/open-apis/authen/v1/index?client_id=${FEISHU_APP_ID}&response_type=code&redirect_uri=${encodeURIComponent(OAUTH_REDIRECT_URI)}`;
const TIMEOUT = 120_000;

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

/** 用 /api/current 验证 Token 是否仍然有效 */
function validateToken(token) {
  return new Promise((resolve) => {
    const req = https.get(`${API_BASE_URL}/api/current`, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: 5000,
    }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
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
      let result = '';
      res.on('data', (c) => (result += c));
      res.on('end', () => {
        try { resolve(JSON.parse(result)); } catch { reject(new Error(`Invalid JSON: ${result.substring(0, 200)}`)); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });
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

  const prepareToken = prepareResp.data?.prepare_token;
  if (!prepareToken) {
    throw new Error(`prepare 失败: ${JSON.stringify(prepareResp).substring(0, 200)}`);
  }

  // Step 2: confirm
  const confirmResp = await httpsPost(`${PLATFORM_BASE_URL}/api/auth/login/confirm`, {
    identity_type: 'user',
    prepare_token: prepareToken,
  });

  const token = confirmResp.data?.token;
  if (!token) {
    throw new Error(`confirm 失败: ${JSON.stringify(confirmResp).substring(0, 200)}`);
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
          process.stderr.write(`ERROR: Token 换取失败: ${err.message}\n`);
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
  // 第 1 步：检查缓存 Token
  const cached = readCachedToken();
  if (cached) {
    const valid = await validateToken(cached);
    if (valid) {
      process.stdout.write(cached);
      return;
    }
    process.stderr.write('INFO: 缓存 Token 已过期，重新获取\n');
  }

  // 第 2 步：飞书 OAuth 授权流程
  const token = await acquireTokenViaOAuth();

  if (token) {
    writeCachedToken(token);
    process.stdout.write(token);
  } else {
    process.stderr.write('ERROR: Token 获取失败\n');
    process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`ERROR: ${err.message}\n`);
  process.exit(1);
});
