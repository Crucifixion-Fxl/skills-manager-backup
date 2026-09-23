#!/usr/bin/env node

/**
 * YAML 埋点定义 → 批量创建 API 请求体生成器
 *
 * 读取 tracking-design.md（或 .yaml），转换为 POST /api/info/batchCreateEvents 的 JSON 请求体。
 *
 * 输入支持：
 *   - tracking-design.md（含 ```tracking-spec YAML 代码块）
 *   - 纯 YAML 文件
 *
 * 用法：
 *   # 生成 payload 并打印
 *   node yaml-to-batch-payload.js --input=tracking-design.md --app-id=14
 *
 *   # 通过应用名称自动解析 ID（需要 API Key）
 *   node yaml-to-batch-payload.js --input=tracking-design.md --api-key=xxx
 *
 *   # 输出到文件
 *   node yaml-to-batch-payload.js --input=tracking-design.yaml --app-id=14 --output=payload.json
 *
 *   # 直接调用 API 创建
 *   node yaml-to-batch-payload.js --input=tracking-design.yaml --app-id=14 --api-key=xxx --execute
 *
 * 退出码：0 = 成功，1 = 参数错误，2 = 解析错误，3 = API 调用失败
 */

const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const BASE_URL = 'https://us-analytics-management.theunismart.com';

const VALID_TYPES = ['PAGE', 'MODULE', 'COMPONENT', 'SELF_DEFINE'];
const VALID_TRACKER_TYPES = ['BASE', 'CLK', 'EXP'];
const VALID_VALUE_TYPES = ['string', 'integer', 'float', 'boolean', 'array', 'object'];

// ---------------------------------------------------------------------------
// 参数解析
// ---------------------------------------------------------------------------

function parseArgs() {
  const args = {};
  process.argv.slice(2).forEach(arg => {
    if (arg.startsWith('--')) {
      const eq = arg.indexOf('=');
      if (eq > -1) {
        args[arg.slice(2, eq)] = arg.slice(eq + 1);
      } else {
        args[arg.slice(2)] = true;
      }
    }
  });
  return args;
}

// ---------------------------------------------------------------------------
// YAML 简易解析器（不依赖外部包）
// ---------------------------------------------------------------------------

