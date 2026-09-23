#!/usr/bin/env node

/**
 * 沙盒埋点验证工具
 *
 * 从 Snowplow 沙盒环境查询事件上报数据，解析并输出结构化 JSON。
 * 解析逻辑与 tracker_manager_frontend/src/services/check.js 保持一致。
 *
 * 用法:
 *   node sandbox-check.js --app_id=14 --namespace=ci-vicohome-android [--limit=50] [--type=pv|exp|clk|self_event] [--spm=store_page] [--reset]
 *
 * namespace 约定: 每个应用使用固定值，格式 ci-{应用名}，如 ci-vicohome-android、ci-iot-service-local
 *
 * 输出: JSON 格式的验证结果
 */

const https = require('https');

const BASE_URL = 'https://us-prod-log-sandbox.theunismart.com/micro';

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

function request(url, method, body) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const options = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname,
      method,
      headers: { 'Content-Type': 'application/json' },
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch {
          resolve(data);
        }
      });
    });

    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// --- 数据解析（与 tracker_manager_frontend/src/services/check.js 保持一致）---

function getContextsFromParameters(parameters) {
  if (parameters?.co) {
    return JSON.parse(parameters.co).data;
  } else if (parameters?.cx) {
    return JSON.parse(Buffer.from(parameters.cx, 'base64').toString()).data;
  }
  return undefined;
}

function getDataFromParameters(parameters) {
  if (parameters?.ue_pr) {
    return JSON.parse(parameters.ue_pr).data;
  } else if (parameters?.ue_px) {
    return JSON.parse(Buffer.from(parameters.ue_px, 'base64').toString()).data;
  }
  return {};
}

function getErrorTitle(errors) {
  if (!errors) {
    return '无错误信息';
  }

  try {
    const error = JSON.parse(errors[1]);
    const message = error.data.failure.messages[0];
    if (!message) return errors[0];

    if (message.error?.lookupHistory?.[0]?.errors?.[0]?.error === 'NotFound') {
      return `${message.error.error}: schema not found`;
    }

    return (
      message?.error?.dataReports?.[0]?.message ||
      message?.error?.error ||
      message?.message?.error ||
      errors[0]
    );
  } catch {
    return errors?.[0];
  }
}

function parseSpmEvent(r) {
  const co = getContextsFromParameters(r.rawEvent.parameters) || [];
  const baseSchemaEntry = co.find(d => d.schema.includes('base-schema'));
  const baseSchema = baseSchemaEntry?.data || { name: '', spm: '', type: '', pre_spm: '', pre_pv_spm: '' };
  const data = getDataFromParameters(r.rawEvent.parameters);
  const abContext = co.find(d => d.schema.includes('abtest-schema'))?.data || '{}';

  return {
    id: r.rawEvent.parameters?.eid,
    event_vendor: r.event?.event_vendor,
    name: baseSchema.name || r.event?.event_name,
    spm: baseSchema.spm,
    type: baseSchema.type,
    pre_spm: baseSchema.pre_spm,
    pre_pv_spm: baseSchema.pre_pv_spm,
    params: JSON.stringify(data.data),
    detail: JSON.stringify(r.rawEvent, null, 4),
    abcontext: JSON.stringify(abContext, null, 4),
    collector_time: r.event?.dvce_created_tstamp || r.rawEvent.context?.timestamp,
    error: r.errors || null,
    error_title: getErrorTitle(r.errors),
    status: r.errors ? 'bad' : 'good',
  };
}

function parseSelfEvent(r) {
  const data = getDataFromParameters(r.rawEvent.parameters);
  return {
    id: r.id,
    name: data.schema ? data.schema.split('/')[1] : '未知事件',
    spm: '',
    type: 'self_event',
    pre_spm: '',
    pre_pv_spm: '',
    params: JSON.stringify(data.data),
    detail: JSON.stringify(r.rawEvent, null, 4),
    collector_time: r.event?.dvce_created_tstamp || r.rawEvent.context?.timestamp,
    error: r.errors || null,
    error_title: getErrorTitle(r.errors),
    status: r.errors ? 'bad' : 'good',
  };
}

function parseUnknownEvent(r) {
  return {
    id: r.id,
    name: '不符合 spm 规范',
    spm: null,
    type: 'unknown',
    params: null,
    detail: JSON.stringify(r.rawEvent, null, 4),
    error: r.errors || null,
    error_title: getErrorTitle(r.errors),
    status: 'bad',
  };
}

function parseEvent(raw) {
  try {
    return parseSpmEvent(raw);
  } catch {
    try {
      return parseSelfEvent(raw);
    } catch {
      return parseUnknownEvent(raw);
    }
  }
}

// --- 主流程 ---

async function main() {
  const args = parseArgs();

  // --reset: 重置沙盒缓存
  if (args.reset) {
    const res = await request(`${BASE_URL}/reset`, 'GET');
    console.log(JSON.stringify({ action: 'reset', result: res }));
    return;
  }

  if (!args.app_id) {
    console.error(JSON.stringify({ error: '必须指定 --app_id 参数' }));
    process.exit(1);
  }

  if (!args.namespace) {
    console.error(JSON.stringify({ error: '必须指定 --namespace 参数，格式: ci-{应用名}，如 ci-vicohome-android' }));
    process.exit(1);
  }

  const appId = args.app_id;
  const namespace = args.namespace;
  const limit = parseInt(args.limit) || 50;
  const filterType = args.type || null;
  const filterSpm = args.spm || null;

  // 并行查询 good 和 bad
  const [goodRaw, badRaw] = await Promise.all([
    request(`${BASE_URL}/good`, 'POST', { limit, app_id: appId, namespace }),
    request(`${BASE_URL}/bad`, 'POST', { limit, app_id: appId, namespace }),
  ]);

  const allRaw = [...(goodRaw || []), ...(badRaw || [])].filter(
    r => r.rawEvent?.context
  );

  // 解析所有事件
  let events = allRaw.map(parseEvent).filter(Boolean);

  // 按时间倒序
  events.sort((a, b) => (a.collector_time < b.collector_time ? 1 : -1));

  // 过滤
  if (filterType) {
    events = events.filter(e => e.type === filterType);
  }
  if (filterSpm) {
    events = events.filter(e => e.spm?.includes(filterSpm));
  }

  const good = events.filter(e => e.status === 'good');
  const bad = events.filter(e => e.status === 'bad');

  const output = {
    namespace,
    app_id: appId,
    query_time: new Date().toISOString(),
    summary: {
      total: events.length,
      good: good.length,
      bad: bad.length,
    },
    good,
    bad,
  };

  console.log(JSON.stringify(output, null, 2));
}

main().catch(err => {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
});
