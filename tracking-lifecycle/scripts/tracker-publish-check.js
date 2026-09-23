#!/usr/bin/env node

/**
 * 埋点发布状态校验工具（CI 用）
 *
 * 用途：在 MR / PR review 阶段，校验代码引用的埋点点位 + 版本号
 * 是否已经在埋点平台（Tracker Manager）发布到线上。未发布即引用 = 卡住合并。
 *
 * 用法：
 *   TMT_TOKEN=tmt_xxx node tracker-publish-check.js \
 *     --application=<app-point> \
 *     --version=<v> \
 *     --specs=<point1,point2,point3>
 *
 *   可选：
 *     --base-url=<url>          默认 https://us-analytics-management.theunismart.com
 *     --token-env=<NAME>        默认 TMT_TOKEN
 *
 * 输入语义：
 *   --application  应用 point（EventInfo 表中应用记录的 point 字段，例如 smart_camera）
 *   --version      埋点版本号
 *   --specs        本次 MR 变更的埋点点位列表（逗号分隔）
 *                  提取来源（CI/调用方负责）：yaml diff、代码 diff 中的
 *                  track()/logEvent()/事件常量引用 — 不限定来源，脚本只消费三元组
 *
 * 校验输出 — 把 specs 分到三类：
 *   published           已在该版本发布到线上
 *   definedUnreleased   EventInfo 已定义，但未进入该版本的发布工单
 *   undefinedOnPlatform 平台 EventInfo 表里完全没这个 point
 *
 * 退出码：
 *   0 — 所有 specs 在该版本 published（放行）
 *   1 — 至少 1 个 spec 落入 definedUnreleased / undefinedOnPlatform（卡住合并）
 *   2 — 该版本尚未 RELEASED（卡住合并；输出仍含三类分桶供发布后参考）
 *   3 — 参数错误
 *   4 — API / 网络 / 鉴权错误
 */

const https = require('https');
const http = require('http');

const DEFAULT_BASE_URL = 'https://us-analytics-management.theunismart.com';
const RELEASED_STATUS_CODE = 3; // tracker.management.enums.ReleaseStatusEnum.RELEASED

// --- 参数解析 ---

function parseArgs() {
  const args = {};
  process.argv.slice(2).forEach(arg => {
    if (arg.startsWith('--')) {
      const eq = arg.indexOf('=', 2);
      if (eq > -1) {
        args[arg.slice(2, eq)] = arg.slice(eq + 1);
      } else {
        args[arg.slice(2)] = true;
      }
    }
  });
  return args;
}

// --- HTTP 请求 ---

function request(url, method, headers) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const lib = parsed.protocol === 'http:' ? http : https;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname + (parsed.search || ''),
      method,
      headers: { 'Content-Type': 'application/json', ...headers },
    };

    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        let parsedBody;
        try {
          parsedBody = JSON.parse(data);
        } catch {
          parsedBody = data;
        }
        resolve({ status: res.statusCode, body: parsedBody });
      });
    });

    req.on('error', reject);
    req.end();
  });
}

// --- 主流程 ---

