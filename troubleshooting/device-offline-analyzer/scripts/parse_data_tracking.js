#!/usr/bin/env node
/**
 * parse_data_tracking.js
 * 解析 data tracking 事件响应，提取关键字段并输出表格
 *
 * 用法：
 *   cat response.json | node scripts/parse_data_tracking.js
 *   node scripts/parse_data_tracking.js < response.json
 *   node scripts/parse_data_tracking.js response.json
 *
 * 输出：Markdown 表格，时间转换为北京时间（UTC+8），按原始顺序排列
 */

const fs = require('fs');
const path = require('path');

const filePath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, 'response.json');
const raw = fs.readFileSync(filePath, 'utf-8');
const json = JSON.parse(raw);

const records = json?.result?.data?.data ?? [];

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts + 8 * 60 * 60 * 1000);
    return d.toISOString().slice(0, 19).replace('T', ' ');
}

function formatUnstructData(raw) {
    if (!raw) return '';
    try {
        const parsed = JSON.parse(raw);
        const data = parsed?.data ?? parsed;
        return JSON.stringify(data);
    } catch {
        return raw;
    }
}

function escape(val) {
    return String(val ?? '').replace(/\|/g, '\\|');
}

const header = '| event_time | event_name | firmware_id | unstruct_event_data |';
const divider = '|---|---|---|---|';

const rows = records.map(r => {
    const time = escape(formatTime(r.event_time));
    const name = escape(r.event_name);
    const fw = escape(r.firmware_id);
    const data = escape(formatUnstructData(r.unstruct_event_data));
    return `| ${time} | ${name} | ${fw} | ${data} |`;
});

const table = [header, divider, ...rows].join('\n');
console.log(table);
