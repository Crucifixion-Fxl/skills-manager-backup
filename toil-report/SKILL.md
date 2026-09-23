---
name: toil-report
description: >
  从 Claude Code JSONL 对话记录自动提取运维工作数据，生成 Toil 日报（Timeline 甘特图）
  或周报（SRE Toil 分析 HTML）。日报展示一天的工作全景、并行度和耗时分布；
  周报展示 Toil 分类趋势、自动化潜力和重复模式。
  当用户说"toil report"、"toil日报"、"toil周报"、"运维日报"、"运维周报"时触发。
argument-hint: "[YYYY-MM-DD | weekly] (默认今天)"
allowed-tools: Bash, Agent
---

## Description

从 Claude Code 的 JSONL 对话记录中自动提取运维工作数据，经过 per-task Agent 归纳（分类/摘要/自动化潜力）和聚合分析 Agent（洞察/方案/路线图），生成自包含的 HTML 报告。日报聚焦单天工作全景（Timeline 甘特图 + KPI + Insight Cards），周报聚焦 Toil 趋势分析（8 个 Section：总览/分类/热力图/自动化/任务清单/被动vs主动/重复追踪/路线图）。核心原则：日报中记录的全部是人工介入的 Toil，自动化完成的不会出现，目标是让日报越来越短。

**数据源**：Claude Code 2.1.97+ 的 `system.away_summary` (recap) 条目。每条 recap 是 Claude Code 在用户停顿 ≥3min 后自动生成的"目标/进展/下一步"三段式任务总结。extract.py 按时间窗把 recap 绑定到 task，**无 recap 的 task 直接丢弃**（视作短任务/非 toil）。好处：per-task Agent 投喂量从 700 字降到 ~290 字（-58% tokens），摘要质量直接来自官方 recap。

## Rules

- **工作日边界**：单天定义为 08:00 BJT → 次日 08:00 BJT，凌晨 00:00-07:59 的记录归属前一个工作日
- **噪声过滤**：自动排除空任务（duration=0 且 cost=0）、sre-agent 自动化运行（user turns>100 + 系统首条消息）、内置 slash 命令片段、**无 recap 的任务**
- **recap 绑定**：recap.timestamp 落在 task `[first_ts, last_ts + 5min]` 时间窗内即绑定（+5min 容忍 recap 在 3min idle 后触发超出 last_ts 的情况）
- **Agent 数量**：N≤20 inline 归纳，N≤80 用 2 Agent，N≤160 用 4 Agent，N>160 最多 6 Agent
- **Agent 并发**：num_agents≥2 时**必须在单条 assistant 响应里同时发出所有 Agent tool_use block**，禁止拆成多条响应（会串行化，浪费 50%+ 时间）
- **Agent I/O 约定**：Agent 通过**读文件**接收任务输入（`/tmp/toil_batch_N_in.txt` 或 `/tmp/toil_compact.txt`），通过 **Bash heredoc 写独立输出文件**返回结果——**不使用 Write 工具**（subagent 对已有路径的 Write 会被 "File has not been read yet" 拦截）
- **分析 Agent 模式**：daily 只输出 top_insights（3 条洞察），weekly 输出全量分析（daily_summaries + P0/P1 + 自动化方案 + 路线图）
- **降级策略**：任何 Agent 输出异常时写入空 JSON，render 脚本跳过对应区域，不阻塞报告生成
- **安全**：HTML 输出统一 escape_html + safe_js 转义

## Examples

### Good

```
用户：/toil-report
→ 生成今天的日报 /tmp/toil-daily-2026-04-07.html

用户：/toil-report 2026-04-03
→ 生成指定日期的日报 /tmp/toil-daily-2026-04-03.html

用户：/toil-report weekly
→ 生成本周周报 /tmp/toil-weekly-2026-04-07.html（周一到今天）
```

### Bad

```
用户：/toil-report 上周
→ 不支持中文日期，应使用 YYYY-MM-DD 格式或 "weekly"

用户：/toil-report 2026-04
→ 不支持月份级别，只支持单天或整周
```

## Step 1 — 参数解析（必须执行，不得跳过）

无论参数是否已明确，必须执行此解析块并导出 `$TARGET_DATE` 变量，后续所有 Step 都依赖它（包括 bash 分支判断和文件命名）。

```bash
TARGET_DATE=$(TOIL_ARGS="$ARGUMENTS" python3 -c "
import os, re
from datetime import date
args = os.environ.get('TOIL_ARGS', '').strip()
if 'weekly' in args.lower():
    print('weekly')
elif re.match(r'\d{4}-\d{2}-\d{2}', args):
    print(args[:10])
else:
    print(date.today().isoformat())
")
echo "MODE: $TARGET_DATE"
```

