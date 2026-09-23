#!/usr/bin/env node

/**
 * 埋点规范校验工具
 *
 * 校验 tracking-design.md 的 YAML 格式合规性，以及与 metric-spec.md 的一致性。
 * 规则来源：tracking-schema.md 红线清单 + tracking-lifecycle Step 3 一致性评审维度。
 *
 * 用法：
 *   node tracking-spec-validator.js --tracking-design=path/to/tracking-design.md [--metric-spec=path/to/metric-spec.md]
 *
 * 仅提供 --tracking-design 时只做 YAML 格式校验（Step 2）。
 * 同时提供 --metric-spec 时追加一致性检查（Step 3）。
 *
 * 退出码：0 = 通过，1 = 有阻断级别错误，2 = 仅有警告
 */

const fs = require('fs');
const path = require('path');

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
// Markdown 解析：提取 ```tracking-spec ``` 块中的 YAML
// ---------------------------------------------------------------------------

function extractYamlBlocks(md) {
  const blocks = [];
  const lines = md.split('\n');
  let inBlock = false;
  let current = [];
  for (const line of lines) {
    if (!inBlock && line.startsWith('```tracking-spec')) {
      inBlock = true;
      current = [];
    } else if (inBlock && line.startsWith('```')) {
      blocks.push(current.join('\n'));
      inBlock = false;
      current = [];
    } else if (inBlock) {
      current.push(line);
    }
  }
  return blocks;
}

/**
 * 简易 YAML 解析器 —— 仅处理 tracking-spec 用到的子集：
 *   顶层 key: value
 *   events 列表（- name: ... 开头）
 *   嵌套 parameters 列表
 *
 * 不依赖外部库。
 */

