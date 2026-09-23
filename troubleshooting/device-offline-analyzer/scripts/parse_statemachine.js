#!/usr/bin/env node
/**
 * parse_statemachine.js
 * 解析状态机日志响应，提取关键字段并输出表格
 *
 * 用法：
 *   cat response.json | node scripts/parse_statemachine.js
 *   node scripts/parse_statemachine.js < response.json
 *   node scripts/parse_statemachine.js response.json
 *
 * 输出：Markdown 表格，时间转换为北京时间（UTC+8），按时间倒序排列
 */

const fs = require('fs');

function toBeijingTime(isoStr) {
  const dt = new Date(isoStr);
  const ms = isoStr.split('.')[1].replace('Z', '');
  dt.setTime(dt.getTime() + 8 * 3600 * 1000);
  const pad = n => String(n).padStart(2, '0');
  return (
    dt.getUTCFullYear() + '-' +
    pad(dt.getUTCMonth() + 1) + '-' +
    pad(dt.getUTCDate()) + ' ' +
    pad(dt.getUTCHours()) + ':' +
    pad(dt.getUTCMinutes()) + ':' +
    pad(dt.getUTCSeconds()) + '.' + ms
  );
}

function parse(json) {
  const results = json?.result?.data?.results;
  if (!Array.isArray(results) || results.length === 0) {
    console.error('未找到 result.data.results 数据');
    process.exit(1);
  }

  const rows = results.map(r => ({
    time: toBeijingTime(r._source['@timestamp']),
    fromState: r._source.fromState?.name ?? '-',
    toState: r._source.toState?.name ?? '-',
    event: r._source.event?.name ?? '-',
  }));

  // 按时间倒序
  rows.sort((a, b) => b.time.localeCompare(a.time));

  // 输出 Markdown 表格
  console.log('| 北京时间 | fromState | toState | event |');
  console.log('|----------|-----------|---------|-------|');
  for (const r of rows) {
    console.log(`| ${r.time} | ${r.fromState} | ${r.toState} | ${r.event} |`);
  }
}

// 读取输入
let input = '';
if (process.argv[2]) {
  input = fs.readFileSync(process.argv[2], 'utf8');
  parse(JSON.parse(input));
} else {
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => { input += chunk; });
  process.stdin.on('end', () => { parse(JSON.parse(input)); });
}