## Step 2 — 执行提取脚本

```bash
SKILL_DIR="$HOME/.claude/skills/toil-report"

if [ "$TARGET_DATE" = "weekly" ]; then
    python3 "$SKILL_DIR/scripts/extract.py" --weekly --json > /tmp/toil_raw.json
else
    python3 "$SKILL_DIR/scripts/extract.py" --date "$TARGET_DATE" --json > /tmp/toil_raw.json
fi
```

检查结果：
```bash
python3 -c "
import json
d = json.load(open('/tmp/toil_raw.json'))
print(f'任务数: {d[\"task_count\"]}，日期: {d[\"date_range\"]}')
"
```

若 task_count = 0，生成空报告（KPI 全为 0，主区域显示"当天无运维记录"）并回复文件路径后停止。

## Step 3 — 并行 Agent 归纳

### 3.1 计算 Agent 数量 + 切 batches + 写入 batch 输入文件

```bash
SKILL_DIR="$HOME/.claude/skills/toil-report"

python3 - <<'PY'
import json, math, os

d = json.load(open('/tmp/toil_raw.json'))
tasks = d['tasks']
N = len(tasks)

if N <= 20:
    num_agents = 1
elif N <= 80:
    num_agents = 2
elif N <= 160:
    num_agents = 4
else:
    num_agents = min(6, math.ceil(N / 50))

batch_size = math.ceil(N / num_agents) if num_agents else 0

def format_task(t):
    lines = [f"任务 #{t['id']}:"]
    recaps = t.get('recaps') or []
    if recaps:
        lines.append(f"  初始进展: {recaps[0]['content']}")
        if len(recaps) >= 2:
            lines.append(f"  最终进展: {recaps[-1]['content']}")
    lines.append(f"  Skills: {', '.join(t.get('skills') or []) or '-'}")
    lines.append(f"  工具调用: {t.get('tools') or '-'}")
    tr = t.get('turns') or {}
    lines.append(f"  轮次: {tr.get('user', 0)}u/{tr.get('tool', 0)}t")
    lines.append(f"  耗时: {t.get('duration_min', 0)}min")
    return '\n'.join(lines)

os.makedirs('/tmp', exist_ok=True)
for i in range(num_agents):
    batch = tasks[i * batch_size : (i + 1) * batch_size]
    with open(f'/tmp/toil_batch_{i}_in.txt', 'w') as f:
        for t in batch:
            f.write(format_task(t) + '\n\n')

with open('/tmp/toil_batch_meta.json', 'w') as f:
    json.dump({'N': N, 'num_agents': num_agents, 'batch_size': batch_size}, f)

print(f"N={N}, num_agents={num_agents}, batch_size={batch_size}")
PY
```

### 3.2 若 num_agents = 1（N ≤ 20）

直接在当前会话内读 `/tmp/toil_batch_0_in.txt` + `$SKILL_DIR/references/taxonomy.md` 完成归纳，每个任务输出一行 JSON 追加到 `/tmp/toil_enriched_inline.txt`，然后用 bash heredoc 合并到 `/tmp/toil_enriched.json` 后跳到 Step 4.5。

### 3.3 若 num_agents ≥ 2

**🚨 并发强制约束**：必须在**单条 assistant 响应里**同时发出所有 `num_agents` 个 Agent tool_use block（一条响应 = 多个 tool_use block）。**禁止拆成多条响应**——串行会浪费约 50% 时间。若 focus mode 下自然倾向于"先宣告再调用"，直接跳过宣告，在同一条响应里打包所有 Agent 调用。

每个 Agent 的 prompt 模板（**短**，分类体系由 Agent 自读文件）：

---
你是 Toil 分类专家。

**任务**：读取 `/tmp/toil_batch_{i}_in.txt` 中的全部任务，按 `~/.claude/skills/toil-report/references/taxonomy.md` 定义的 18 类分类体系和归纳规则，对每个任务输出一行 JSON。

**输出**：用 Bash heredoc 写入 `/tmp/toil_batch_{i}_out.txt`（**每行一个 JSON，无代码块标记，无其他文字**）：

```bash
cat > /tmp/toil_batch_{i}_out.txt <<'EOF'
{"id": N, "summary": "...", "category": "...", "trigger": "被动", "automation": "semi", "repeat_key": null}
{"id": N+1, ...}
...
EOF
```

**硬性要求**：
1. 先 Read `~/.claude/skills/toil-report/references/taxonomy.md` 获取分类体系和归纳规则，不要凭记忆分类
2. 再 Read `/tmp/toil_batch_{i}_in.txt` 获取任务列表
3. **禁止使用 Write 工具**写输出文件（subagent 会被 "File has not been read yet" 拦截），只用 Bash heredoc
4. 输出任务数必须等于输入任务数，id 字段原样保留，不得合并或跳过
5. category 必须严格使用 taxonomy.md 的 18 个分类名之一