async function main() {
  const args = parseArgs();

  // 1. 参数校验
  const missingArgs = ['application', 'version', 'specs'].filter(k => !args[k]);
  if (missingArgs.length > 0) {
    console.error(JSON.stringify({
      error: `缺少必须参数: ${missingArgs.join(', ')}`,
      usage: 'node tracker-publish-check.js --application=<app> --version=<v> --specs=<p1,p2>',
    }));
    process.exit(3);
  }

  const application = args.application;
  const version = args.version;
  const requestedSpecs = String(args.specs).split(',').map(s => s.trim()).filter(Boolean);

  if (requestedSpecs.length === 0) {
    console.error(JSON.stringify({ error: '--specs 不能为空' }));
    process.exit(3);
  }

  const baseUrl = args['base-url'] || DEFAULT_BASE_URL;
  const tokenEnv = args['token-env'] || 'TMT_TOKEN';
  const token = process.env[tokenEnv];
  if (!token) {
    console.error(JSON.stringify({ error: `环境变量 ${tokenEnv} 未设置` }));
    process.exit(3);
  }

  // 2. 并发调平台：getPublishedEvents + getAllApplication
  const headers = { Authorization: `Bearer ${token}` };
  const publishedUrl = `${baseUrl}/api/release/getPublishedEvents?application=${encodeURIComponent(application)}&version=${encodeURIComponent(version)}`;
  const appsUrl = `${baseUrl}/api/info/getAllApplication`;

  let publishedResp, appsResp;
  try {
    [publishedResp, appsResp] = await Promise.all([
      request(publishedUrl, 'GET', headers),
      request(appsUrl, 'GET', headers),
    ]);
  } catch (err) {
    console.error(JSON.stringify({ error: `平台请求失败: ${err.message}` }));
    process.exit(4);
  }

  for (const r of [publishedResp, appsResp]) {
    if (r.status === 401 || r.status === 403) {
      console.error(JSON.stringify({ error: `鉴权失败 (HTTP ${r.status})`, hint: `检查 ${tokenEnv} 是否有效` }));
      process.exit(4);
    }
    if (r.status !== 200 || !r.body || r.body.code !== 200) {
      console.error(JSON.stringify({
        error: '平台返回异常',
        httpStatus: r.status,
        apiCode: r.body && r.body.code,
        apiMessage: r.body && r.body.errorMessage,
      }));
      process.exit(4);
    }
  }

  const publishedData = publishedResp.body.data || {};
  const appRecord = (appsResp.body.data || []).find(a => a.point === application);
  if (!appRecord) {
    console.error(JSON.stringify({ error: `应用 point=${application} 在平台未注册` }));
    process.exit(4);
  }

  // 3. 拉取该应用所有已定义事件，构造 definedSet
  const searchUrl = `${baseUrl}/api/info/search?applicationId=${appRecord.id}&pageSize=1000`;
  let searchResp;
  try {
    searchResp = await request(searchUrl, 'GET', headers);
  } catch (err) {
    console.error(JSON.stringify({ error: `info/search 请求失败: ${err.message}` }));
    process.exit(4);
  }
  if (searchResp.status !== 200 || !searchResp.body || searchResp.body.code !== 200) {
    console.error(JSON.stringify({
      error: 'info/search 返回异常',
      httpStatus: searchResp.status,
      apiCode: searchResp.body && searchResp.body.code,
    }));
    process.exit(4);
  }

  const definedRows = searchResp.body.data || [];
  const definedSet = new Set(definedRows.map(r => r.point).filter(Boolean));
  const publishedSet = new Set(
    (publishedData.events || []).map(e => e.point).filter(Boolean)
  );

  // 4. 三类分桶
  const published = [];
  const definedUnreleased = [];
  const undefinedOnPlatform = [];
  for (const s of requestedSpecs) {
    if (publishedSet.has(s)) published.push(s);
    else if (definedSet.has(s)) definedUnreleased.push(s);
    else undefinedOnPlatform.push(s);
  }

  const versionReleased = publishedData.releaseStatus === RELEASED_STATUS_CODE;
  const allPublished = definedUnreleased.length === 0 && undefinedOnPlatform.length === 0;
  const ok = versionReleased && allPublished;

  const result = {
    ok,
    application,
    applicationId: appRecord.id,
    version,
    releaseStatus: publishedData.releaseStatus !== undefined ? publishedData.releaseStatus : null,
    versionReleased,
    requested: requestedSpecs,
    published,
    definedUnreleased,
    undefinedOnPlatform,
    publishedCount: publishedSet.size,
    definedCount: definedSet.size,
  };

  if (!versionReleased) {
    result.reason = 'VERSION_NOT_RELEASED';
    result.hint = '该应用 + 版本在埋点平台尚未达到 RELEASED 状态。请先完成发布工单审批，然后重跑。';
  } else if (!allPublished) {
    result.hint = [
      definedUnreleased.length ? `${definedUnreleased.length} 个点位已在平台定义但未进本次发布 → 去补发布工单` : null,
      undefinedOnPlatform.length ? `${undefinedOnPlatform.length} 个点位平台完全未定义 → 先去平台创建事件再走发布工单` : null,
    ].filter(Boolean).join('；');
  }

  console.log(JSON.stringify(result, null, 2));
  if (!versionReleased) process.exit(2);
  process.exit(ok ? 0 : 1);
}

main().catch(err => {
  console.error(JSON.stringify({ error: err.message, stack: err.stack }));
  process.exit(4);
});