function parseYamlInlineList(val) {
  return val.slice(1, -1).split(',').map(s => s.trim().replace(/^["']/, '').replace(/["']$/, ''));
}

function parseYamlScalar(val) {
  if (val === 'true') return true;
  if (val === 'false') return false;
  if (val.startsWith('[') && val.endsWith(']')) return parseYamlInlineList(val);
  return val;
}

function flushParam(currentParam, currentEvent) {
  if (currentParam && currentEvent) {
    currentEvent.parameters.push(currentParam);
  }
}

function handleNewEvent(trimmed, result, state) {
  flushParam(state.currentParam, state.currentEvent);
  if (state.currentEvent) result.events.push(state.currentEvent);
  state.currentEvent = { parameters: [] };
  state.currentEvent.name = trimmed.replace('- name:', '').trim();
  state.inParameters = false;
  state.currentParam = null;
}

function handleParamAttrLine(trimmed, state) {
  const colonIdx = trimmed.indexOf(':');
  if (colonIdx <= -1) return;
  const key = trimmed.slice(0, colonIdx).trim();
  const raw = trimmed.slice(colonIdx + 1).trim();
  state.currentParam[key] = parseYamlScalar(raw);
}

function handleEventAttrLine(trimmed, currentEvent) {
  const colonIdx = trimmed.indexOf(':');
  if (colonIdx <= -1) return;
  const key = trimmed.slice(0, colonIdx).trim();
  const raw = trimmed.slice(colonIdx + 1).trim();
  currentEvent[key] = parseYamlScalar(raw);
}

function handleZeroIndentLine(trimmed, result) {
  if (trimmed.startsWith('application:')) {
    result.application = trimmed.split(':').slice(1).join(':').trim();
  }
}

function handleParametersContext(trimmed, indent, state) {
  if (!state.currentEvent) return false;
  if (trimmed === 'parameters:') {
    state.inParameters = true;
    state.currentParam = null;
    return true;
  }
  if (state.inParameters && trimmed.startsWith('- name:') && indent >= 6) {
    flushParam(state.currentParam, state.currentEvent);
    state.currentParam = { name: trimmed.replace('- name:', '').trim() };
    return true;
  }
  if (state.inParameters && state.currentParam && indent >= 8) {
    handleParamAttrLine(trimmed, state);
    return true;
  }
  return false;
}

function handleEventContext(trimmed, indent, result, state) {
  if (trimmed.startsWith('- name:') && indent <= 4) {
    handleNewEvent(trimmed, result, state);
    return;
  }
  if (state.inParameters && indent <= 4 && !trimmed.startsWith('-')) {
    flushParam(state.currentParam, state.currentEvent);
    state.currentParam = null;
    state.inParameters = false;
  }
  if (!state.inParameters && state.currentEvent && indent >= 4) {
    handleEventAttrLine(trimmed, state.currentEvent);
    return;
  }
  if (state.inParameters && indent < 6 && trimmed.includes(':')) {
    flushParam(state.currentParam, state.currentEvent);
    state.currentParam = null;
    state.inParameters = false;
    handleEventAttrLine(trimmed, state.currentEvent);
  }
}

function parseTrackingYaml(yamlStr) {
  const lines = yamlStr.split('\n');
  const result = { application: null, events: [] };
  const state = { currentEvent: null, currentParam: null, inParameters: false };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, '');
    const trimmed = line.trimStart();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const indent = line.length - trimmed.length;
    if (indent === 0) { handleZeroIndentLine(trimmed, result); continue; }
    if (handleParametersContext(trimmed, indent, state)) continue;
    handleEventContext(trimmed, indent, result, state);
  }

  flushParam(state.currentParam, state.currentEvent);
  if (state.currentEvent) result.events.push(state.currentEvent);

  return result;
}

// ---------------------------------------------------------------------------
// metric-spec.md 解析：提取 Markdown 表格
// ---------------------------------------------------------------------------

function parseMetricSpecTable(md) {
  const metrics = [];
  const lines = md.split('\n');
  let headerFound = false;
  let columnNames = [];
  let isMetricTable = false;

  for (const line of lines) {
    const trimmed = line.trim();

    // 遇到非表格行时重置状态（允许解析下一个表格）
    if (!trimmed.startsWith('|')) {
      headerFound = false;
      columnNames = [];
      isMetricTable = false;
      continue;
    }

    const cells = trimmed.split('|').slice(1, -1).map(c => c.trim());

    // 跳过分隔行
    if (cells.every(c => /^[-:]+$/.test(c))) {
      headerFound = true;
      continue;
    }

    // 表头 — 判断是否为指标主表（必须包含"计算公式"列，以区分告警子表等）
    if (!headerFound) {
      columnNames = cells.map(c => c.toLowerCase());
      isMetricTable = columnNames.some(c => c.includes('计算公式') || c.includes('formula'));
      continue;
    }

    // 数据行 — 只处理指标主表
    if (headerFound && isMetricTable && cells.length >= 2) {
      const metric = {};
      columnNames.forEach((col, i) => {
        metric[col] = cells[i] || '';
      });
      metrics.push(metric);
    }
  }

  return metrics;
}

// ---------------------------------------------------------------------------
// tracking-design.md 解析：提取自然语言部分的指标来源/参与信息
// ---------------------------------------------------------------------------

function parseSectionTrigger(block, section) {
  const triggerMatch = block.match(/触发场景[：:]\s*(.+)/);
  if (triggerMatch) {
    section.hasTriggerScene = true;
    section.description = triggerMatch[1].trim();
  }
}

function stripLeadingParenthetical(s) {
  const openIdx = s.search(/[（(]/);
  return openIdx < 0 ? s.trim() : s.slice(0, openIdx).trim();
}

function splitOnFirstEquals(s) {
  const line = s.split('\n', 1)[0];
  const eqIdx = line.search(/[=＝]/);
  if (eqIdx <= 0) return null;
  const name = line.slice(0, eqIdx).trim();
  const formula = line.slice(eqIdx + 1).trim();
  if (!name || !formula) return null;
  return { name, formula };
}

function parseSectionMetrics(block, section) {
  const sourceMatch = block.match(/指标来源[：:]\s*(.+)/);
  if (sourceMatch) {
    section.metricSources = sourceMatch[1].split(/[、,]/).map(stripLeadingParenthetical);
  }

  const roleMatch = block.match(/指标参与[：:]\s*(.+)/);
  if (roleMatch) {
    section.metricRoles.push(roleMatch[1].trim());
  }

  for (const m of block.matchAll(/计算公式回溯[：:]\s*(.+)/g)) {
    section.formulaTracebacks.push(m[1].trim());
  }
}

function extractEventHeaderName(block) {
  const marker = block.match(/\*\*事件[：:]/);
  if (!marker) return null;
  const afterMarker = block.slice(marker.index + marker[0].length);
  const newlineIdx = afterMarker.indexOf('\n');
  const headerLine = newlineIdx < 0 ? afterMarker : afterMarker.slice(0, newlineIdx);
  const endIdx = headerLine.indexOf('**');
  if (endIdx < 0) return null;
  const name = headerLine.slice(0, endIdx).trim();
  return name || null;
}

function parseTrackingDesignSections(md) {
  const sections = [];
  const eventBlocks = md.split(/(?=\*\*事件[：:])/);

  for (const block of eventBlocks) {
    const headerName = extractEventHeaderName(block);
    if (!headerName) continue;

    const section = {
      name: headerName,
      hasTriggerScene: false,
      hasDataExample: false,
      metricSources: [],
      metricRoles: [],
      formulaTracebacks: [],
      hasYamlBlock: false,
      description: '',
    };

    parseSectionTrigger(block, section);

    if (block.match(/数据示例[：:]/)) {
      section.hasDataExample = true;
    }

    parseSectionMetrics(block, section);

    if (block.includes('```tracking-spec') || block.match(/^[ \t]+- name:/m)) {
      section.hasYamlBlock = true;
    }

    sections.push(section);
  }
  return sections;
}

// ---------------------------------------------------------------------------
// 校验规则
// ---------------------------------------------------------------------------

const VALID_TYPES = ['PAGE', 'MODULE', 'COMPONENT', 'SELF_DEFINE'];
const VALID_TRACKER_TYPES = ['BASE', 'CLK', 'EXP'];
const VALID_VALUE_TYPES = ['string', 'integer', 'float', 'boolean', 'array', 'object'];
const SNAKE_CASE_RE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;

// 敏感字段检测：镜像后端 application-prod.yml 的 sensitive-field 配置
// （tracker-management TrackerSensitiveFieldService）。后端对**新增**参数名做全匹配
// （不区分大小写），命中即拒绝创建（`检测到敏感字段，无法添加参数`）；含 allow 关键词则放行。
// ⚠️ 这些是后端配置的快照，若后端 sensitive-field 配置变更，此处需同步。
const SENSITIVE_FIELD_ALLOW_KEYWORDS = ['encrypt'];
// 子串匹配的敏感关键词（镜像后端 `.*kw.*` 规则）
const SENSITIVE_FIELD_KEYWORDS = [
  { kw: 'password', desc: '密码相关字段' },
  { kw: 'pswd', desc: '密码相关字段' },
  { kw: 'secret', desc: '密钥相关字段' },
  { kw: 'token', desc: '令牌相关字段' },
  { kw: 'phone', desc: '电话号码相关字段' },
  { kw: 'mobile', desc: '手机号码相关字段' },
  { kw: 'email', desc: '邮箱相关字段' },
  { kw: 'ssid', desc: '身份证相关字段' },
  { kw: 'mac', desc: 'mac 相关字段' },
  { kw: 'address', desc: '地址相关字段' },
];

// 返回命中的敏感字段描述，未命中返回 null（语义对齐后端 checkSingleField）。
// 全部用字符串包含 + 下划线分段判断，不用正则——避免 ReDoS（Sonar S5852）。
function matchSensitiveField(name) {
  if (!name) return null;
  const lower = name.toLowerCase();
  if (SENSITIVE_FIELD_ALLOW_KEYWORDS.some(kw => lower.includes(kw))) return null;
  for (const { kw, desc } of SENSITIVE_FIELD_KEYWORDS) {
    if (lower.includes(kw)) return desc;
  }
  // 后端 ip 规则 (^|.*_)ip(_.*|$)：ip 必须是完整的下划线分段（ip / ip_x / x_ip / x_ip_y）
  if (lower.split('_').includes('ip')) return 'ip 相关字段';
  return null;
}

function validateEventRequiredFields(evt, label, add) {
  if (!evt.name) add('error', 'YAML', `${label}: 缺少 name`);
  if (!evt.point) add('error', 'YAML', `${label}: 缺少 point`);

  if (!evt.type) {
    add('error', 'YAML', `${label}: 缺少 type`);
  } else if (!VALID_TYPES.includes(evt.type)) {
    add('error', 'YAML', `${label}: type "${evt.type}" 无效，可选值: ${VALID_TYPES.join(', ')}`);
  }

  if (!evt.tracker_type) {
    add('error', 'YAML', `${label}: 缺少 tracker_type`);
  } else if (!VALID_TRACKER_TYPES.includes(evt.tracker_type)) {
    add('error', 'YAML', `${label}: tracker_type "${evt.tracker_type}" 无效，可选值: ${VALID_TRACKER_TYPES.join(', ')}`);
  }

  if (!evt.description) add('warn', 'YAML', `${label}: 缺少 description`);
}

function validateComponentHierarchy(evt, label, pagePoints, modulePoints, add) {
  if (!evt.parent_page) {
    add('error', 'R4', `${label}: COMPONENT 类型必须指定 parent_page`);
  } else if (!pagePoints.has(evt.parent_page)) {
    add('error', 'R4', `${label}: parent_page "${evt.parent_page}" 未在当前文档中定义为 PAGE`);
  }
  if (!evt.parent_module) {
    add('error', 'R4', `${label}: COMPONENT 类型必须指定 parent_module`);
  } else if (!modulePoints.has(evt.parent_module)) {
    add('error', 'R4', `${label}: parent_module "${evt.parent_module}" 未在当前文档中定义为 MODULE`);
  }
}

function validateModuleHierarchy(evt, label, pagePoints, add) {
  if (!evt.parent_page) {
    add('error', 'R5', `${label}: MODULE 类型必须指定 parent_page`);
  } else if (!pagePoints.has(evt.parent_page)) {
    add('error', 'R5', `${label}: parent_page "${evt.parent_page}" 未在当前文档中定义为 PAGE`);
  }
}

function validateEventHierarchy(evt, label, pagePoints, modulePoints, add) {
  if (evt.type === 'COMPONENT') {
    validateComponentHierarchy(evt, label, pagePoints, modulePoints, add);
  }
  if (evt.type === 'MODULE') {
    validateModuleHierarchy(evt, label, pagePoints, add);
  }
  if (evt.type === 'PAGE' || evt.type === 'SELF_DEFINE') {
    if (evt.parent_page) add('warn', 'YAML', `${label}: ${evt.type} 类型不应指定 parent_page`);
    if (evt.parent_module) add('warn', 'YAML', `${label}: ${evt.type} 类型不应指定 parent_module`);
  }
}

function validateEventTrackerSemantic(evt, label, add) {
  if (!evt.type || !evt.tracker_type) return;
  const mismatchRules = [
    { type: 'PAGE', expected: ['BASE'], msg: 'PAGE 通常搭配 BASE' },
    { type: 'COMPONENT', expected: ['CLK', 'EXP'], msg: 'COMPONENT 通常搭配 CLK 或 EXP' },
    { type: 'MODULE', expected: ['BASE', 'EXP'], msg: 'MODULE 通常搭配 BASE 或 EXP' },
  ];
  for (const rule of mismatchRules) {
    if (evt.type === rule.type && !rule.expected.includes(evt.tracker_type)) {
      add('warn', 'R10', `${label}: ${rule.msg}，当前为 ${evt.tracker_type}`);
    }
  }
}

// 单个参数的字段级校验（拆出以降低 validateEventParams 的认知复杂度）
function validateSingleParam(param, pLabel, add) {
  if (!SNAKE_CASE_RE.test(param.name)) {
    add('warn', 'R8', `${pLabel}: 参数名不符合 snake_case 规范`);
  }

  const sensitiveDesc = matchSensitiveField(param.name);
  if (sensitiveDesc) {
    add('error', 'SENSITIVE',
      `${pLabel}: 参数名命中敏感字段（${sensitiveDesc}），平台会拒绝创建（检测到敏感字段，无法添加参数）。` +
      `PII 应在端上脱敏/哈希后用非敏感字段名上报；确需保留可加 allow 关键词（如 encrypt_）`);
  }

  if (!param.value_type) {
    add('warn', 'YAML', `${pLabel}: 缺少 value_type`);
  } else if (!VALID_VALUE_TYPES.includes(param.value_type)) {
    add('warn', 'YAML', `${pLabel}: value_type "${param.value_type}" 无效`);
  }

  if (param.is_required === undefined || param.is_required === '') {
    add('warn', 'YAML', `${pLabel}: 缺少 is_required`);
  }
}

function validateEventParams(evt, label, add) {
  const paramNames = new Set();
  for (const param of (evt.parameters || [])) {
    const pLabel = `${label} → 参数 "${param.name || '未命名'}"`;

    if (!param.name) {
      add('error', 'YAML', `${pLabel}: 缺少 name`);
      continue;
    }

    if (paramNames.has(param.name)) {
      add('error', 'YAML', `${pLabel}: 参数名 "${param.name}" 在同一事件中重复`);
    }
    paramNames.add(param.name);

    validateSingleParam(param, pLabel, add);
  }
}

function validateCrossEventParamTypes(events, add) {
  const paramTypeMap = new Map();
  for (const evt of events) {
    for (const param of (evt.parameters || [])) {
      if (!param.name || !param.value_type) continue;
      const existing = paramTypeMap.get(param.name);
      if (existing && existing.type !== param.value_type) {
        add('warn', 'CONSISTENCY',
          `参数 "${param.name}" 在 "${existing.eventName}" 中为 ${existing.type}，` +
          `在 "${evt.name}" 中为 ${param.value_type}，类型不一致`);
      }
      if (!existing) {
        paramTypeMap.set(param.name, { type: param.value_type, eventName: evt.name });
      }
    }
  }
}

function buildHierarchySets(events) {
  const pagePoints = new Set();
  const modulePoints = new Set();
  for (const evt of events) {
    if (evt.type === 'PAGE') pagePoints.add(evt.point);
    if (evt.type === 'MODULE') modulePoints.add(evt.point);
  }
  return { pagePoints, modulePoints };
}

function validateEachEvent(events, add) {
  const pointSet = new Set();
  const { pagePoints, modulePoints } = buildHierarchySets(events);
  for (const evt of events) {
    const label = `事件 "${evt.name}" (point: ${evt.point || '未定义'})`;
    validateEventRequiredFields(evt, label, add);
    if (evt.point) {
      if (pointSet.has(evt.point)) {
        add('error', 'R3', `${label}: point "${evt.point}" 重复`);
      }
      pointSet.add(evt.point);
    }
    if (evt.point && !SNAKE_CASE_RE.test(evt.point)) {
      add('warn', 'R8', `${label}: point "${evt.point}" 不符合 snake_case 规范`);
    }
    validateEventHierarchy(evt, label, pagePoints, modulePoints, add);
    validateEventTrackerSemantic(evt, label, add);
    validateEventParams(evt, label, add);
  }
  validateCrossEventParamTypes(events, add);
}

function validateYaml(spec) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  if (!spec.application) {
    add('error', 'YAML', '缺少 application 字段');
  }

  if (!spec.events || spec.events.length === 0) {
    add('error', 'YAML', '未定义任何事件');
  } else {
    validateEachEvent(spec.events, add);
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 一致性检查（metric-spec ↔ tracking-design）
// ---------------------------------------------------------------------------

// 事件名称匹配：剥离常见动作后缀后做精确匹配或前缀匹配
const ACTION_SUFFIXES = ['浏览', '点击', '曝光', '进入', '离开', '完成', '成功', '失败', '展示', '打开', '关闭'];

function eventNameMatches(metricEventName, yamlEvent) {
  const eName = metricEventName.trim();
  const yName = (yamlEvent.name || '').trim();
  const yPoint = (yamlEvent.point || '').trim();

  // 1. 精确匹配 name 或 point
  if (yName === eName || yPoint === eName) return true;

  // 2. 剥离动作后缀后精确匹配（"商店页面浏览" → "商店页面" === yName）
  for (const suffix of ACTION_SUFFIXES) {
    if (eName.endsWith(suffix)) {
      const base = eName.slice(0, -suffix.length);
      if (base.length >= 2 && (base === yName || base === yPoint)) return true;
    }
  }

  // 3. YAML name 是 metric 事件名的前缀（至少 2 字符）
  return yName.length >= 2 && eName.startsWith(yName);
}

function isAlertSkippable(alertSetting) {
  const lower = alertSetting.toLowerCase();
  return (lower.includes('不告警') || lower === '无' || lower === 'no' || lower === '-' || lower === '—') ||
    (alertSetting.includes('见告警子表') || alertSetting.includes('告警子表') || alertSetting.includes('子表'));
}

function validateMetricAlertDetail(metric, add) {
  const alertSetting = findColumn(metric, ['告警设置', '告警', 'alert']);
  if (!alertSetting || isAlertSkippable(alertSetting)) return;

  const metricName = findColumn(metric, ['指标名称', '指标', 'name']) || '未知指标';
  const hasCondition = alertSetting.includes('条件') || alertSetting.includes('低于') || alertSetting.includes('超过') || alertSetting.includes('持续');
  const hasLevel = alertSetting.includes('级别') || alertSetting.includes('紧急') || alertSetting.includes('警告') || alertSetting.includes('通知');

  if (!hasCondition) add('warn', 'ALERT', `指标 "${metricName}" 标记了告警但缺少具体告警条件`);
  if (!hasLevel) add('warn', 'ALERT', `指标 "${metricName}" 标记了告警但缺少告警级别`);
}

function validateConsistency(metrics, events, designSections) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  // 1. 指标覆盖：每个指标的「所需事件」是否都有对应 YAML 事件
  for (const metric of metrics) {
    const requiredEvents = findColumn(metric, ['所需事件', '所需埋点', 'events']);
    if (!requiredEvents) continue;

    const eventNames = requiredEvents.split(/[、,，]/).map(stripLeadingParenthetical).filter(Boolean);
    for (const eName of eventNames) {
      const found = events.some(e => eventNameMatches(eName, e));
      if (!found) {
        const metricName = findColumn(metric, ['指标名称', '指标', 'name']) || '未知指标';
        add('error', 'COVERAGE',
          `指标 "${metricName}" 所需事件 "${eName}" 在 tracking-design.md 中未找到对应的 YAML 定义`);
      }
    }
  }

  // 2. 事件来源：每个 YAML 事件是否都能在 metric-spec 中找到来源
  for (const section of designSections) {
    if (section.metricSources.length === 0) {
      add('warn', 'SOURCE', `事件 "${section.name}" 未标注指标来源`);
    }
  }

  // 3. 告警完整性
  for (const metric of metrics) {
    validateMetricAlertDetail(metric, add);
  }

  return issues;
}

function findColumn(metric, candidates) {
  for (const key of candidates) {
    if (metric[key] !== undefined && metric[key] !== '') return metric[key];
  }
  return null;
}

// ---------------------------------------------------------------------------
// metric-spec.md 独立校验（Step 1 质量红线）
// ---------------------------------------------------------------------------

const REQUIRED_METRIC_HEADERS = ['指标名称', '业务含义', '计算公式', '重要程度', '所需事件', '数据消费方', '告警设置'];
const VALID_PRIORITIES = ['P0', 'P1', 'P2'];
const FUZZY_FORMULA_WORDS = ['相关事件', '若干', '一些', '部分'];

function validateSingleMetricQuality(metric, add) {
  const name = findColumn(metric, ['指标名称', '指标', 'name']) || '未知指标';

  if (/^[A-Z]{2,6}$/.test(name.trim())) {
    add('error', 'METRIC_QUALITY', `指标 "${name}": 使用缩写未展开业务含义`);
  }

  const formula = findColumn(metric, ['计算公式', '公式', 'formula']) || '';
  for (const fuzzy of FUZZY_FORMULA_WORDS) {
    if (formula.includes(fuzzy)) {
      add('error', 'METRIC_QUALITY', `指标 "${name}": 计算公式含模糊词"${fuzzy}"`);
    }
  }
  if (formula.endsWith('等')) {
    add('error', 'METRIC_QUALITY', `指标 "${name}": 计算公式以"等"结尾，所需事件不明确`);
  }

  const meaning = findColumn(metric, ['业务含义', '含义', 'meaning']) || '';
  if (meaning.length < 5) {
    add('warn', 'METRIC_QUALITY', `指标 "${name}": 业务含义过短（${meaning.length}字），无法回答"数字变化意味什么"`);
  }

  const priority = findColumn(metric, ['重要程度', '重要性', 'priority']) || '';
  if (!VALID_PRIORITIES.some(p => priority.includes(p))) {
    add('error', 'METRIC_QUALITY', `指标 "${name}": 重要程度 "${priority}" 不符合 P0/P1/P2 分级`);
  }

  const events = findColumn(metric, ['所需事件', '所需埋点', 'events']) || '';
  if (events.trim().endsWith('等')) {
    add('error', 'METRIC_QUALITY', `指标 "${name}": 所需事件以"等"结尾，事件列表不明确`);
  }

  if (priority.includes('P0') || priority.includes('P1')) {
    const alert = findColumn(metric, ['告警设置', '告警', 'alert']) || '';
    const alertLower = alert.toLowerCase().trim();
    if (alertLower === '有' || alertLower === '是' || alertLower === 'yes') {
      add('error', 'METRIC_QUALITY', `指标 "${name}": P0/P1 指标的告警设置仅写"${alert}"，缺少具体告警条件/级别/通知方式/阈值依据`);
    }
  }
}

function validateMetricSpec(metrics, metricSpecMd) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  if (metrics.length > 0) {
    const actualHeaders = Object.keys(metrics[0]);
    for (const required of REQUIRED_METRIC_HEADERS) {
      if (!actualHeaders.some(h => h.includes(required.toLowerCase()) || h === required.toLowerCase())) {
        add('error', 'METRIC_STRUCTURE', `指标表缺少必需列: "${required}"`);
      }
    }
  }

  for (const metric of metrics) {
    validateSingleMetricQuality(metric, add);
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 自然语言 7 项完整性检查
// ---------------------------------------------------------------------------

function validateNaturalLanguage(designSections, events) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  for (const section of designSections) {
    const label = `事件 "${section.name}"`;

    // 1. 事件描述（标题即描述）— 始终存在，跳过
    // 2. 触发场景
    if (!section.hasTriggerScene) {
      add('warn', 'NL_COMPLETENESS', `${label}: 缺少"触发场景"描述`);
    }
    // 3. 数据示例
    if (!section.hasDataExample) {
      add('warn', 'NL_COMPLETENESS', `${label}: 缺少"数据示例"`);
    }
    // 4. 指标来源
    if (section.metricSources.length === 0) {
      add('warn', 'NL_COMPLETENESS', `${label}: 缺少"指标来源"`);
    }
    // 5. 指标参与
    if (section.metricRoles.length === 0) {
      add('warn', 'NL_COMPLETENESS', `${label}: 缺少"指标参与"`);
    }
    // 6. 计算公式回溯（必须）
    if (section.formulaTracebacks.length === 0) {
      add('error', 'NL_COMPLETENESS', `${label}: 缺少"计算公式回溯"（必须项，Step 3 公式一致性检查的前提）`);
    }
    // 7. YAML 块
    if (!section.hasYamlBlock) {
      add('error', 'NL_COMPLETENESS', `${label}: 缺少 tracking-spec YAML 定义块`);
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 公式一致性检查：tracking-design 的回溯公式 vs metric-spec 原始公式
// ---------------------------------------------------------------------------

function compareFormulas(traceFormula, originalFormula) {
  const normalize = s => s.replace(/\s+/g, '').replace(/×/g, '*').replace(/÷/g, '/');
  return normalize(traceFormula) === normalize(originalFormula);
}

function checkFormulaMatch(section, traceMetricName, traceFormula, metricFormulaMap, add) {
  const originalFormula = metricFormulaMap.get(traceMetricName);
  if (originalFormula) {
    if (!compareFormulas(traceFormula, originalFormula)) {
      add('error', 'FORMULA',
        `事件 "${section.name}" 中回溯的"${traceMetricName}"公式与 metric-spec 不一致。` +
        `\n  回溯: ${traceFormula}\n  原始: ${originalFormula}`);
    }
    return;
  }
  // fuzzy match
  for (const [mName, mFormula] of metricFormulaMap) {
    if (mName.includes(traceMetricName) || traceMetricName.includes(mName)) {
      if (!compareFormulas(traceFormula, mFormula)) {
        add('error', 'FORMULA',
          `事件 "${section.name}" 中回溯的"${mName}"公式与 metric-spec 不一致。` +
          `\n  回溯: ${traceFormula}\n  原始: ${mFormula}`);
      }
      return;
    }
  }
  add('warn', 'FORMULA',
    `事件 "${section.name}" 回溯了指标"${traceMetricName}"，但在 metric-spec 中未找到该指标`);
}

function validateFormulaConsistency(designSections, metrics) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  // 构建指标名 → 公式映射
  const metricFormulaMap = new Map();
  for (const metric of metrics) {
    const name = findColumn(metric, ['指标名称', '指标', 'name']);
    const formula = findColumn(metric, ['计算公式', '公式', 'formula']);
    if (name && formula) {
      metricFormulaMap.set(name.trim(), formula.trim());
    }
  }

  for (const section of designSections) {
    for (const traceback of section.formulaTracebacks) {
      // 格式：指标名 = 公式内容  或  指标名：公式内容
      const split = splitOnFirstEquals(traceback);
      if (!split) continue;

      checkFormulaMatch(section, split.name, split.formula, metricFormulaMap, add);
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 描述↔YAML 语义匹配：自然语言关键词 vs tracker_type
// ---------------------------------------------------------------------------

const SEMANTIC_RULES = [
  { keywords: ['点击', '按钮', '按下', '选择', '选中'], expectedTypes: ['CLK'], hint: 'CLK（点击）' },
  { keywords: ['曝光', '可见', '展示', '显示', '进入可视'], expectedTypes: ['EXP'], hint: 'EXP（曝光）' },
  { keywords: ['浏览', '进入', '加载', '打开', '访问'], expectedTypes: ['BASE'], hint: 'BASE（浏览）' },
];

function validateDescriptionSemantics(designSections, events) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  // 构建事件名 → YAML event 映射
  const eventByName = new Map();
  for (const evt of events) {
    if (evt.name) eventByName.set(evt.name.trim(), evt);
  }

  for (const section of designSections) {
    const desc = section.description || '';
    const sectionName = section.name.trim();

    // 找到对应的 YAML event
    const yamlEvt = eventByName.get(sectionName);
    if (!yamlEvt?.tracker_type) continue;

    for (const rule of SEMANTIC_RULES) {
      const hasKeyword = rule.keywords.some(kw => desc.includes(kw) || sectionName.includes(kw));
      if (hasKeyword && !rule.expectedTypes.includes(yamlEvt.tracker_type)) {
        add('warn', 'SEMANTIC',
          `事件 "${sectionName}" 的描述含有"${rule.keywords.find(kw => desc.includes(kw) || sectionName.includes(kw))}"关键词，` +
          `通常对应 ${rule.hint}，但 YAML 中 tracker_type 为 ${yamlEvt.tracker_type}`);
      }
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 交互触点覆盖（Rule 1.1.1 软检查）：提醒可能漏了 PV / 用户行为埋点
// ---------------------------------------------------------------------------

function validateInteractionCoverage(events) {
  const issues = [];
  const add = (level, rule, msg) => issues.push({ level, rule, message: msg });

  const hasFrontendEvent = events.some(
    (e) => e.type === 'PAGE' || e.type === 'MODULE' || e.type === 'COMPONENT'
  );

  if (hasFrontendEvent) {
    const hasPage = events.some((e) => e.type === 'PAGE');
    const hasClk = events.some((e) => e.tracker_type === 'CLK');

    if (!hasPage) {
      add('warn', 'INTERACTION_COVERAGE',
        '定义了 MODULE/COMPONENT 事件但无 PAGE 事件。如该功能涉及独立页面，请检查是否遗漏页面级 PV 埋点（Rule 1.1.1）。' +
        '若是纯组件/子页面嵌入场景，请在 tracking-design.md 中显式说明例外理由。');
    }

    if (!hasClk) {
      add('warn', 'INTERACTION_COVERAGE',
        '定义了前端事件但无 CLK 事件。如该功能包含用户可点击元素（button、card、tab、menu item 等），' +
        '请检查是否遗漏用户行为埋点（Rule 1.1.1）。若该功能确无点击交互（如纯展示页），请在 tracking-design.md 中显式说明。');
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// 输出格式化
// ---------------------------------------------------------------------------

function calcResult(errors, warns) {
  if (errors.length > 0) return 'FAIL';
  if (warns.length > 0) return 'WARN';
  return 'PASS';
}

function formatOutput(yamlIssues, consistencyIssues, summary) {
  const allIssues = [...yamlIssues, ...consistencyIssues];
  const errors = allIssues.filter(i => i.level === 'error');
  const warns = allIssues.filter(i => i.level === 'warn');

  return {
    result: calcResult(errors, warns),
    summary: {
      ...summary,
      errors: errors.length,
      warnings: warns.length,
    },
    errors: errors.map(i => ({ rule: i.rule, message: i.message })),
    warnings: warns.map(i => ({ rule: i.rule, message: i.message })),
  };
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------

function runMetricSpecOnly(metricSpecOnlyPath) {
  const metricSpecPath = path.resolve(metricSpecOnlyPath);
  if (!fs.existsSync(metricSpecPath)) {
    console.error(JSON.stringify({ error: `文件不存在: ${metricSpecPath}` }));
    process.exit(1);
  }
  const metricSpecMd = fs.readFileSync(metricSpecPath, 'utf-8');
  const metrics = parseMetricSpecTable(metricSpecMd);
  const metricIssues = validateMetricSpec(metrics, metricSpecMd);

  const errors = metricIssues.filter(i => i.level === 'error');
  const warns = metricIssues.filter(i => i.level === 'warn');
  const result = calcResult(errors, warns);

  const output = {
    mode: 'metric-spec-only',
    result,
    summary: { metric_count: metrics.length, errors: errors.length, warnings: warns.length },
    errors: errors.map(i => ({ rule: i.rule, message: i.message })),
    warnings: warns.map(i => ({ rule: i.rule, message: i.message })),
  };
  console.log(JSON.stringify(output, null, 2));
  if (result === 'FAIL') process.exit(1);
  if (result === 'WARN') process.exit(2);
  process.exit(0);
}

function collectMetricSpecIssues(metricSpecArg, trackingDesignMd, allEvents) {
  const metricSpecPath = path.resolve(metricSpecArg);
  if (!fs.existsSync(metricSpecPath)) {
    console.error(JSON.stringify({ error: `文件不存在: ${metricSpecPath}` }));
    process.exit(1);
  }
  const metricSpecMd = fs.readFileSync(metricSpecPath, 'utf-8');
  const metrics = parseMetricSpecTable(metricSpecMd);
  const designSections = parseTrackingDesignSections(trackingDesignMd);

  return {
    metricCount: metrics.length,
    issues: [
      ...validateMetricSpec(metrics, metricSpecMd),
      ...validateConsistency(metrics, allEvents, designSections),
      ...validateFormulaConsistency(designSections, metrics),
    ],
  };
}

function runTrackingDesign(args) {
  if (!args['tracking-design']) {
    console.error(JSON.stringify({
      error: '必须指定 --tracking-design 或 --metric-spec-only 参数',
      usage: [
        'node tracking-spec-validator.js --tracking-design=path/to/tracking-design.md [--metric-spec=path/to/metric-spec.md]',
        'node tracking-spec-validator.js --metric-spec-only=path/to/metric-spec.md',
      ],
    }, null, 2));
    process.exit(1);
  }

  const trackingDesignPath = path.resolve(args['tracking-design']);
  if (!fs.existsSync(trackingDesignPath)) {
    console.error(JSON.stringify({ error: `文件不存在: ${trackingDesignPath}` }));
    process.exit(1);
  }
  const trackingDesignMd = fs.readFileSync(trackingDesignPath, 'utf-8');

  const yamlBlocks = extractYamlBlocks(trackingDesignMd);
  if (yamlBlocks.length === 0) {
    console.error(JSON.stringify({ error: '未找到 ```tracking-spec``` 代码块，请确认文档格式' }));
    process.exit(1);
  }

  let application = null;
  const allEvents = [];
  for (const block of yamlBlocks) {
    const parsed = parseTrackingYaml(block);
    if (parsed.application) application = parsed.application;
    allEvents.push(...parsed.events);
  }

  const yamlIssues = validateYaml({ application, events: allEvents });

  let consistencyIssues = [];
  let metricCount = 0;
  if (args['metric-spec']) {
    const collected = collectMetricSpecIssues(args['metric-spec'], trackingDesignMd, allEvents);
    metricCount = collected.metricCount;
    consistencyIssues = collected.issues;
  }

  const designSectionsForNL = parseTrackingDesignSections(trackingDesignMd);
  const nlIssues = validateNaturalLanguage(designSectionsForNL, allEvents);
  const semanticIssues = validateDescriptionSemantics(designSectionsForNL, allEvents);
  const interactionCoverageIssues = validateInteractionCoverage(allEvents);

  const summary = {
    application: application || '未指定',
    event_count: allEvents.length,
    metric_count: metricCount,
    yaml_blocks: yamlBlocks.length,
    consistency_checked: !!args['metric-spec'],
    nl_sections: designSectionsForNL.length,
  };

  const output = formatOutput(
    [...yamlIssues, ...nlIssues, ...semanticIssues, ...interactionCoverageIssues],
    consistencyIssues,
    summary,
  );
  console.log(JSON.stringify(output, null, 2));

  if (output.result === 'FAIL') process.exit(1);
  if (output.result === 'WARN') process.exit(2);
  process.exit(0);
}

function main() {
  const args = parseArgs();
  if (args['metric-spec-only']) {
    runMetricSpecOnly(args['metric-spec-only']);
  } else {
    runTrackingDesign(args);
  }
}

main();