完成后返回一句确认（"已写入 N 条到 /tmp/toil_batch_{i}_out.txt"），不要把 JSON 正文回显到响应里。

---

## Step 4 — 合并归纳结果 + 条数校验

各子 Agent 独立写出 `/tmp/toil_batch_*_out.txt` 后，主会话用 cat 合并并做 task_count 核对（若少于输入任务数则报错，防止遗漏）：

```bash
python3 - <<'PY'
import json, glob

result = []
for path in sorted(glob.glob('/tmp/toil_batch_*_out.txt')):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('{'):
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

meta = json.load(open('/tmp/toil_batch_meta.json'))
expected = meta['N']
actual = len(result)
ids = {r['id'] for r in result}
missing = [i for i in range(1, expected + 1) if i not in ids]

with open('/tmp/toil_enriched.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False)

print(f"合并完成 {actual}/{expected} 条")
if missing:
    print(f"⚠️  缺失任务 id: {missing[:10]}{'...' if len(missing) > 10 else ''} — 对缺失项做 fallback（category='未分类', summary 取 first_msg 前 30 字）")
PY
```

若校验发现缺失 id，用 fallback 补齐（见"错误处理"小节），然后重新写入 `toil_enriched.json`。

## Step 4.5 — 分析 Agent（聚合洞察）

### 4.5.1 主会话生成 compact 文件（Agent 自读，避免 prompt 内联长数据）

```bash
python3 - <<'PY'
import json

enriched = json.load(open('/tmp/toil_enriched.json'))
raw = json.load(open('/tmp/toil_raw.json'))
by_id = {t['id']: t for t in raw['tasks']}

with open('/tmp/toil_compact.txt', 'w') as f:
    for e in enriched:
        t = by_id.get(e['id'], {})
        rk = e.get('repeat_key') or 'null'
        f.write(f"#{e['id']} | {e['category']} | {e['summary']} | {t.get('duration_min', 0)}min | "
                f"{e['trigger']} | {e['automation']} | {rk} | {t.get('date', '')}\n")

total_dur = sum(t.get('duration_min', 0) for t in raw['tasks'])
num_days = len(set(t.get('date', '') for t in raw['tasks'] if t.get('date')))
meta = {
    'mode': 'weekly' if raw['date_range'].split(' ~ ')[0] != raw['date_range'].split(' ~ ')[1] else 'daily',
    'date_range': raw['date_range'],
    'task_count': raw['task_count'],
    'total_duration_min': total_dur,
    'num_days': num_days,
}
with open('/tmp/toil_compact_meta.json', 'w') as f:
    json.dump(meta, f, ensure_ascii=False)
print(f"compact: {raw['task_count']} tasks | {total_dur}min | {num_days} days | mode={meta['mode']}")
PY
```

若 `$TARGET_DATE` = "weekly"，强制把 meta 的 mode 覆盖为 weekly（前一行的启发式仅用日期宽度判断）。

### 4.5.2 派 1 个分析 Agent（prompt 只给路径，不内联数据）

**🚨 注意**：分析 Agent 必须**通过 Read 工具读取 `/tmp/toil_compact.txt` 和 `/tmp/toil_compact_meta.json`**，**不能**把任务数据内联到 prompt 里（这次执行就是因为主会话手动拼 prompt 导致 #31 被复制两次）。

Agent prompt 模板：

---
你是 SRE Toil 分析专家。

**准备工作**（必须先执行）：
1. Read `/tmp/toil_compact_meta.json` 获取 `mode`（daily 或 weekly）、`date_range`、`task_count`、`total_duration_min`、`num_days`
2. Read `/tmp/toil_compact.txt` 获取全部任务列表（每行一条任务）
3. **禁止**把任务列表写回响应正文或再次拼回下一步的输入——直接基于 Read 得到的内容推理

**严格输出一个 JSON 对象。**

### 必须输出（daily + weekly）：

"top_insights": 3 条最值得关注的洞察，每条：
  - highlight: 关键数字（如 "30%"、"6.6h"）
  - color: 情绪色（红#f85149=问题/警示, 蓝#58a6ff=投资/中性, 橙#f0883e=可改善, 绿#3fb950=正面）
  - text: 一句话解读（≤40字），要有因果或建议，不是复述数据

洞察角度（按优先级）：
  1. 如有故障/告警耗时占比>25%，必须提及
  2. 被动vs主动比例
  3. 可自动化的最大潜力点
  如有 P0/P1 级事件（生产服务中断或严重降级的故障修复），替换一条

