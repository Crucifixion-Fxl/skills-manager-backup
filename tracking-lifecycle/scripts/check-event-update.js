#!/usr/bin/env node

/**
 * 更新事件防丢参数卡控（saveOrUpdateEventInfo 专用）
 *
 * 背景：POST /api/info/saveOrUpdateEventInfo（带 id 更新）对参数列表是**全量覆盖**——
 * 服务端先删除该事件的全部旧参数，再写入请求体 parameters。若 payload 漏带了某个
 * 已有参数，后端**不报错、直接把它删掉**（静默数据丢失）。本脚本在提交前做硬卡控：
 * 对比 getEventDetail 的现有参数与待提交 payload，少了任何已有参数就 FAIL；
 * 同时要求所有 valueType 为 JSON Schema 名称，禁止数字码和单事件 float 别名。
 *
 * 用法：
 *   node check-event-update.js --current=<getEventDetail.json> --payload=<update-payload.json>
 *
 *   --current   GET /api/info/getEventDetail?eventId={id} 的响应（可含/不含外层 {code,data}）
 *   --payload   即将 POST 给 saveOrUpdateEventInfo 的请求体 JSON
 *
 * 退出码：0=通过；1=会丢参数、id 不一致或 valueType 非法（卡住，不要提交）；2=输入错误
 */

const fs = require('fs');
const VALID_VALUE_TYPES = new Set(['null', 'boolean', 'object', 'array', 'number', 'string', 'integer']);

function parseArgs(argv) {
  const args = {};
  for (const a of argv.slice(2)) {
    if (!a.startsWith('--')) continue;
    const eq = a.indexOf('=');
    if (eq === -1) continue;
    args[a.slice(2, eq)] = a.slice(eq + 1);
  }
  return args;
}

// getEventDetail 可能返回 {code,data:{...}} 或直接 {...}；统一取出事件对象
function unwrap(json) {
  if (json && typeof json === 'object' && 'data' in json && json.data && typeof json.data === 'object') {
    return json.data;
  }
  return json;
}

function paramNames(obj) {
  const list = (obj && Array.isArray(obj.parameters)) ? obj.parameters : [];
  return list
    .map(p => (p && typeof p.name === 'string') ? p.name.trim() : null)
    .filter(Boolean);
}

/**
 * 核心比对：返回 { droppedParams, idMismatch, currentId, payloadId, invalidTypeParams }
 * droppedParams = 现有参数里、payload 没带的（会被静默删除）
 */
function diffUpdate(current, payload) {
  const cur = unwrap(current);
  const curNames = new Set(paramNames(cur));
  const payNames = new Set(paramNames(payload));
  const droppedParams = [...curNames].filter(n => !payNames.has(n));

  const currentId = cur?.id != null ? cur.id : null;
  const payloadId = payload?.id != null ? payload.id : null;
  // 更新场景：payload 必须带 id，且需与现有事件 id 一致
  const idMismatch = payloadId == null || (currentId != null && String(currentId) !== String(payloadId));

  const invalidTypeParams = (Array.isArray(payload?.parameters) ? payload.parameters : [])
    .flatMap((parameter, index) => VALID_VALUE_TYPES.has(parameter?.valueType)
      ? [] : [{ index, name: parameter?.name ?? null }]);

  return { droppedParams, idMismatch, currentId, payloadId, invalidTypeParams };
}

function readJson(p, label) {
  if (!p) {
    console.error(JSON.stringify({ error: `缺少 --${label} 参数` }));
    process.exit(2);
  }
  if (!fs.existsSync(p)) {
    console.error(JSON.stringify({ error: `文件不存在: ${p}` }));
    process.exit(2);
  }
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) {
    console.error(JSON.stringify({ error: `JSON 解析失败 (${label}): ${e.message}` }));
    process.exit(2);
  }
}

function main() {
  const args = parseArgs(process.argv);
  const current = readJson(args.current, 'current');
  const payload = readJson(args.payload, 'payload');

  const { droppedParams, idMismatch, currentId, payloadId, invalidTypeParams } = diffUpdate(current, payload);
  const problems = [];
  if (idMismatch) {
    problems.push(
      `payload.id (${payloadId}) 与现有事件 id (${currentId}) 不一致或缺失：更新必须带正确的 id，` +
      `否则会被当作新建（或操作错事件）`);
  }
  if (droppedParams.length > 0) {
    problems.push(
      `以下已有参数未包含在 payload 中，提交后会被服务端静默删除：${droppedParams.join(', ')}。` +
      `补参数必须回传"已有参数 + 新增参数"的完整列表（先 getEventDetail 取回再 append）`);
  }

  if (invalidTypeParams.length > 0) {
    problems.push('Invalid valueType: use exact JSON Schema type names (null, boolean, object, array, number, string, integer); numeric codes and float are not accepted by the single-event endpoint.');
  }
  const result = problems.length > 0 ? 'FAIL' : 'PASS';
  console.log(JSON.stringify({
    result,
    currentId,
    payloadId,
    droppedParams,
    idMismatch,
    invalidTypeParams,
    problems,
  }, null, 2));
  process.exit(result === 'FAIL' ? 1 : 0);
}

if (require.main === module) {
  main();
}

module.exports = { diffUpdate, unwrap, paramNames };