function parseYamlValue(raw) {
  const val = raw.trim().replace(/^["']/, '').replace(/["']$/, '');
  if (val === 'true') return true;
  if (val === 'false') return false;
  return val;
}

function parseYamlKV(str) {
  const colonIdx = str.indexOf(':');
  if (colonIdx <= 0) return null;
  return { key: str.slice(0, colonIdx).trim(), val: parseYamlValue(str.slice(colonIdx + 1)) };
}

function handleBaseSchemaField(content, state, currentEvent) {
  state.inParams = false;
  state.inBaseSchemas = false;
  state.currentParam = null;
  const val = content.slice('base_schemas:'.length).trim();
  if (val.startsWith('[')) {
    try {
      currentEvent.base_schemas = JSON.parse(val.replace(/'/g, '"'));
    } catch {
      currentEvent.base_schemas = [];
    }
  } else {
    state.inBaseSchemas = true;
    currentEvent.base_schemas = [];
  }
}

function handleEventField(content, indent, state, currentEvent) {
  if (state.eventFieldIndent < 0) state.eventFieldIndent = indent;
  if (indent > state.eventFieldIndent) return;

  if (content === 'parameters:') {
    state.inParams = true;
    state.inBaseSchemas = false;
    state.currentParam = null;
    state.paramListIndent = -1;
    currentEvent.parameters = [];
    return;
  }

  if (content.startsWith('base_schemas:')) {
    handleBaseSchemaField(content, state, currentEvent);
    return;
  }

  state.inParams = false;
  state.inBaseSchemas = false;
  state.currentParam = null;
  const kv = parseYamlKV(content);
  if (kv) currentEvent[kv.key] = kv.val;
}

function handleTopLevelLine(content, result, state, inEventsRef) {
  if (content.startsWith('application:')) {
    result.application = parseYamlValue(content.slice('application:'.length));
  } else if (content.startsWith('version:')) {
    result.version = parseYamlValue(content.slice('version:'.length));
  } else if (content === 'events:') {
    inEventsRef.value = true;
    result.events = [];
    state.eventListIndent = -1;
  }
}

function handleListItem(content, indent, state, result) {
  if (state.eventListIndent < 0) state.eventListIndent = indent;

  if (indent <= state.eventListIndent) {
    state.eventListIndent = indent;
    state.currentEvent = {};
    state.inParams = false;
    state.inBaseSchemas = false;
    state.currentParam = null;
    state.paramListIndent = -1;
    state.eventFieldIndent = -1;
    result.events.push(state.currentEvent);
    const kv = parseYamlKV(content.slice(2).trim());
    if (kv) state.currentEvent[kv.key] = kv.val;
    return;
  }

  if (state.inParams && state.currentEvent) {
    if (state.paramListIndent < 0) state.paramListIndent = indent;
    state.currentParam = {};
    state.currentEvent.parameters.push(state.currentParam);
    const kv = parseYamlKV(content.slice(2).trim());
    if (kv) state.currentParam[kv.key] = kv.val;
    return;
  }

  if (state.inBaseSchemas && state.currentEvent) {
    state.currentEvent.base_schemas.push(parseYamlValue(content.slice(2)));
  }
}

function handleNestedParamAttr(content, indent, state) {
  if (!state.currentParam || state.paramListIndent <= 0 || indent <= state.paramListIndent) return false;
  const kv = parseYamlKV(content);
  if (kv) state.currentParam[kv.key] = kv.val;
  return true;
}

function processSimpleYamlLine(line, result, inEventsRef, state) {
  const trimmed = line.trimEnd();
  if (!trimmed || /^\s*#/.test(trimmed)) return;

  const indent = line.length - line.trimStart().length;
  const content = trimmed.trimStart();

  if (indent === 0) {
    handleTopLevelLine(content, result, state, inEventsRef);
    return;
  }

  if (!inEventsRef.value) return;

  if (content.startsWith('- ')) {
    handleListItem(content, indent, state, result);
    return;
  }

  if (!state.currentEvent) return;

  if (state.eventFieldIndent < 0 && indent > state.eventListIndent) {
    state.eventFieldIndent = indent;
  }

  if (handleNestedParamAttr(content, indent, state)) return;

  if (indent <= state.eventFieldIndent || state.eventFieldIndent < 0) {
    handleEventField(content, indent, state, state.currentEvent);
  }
}

function extractTrackingSpec(raw) {
  const blocks = [];
  let inBlock = false;
  let current = [];
  for (const line of raw.split('\n')) {
    if (!inBlock && line.startsWith('```tracking-spec')) {
      inBlock = true;
      current = [];
    } else if (inBlock && line.startsWith('```')) {
      inBlock = false;
      blocks.push(current.join('\n'));
    } else if (inBlock) {
      current.push(line);
    }
  }
  return blocks.length > 0 ? blocks.join('\n') : raw;
}

function parseSimpleYaml(yamlStr) {
  const lines = yamlStr.split('\n');
  const result = {};
  const inEventsRef = { value: false };
  const state = {
    currentEvent: null,
    currentParam: null,
    inParams: false,
    inBaseSchemas: false,
    eventListIndent: -1,
    eventFieldIndent: -1,
    paramListIndent: -1,
  };

  for (const line of lines) {
    processSimpleYamlLine(line, result, inEventsRef, state);
  }

  return result;
}

// ---------------------------------------------------------------------------
// 校验
// ---------------------------------------------------------------------------

function validateEventParams(event, prefix) {
  const errors = [];
  for (const [pi, param] of (event.parameters || []).entries()) {
    if (!param.name) errors.push(`${prefix}.parameters[${pi}]: 缺少 name`);
    if (param.value_type && !VALID_VALUE_TYPES.includes(param.value_type)) {
      errors.push(`${prefix}.parameters[${pi}]: value_type "${param.value_type}" 无效`);
    }
  }
  return errors;
}

function validateEvent(event, index) {
  const errors = [];
  const prefix = `events[${index}] (${event.point || event.name || '?'})`;

  if (!event.point) errors.push(`${prefix}: 缺少 point`);
  if (!event.type) errors.push(`${prefix}: 缺少 type`);
  if (event.type && !VALID_TYPES.includes(event.type.toUpperCase())) {
    errors.push(`${prefix}: type "${event.type}" 无效，允许值: ${VALID_TYPES.join(', ')}`);
  }
  if (event.tracker_type && !VALID_TRACKER_TYPES.includes(event.tracker_type.toUpperCase())) {
    errors.push(`${prefix}: tracker_type "${event.tracker_type}" 无效，允许值: ${VALID_TRACKER_TYPES.join(', ')}`);
  }

  const type = (event.type || '').toUpperCase();
  if (type === 'MODULE' && !event.parent_page) {
    errors.push(`${prefix}: MODULE 类型必须指定 parent_page`);
  }
  if (type === 'COMPONENT') {
    if (!event.parent_page) errors.push(`${prefix}: COMPONENT 类型必须指定 parent_page`);
    if (!event.parent_module) errors.push(`${prefix}: COMPONENT 类型必须指定 parent_module`);
  }

  errors.push(...validateEventParams(event, prefix));

  return errors;
}

// ---------------------------------------------------------------------------
// 转换：YAML spec → API 请求体
// ---------------------------------------------------------------------------

function transformToApiPayload(spec, applicationId) {
  const events = (spec.events || []).map(event => {
    const apiEvent = {
      name: event.name || event.point,
      type: (event.type || '').toUpperCase(),
      trackerType: (event.tracker_type || 'BASE').toUpperCase(),
      point: event.point,
    };

    if (event.description) apiEvent.description = event.description;
    if (event.category) apiEvent.category = event.category;
    if (event.parent_page) apiEvent.parentPage = event.parent_page;
    if (event.parent_module) apiEvent.parentModule = event.parent_module;
    if (event.base_schemas && event.base_schemas.length > 0) {
      apiEvent.baseSchemas = event.base_schemas;
    }

    if (event.parameters && event.parameters.length > 0) {
      apiEvent.parameters = event.parameters.map(p => ({
        name: p.name,
        valueType: p.value_type || 'string',
        isRequired: p.is_required === true || p.is_required === 'true',
        description: p.description || '',
      }));
    }

    return apiEvent;
  });

  return {
    applicationId: parseInt(applicationId, 10),
    events,
  };
}

// ---------------------------------------------------------------------------
// API 调用
// ---------------------------------------------------------------------------

async function resolveApplicationId(appName, apiKey) {
  const url = `${BASE_URL}/api/info/getAllApplication`;
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${apiKey}` },
  });
  if (!res.ok) throw new Error(`获取应用列表失败: ${res.status}`);
  const body = await res.json();
  if (!body.success || body.code !== 200) {
    throw new Error(`API 错误: ${body.errorMessage || JSON.stringify(body)}`);
  }
  const apps = body.data || [];
  const app = apps.find(a =>
    a.name === appName || a.point === appName
  );
  if (!app) {
    const available = apps.map(a => `${a.name}(${a.point})`).join(', ');
    throw new Error(`找不到应用 "${appName}"，可用应用: ${available}`);
  }
  return app.id;
}

async function executeBatchCreate(payload, apiKey) {
  const url = `${BASE_URL}/api/info/batchCreateEvents`;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify(payload),
  });

  const body = await res.json();
  return body;
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------

async function resolveAppId(args, appName) {
  let appId = args['app-id'];
  if (!appId && appName && args['api-key']) {
    console.log(`正在通过应用名 "${appName}" 解析 applicationId...`);
    try {
      appId = await resolveApplicationId(appName, args['api-key']);
      console.log(`解析成功: applicationId = ${appId}`);
    } catch (err) {
      console.error(`错误: ${err.message}`);
      process.exit(3);
    }
  }
  return appId;
}

async function executeApiCreate(payload, apiKey) {
  if (!apiKey) {
    console.error('错误: --execute 需要提供 --api-key');
    process.exit(1);
  }

  console.log(`正在调用批量创建 API (${payload.events.length} 个事件)...`);
  try {
    const result = await executeBatchCreate(payload, apiKey);
    if (result.success || result.code === 200) {
      console.log('创建成功:');
      console.log(JSON.stringify(result.data, null, 2));
    } else {
      console.error(`创建失败: ${result.errorMessage || JSON.stringify(result)}`);
      process.exit(3);
    }
  } catch (err) {
    console.error(`API 调用异常: ${err.message}`);
    process.exit(3);
  }
}

async function main() {
  const args = parseArgs();

  if (args.help) {
    console.log(`用法: node yaml-to-batch-payload.js --input=<file> --app-id=<id> [options]

选项:
  --input=<file>       输入 YAML 文件
  --app-id=<id>        应用 ID（数字）
  --api-key=<key>      Personal Access Token（tmt_ 前缀，用于应用名解析或 --execute）
  --author=<name>      操作者标识（可选）
  --output=<file>      输出 JSON 文件路径（默认打印到 stdout）
  --execute            生成 payload 后直接调用批量创建 API
  --help               显示帮助`);
    process.exit(0);
  }

  if (!args.input) {
    console.error('错误: 必须指定 --input=<file>');
    process.exit(1);
  }

  const inputPath = path.resolve(args.input);
  if (!fs.existsSync(inputPath)) {
    console.error(`错误: 文件不存在: ${inputPath}`);
    process.exit(1);
  }

  const raw = fs.readFileSync(inputPath, 'utf-8');
  const content = extractTrackingSpec(raw);
  const spec = parseSimpleYaml(content);
  const appName = spec.application || null;
  const allEvents = spec.events || [];

  if (allEvents.length === 0) {
    console.error('错误: 未找到任何事件定义');
    process.exit(2);
  }

  const errors = [];
  allEvents.forEach((event, i) => errors.push(...validateEvent(event, i)));

  if (errors.length > 0) {
    console.error('校验失败:');
    errors.forEach(e => console.error(`  ✗ ${e}`));
    process.exit(2);
  }

  const appId = await resolveAppId(args, appName);
  if (!appId) {
    console.error('错误: 必须指定 --app-id=<id> 或同时提供 --api-key 以自动解析应用名');
    process.exit(1);
  }

  const payload = transformToApiPayload({ events: allEvents }, appId);
  const jsonStr = JSON.stringify(payload, null, 2);

  if (args.output) {
    const outputPath = path.resolve(args.output);
    fs.writeFileSync(outputPath, jsonStr, 'utf-8');
    console.log(`已写入: ${outputPath}`);
    console.log(`事件数量: ${payload.events.length}`);
  }

  if (!args.execute && !args.output) {
    console.log(jsonStr);
    console.log(`\n共 ${payload.events.length} 个事件`);
  }

  if (args.execute) {
    await executeApiCreate(payload, args['api-key']);
  }
}

main();