### 仅 weekly 模式额外输出：

"daily_summaries": 每天一个对象
  - hours: 当天总耗时（小时，1位小数）
  - color: >6h → "#f85149", 3-6h → "#ffa657", <3h → "#3fb950"
  - note: 一句话特征（≤20字）

"p0_p1_events": 一行文字总结（如 "1P0 + 2P1"），无则 "无"
"p0_p1_detail": 事件名称逗号分隔，无则 ""
  判断标准：事件响应/问题排查/故障修复类，综合耗时和影响判断。
  生产环境服务中断/数据丢失 → P0；性能严重降级/功能部分不可用 → P1；
  仅耗时长但影响有限（如 CI 失败）→ 不算 P0/P1

"biggest_item": {"name": "任务摘要", "hours": N}

"automation_details": 按 full/semi/manual 三级，同分类合并：
  - full 级: item/duration_min/solution/impl_cost/weekly_save
  - semi 级: item/duration_min/solution/reduce_to
  - manual 级: item/duration_min/tool_assist

"roadmap": 基于本周数据推断
  - completed: 从"自动化开发"类任务提取已完成改善项（2-4条）
  - next_week: 从 full 级推断应优先做的（2-3条，附预期节省）
  - future: 从 semi 级推断中期规划（2-3条）
  - prediction:
    - current_hours: 本周总耗时（小时）
    - weeks: 7 周标签（本周 WXX 起）
    - no_automation: 不自动化预测（每周+5%）
    - with_automation: 按计划自动化预测（每项扣除 weekly_save）

**输出方式**：用 Bash heredoc 把 JSON 写入 `/tmp/toil_analysis.json`（**禁止使用 Write 工具**）：

```bash
cat > /tmp/toil_analysis.json <<'EOF'
{ ... 你生成的 JSON ... }
EOF
```

完成后返回一句确认（"已写入 /tmp/toil_analysis.json"），**不要把 JSON 回显到响应正文**（避免触发 Claude Code 把 JSON 再 echo 回主会话、浪费 tokens）。
---

### 4.5.3 主会话校验分析 Agent 输出

```bash
python3 - <<'PY'
import json, re, pathlib
path = pathlib.Path('/tmp/toil_analysis.json')
try:
    obj = json.loads(path.read_text())
    # sanity check：top_insights 至少 1 条
    if not obj.get('top_insights'):
        raise ValueError('top_insights missing or empty')
    print(f"分析完成: {len(obj.get('top_insights', []))} 条 insight")
except Exception as e:
    print(f"分析 Agent 输出异常 ({e})，降级为空对象")
    path.write_text('{}')
PY
```

## Step 5 — 渲染 HTML

```bash
SKILL_DIR="$HOME/.claude/skills/toil-report"

if [ "$TARGET_DATE" = "weekly" ]; then
    LAST_DATE=$(python3 -c "import json; d=json.load(open('/tmp/toil_raw.json')); print(d['date_range'].split(' ~ ')[1])")
    python3 "$SKILL_DIR/scripts/render_weekly.py" /tmp/toil_raw.json /tmp/toil_enriched.json /tmp/toil_analysis.json > "/tmp/toil-weekly-$LAST_DATE.html"
    open "/tmp/toil-weekly-$LAST_DATE.html"
    echo "/tmp/toil-weekly-$LAST_DATE.html"
else
    python3 "$SKILL_DIR/scripts/render_daily.py" /tmp/toil_raw.json /tmp/toil_enriched.json /tmp/toil_analysis.json > "/tmp/toil-daily-$TARGET_DATE.html"
    open "/tmp/toil-daily-$TARGET_DATE.html"
    echo "/tmp/toil-daily-$TARGET_DATE.html"
fi
```

**只回复文件路径，不在对话中打印报告内容。**

## 错误处理

- extract.py 执行失败 → 报错说明原因，停止执行
- task_count = 0 → 生成空报告，标注"当天无运维记录"
- 归纳 Agent 输出缺 id / 条数不足 → 主会话为缺失 id 补 fallback（`summary` 用 first_msg 前 30 字，`category="未分类"`, `trigger="主动"`, `automation="manual"`, `repeat_key=null`），追加到 enriched.json 再继续
- render 脚本执行失败 → 报错说明原因
- **分析 Agent 输出异常 / `top_insights` 为空 → analysis.json 写入 `{}`，render 降级为纯数据展示**
- **Token 统计异常 → 跳过费用计算，在 HTML 中标注"费用数据不可用"**
- **Agent 用 Write 被 "File has not been read yet" 拦截** → 这是预期行为，说明 Agent 违反了"必须用 Bash heredoc"规则，需修 Agent prompt 而非解除拦截
