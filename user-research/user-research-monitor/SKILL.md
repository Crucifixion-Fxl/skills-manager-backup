---
name: user-research-monitor
description: >
  在问卷回收期间从 Google Forms 或 Typeform 采集并归一化回答，运行项目自定义的数据质量检查，
  输出样本健康度、复核清单和结构化分析输入。适用于问卷回收量监测、数据质量评估和报告就绪检查；
  不用于问卷设计、开放题编码或最终研究报告。
---

# User Research Monitor

## Description

用于问卷开始回收后做三件事：采集与归一化回答、运行项目自定义的数据质量检查、输出样本健康度和结构化分析输入。它是后续从用户研究工作流中拆出的执行型子 skill，不是最初问卷设计能力的必要组成。

### 能力边界

- 提供 Google Forms / Typeform 采集适配器、统一回答格式、确定性质量规则引擎、描述统计函数和项目模板。
- 不内置题号、功能名、cohort 语义、业务阈值或研究结论。
- 不设计问卷，不对原始开放文本做主题编码，不写最终研究报告。
- 问卷设计路由到 `survey-research-workflow`；开放文本编码路由到 `user-research-coding`；研究报告路由到 `user-research-report`。

### 核心产物

- 原始时间点快照与平台 schema。
- `review_tab.csv`：按回答聚合的人工复核清单。
- `review_tab_detail.csv`：逐规则命中证据。
- `quality_run_manifest.json`：规则文件哈希、策略、样本计数和显式跳过记录。
- `health_report.md`：数据健康度，不含业务结论。
- `findings_data.json`：仅当项目提供自己的 `scripts/analyze.py` 时生成。

## Rules

### 1. 项目配置与通用引擎分离

每个项目在自己的目录维护：

- `config/cohort_mapping.yaml`
- `config/schema_overrides.yaml`
- `config/quality_rules.yaml`
- `scripts/analyze.py`（可选）

skill 内只保留通用实现和无业务含义的模板。不得把某次问卷的 Q 编号、功能列表、选项、cohort 定义或阈值写入 skill 脚本。

### 2. 确定性题目映射

`enrich_schema.py` 只接受两类项目声明：

- 平台题目 ID 与 logical_qid 的精确映射；
- 规范化后的完整题面精确匹配。

不使用题目顺序、相邻题、前几个词或模糊语义猜测。重复映射和歧义映射必须报错。未映射题目可以保留，但质量规则引用的 logical_qid 缺失时默认阻断；只有项目明确写 `on_missing: skip` 才能跳过。

### 3. 显式、可验证的质量规则

`quality_rules.yaml` 使用 `type + params` 声明规则。支持的规则类型以 `scripts/quality_engine.py` 中的 `SUPPORTED_RULE_TYPES` 为唯一事实来源。

- 未知规则类型、重复 ID、错误严重度、未替换占位符或缺少参数必须在处理回答前报错。
- 不允许通过表达式求值、任意代码或 LLM 猜测执行规则。
- 不宣称“AI 文本识别”、默认排序检测或其他未实现能力。
- 每次运行必须写入配置 SHA-256，以便报告口径可追溯。

### 4. 默认只标记

默认 `policy: flag_only`，任何规则都只进入人工复核，不自动删除答卷。只有研究 owner 明确采用双口径时才设 `dual_threshold`：

- S0：保守与严格口径都剔除；
- S1：仅严格口径剔除；
- S2：仅标记。

质量信号不是“受访者不诚实”的证明。报告应披露规则、阈值、命中量及对主要结论的敏感性。

### 5. 运行流程

1. 读 research brief 和冻结后的问卷结构。
2. 从 `templates/` 复制并填写项目配置；删除不适用的示例。
3. 运行 `python -m scripts.main analyze --project-dir <project_dir>`。该命令默认不执行项目目录中的代码；如确需业务分析，先检查 `scripts/analyze.py` 的来源与内容，再显式添加 `--run-project-analyzer`。
4. 检查 `quality_run_manifest.json`、两份 review tab 和 health report。
5. 项目分析需要开放文本时，用 `open_text_dump` 去标识化导出，再调用 `user-research-coding` 生成 `coding-package.json`。
6. 将 `findings_data.json`、`coding-package.json`（如有）、research brief 和数据健康度交给 `user-research-report`。

### 6. 立即停止条件

遇到以下任一情况，不得继续产出结论：

- 问卷结构仍在变化，或项目需要支持的决策不清楚；
- 项目配置仍有占位符、未知规则类型或没有启用任何规则；
- 规则引用的 logical_qid 未映射且没有明确允许跳过；
- cohort 定义或平台 form ID 不完整；
- 采集失败、分析脚本失败或规则运行没有 manifest；
- OAuth 凭证或 API token 出现在 skill、项目配置或输出快照中。

### 7. 平台与凭证

- Typeform token 只从 `TYPEFORM_ACCESS_TOKEN` 读取；API base 必须是官方地址。
- Google OAuth client secret 和 token 只保存在本地安全配置，不随 skill 或项目产物分发。
- Typeform Responses API 可能延迟，不将一次拉取描述成实时监控。

### 8. 模板

- `templates/cohort_mapping.yaml.template`：平台来源与业务分群。
- `templates/schema_overrides.yaml.template`：项目 logical_qid 精确映射。
- `templates/quality_rules.yaml.template`：支持规则的关闭态示例；填写后至少启用一条。
- `templates/analyze.py.template`：按研究问题组织描述统计与预先计划的交叉分析。
- `templates/monitor_status.json.template`：供上层 workflow 判断报告就绪状态。

## Examples

### Bad

```python
feature_questions = discovered_questions[8:17]
```

根据固定位置猜测某次项目的功能题，换问卷后可能静默误判。

### Good

```yaml
version: 1
policy: flag_only
rules:
  - id: mutually_exclusive_answer
    type: exclusive_choice_mixed
    label: 互斥选项与其他选项同时选择
    severity: S1
    params:
      logical_qid: SHARING_CHANNELS
      exclusive_values: ["I would not share"]
```

题目与答案属于项目配置。若 `SHARING_CHANNELS` 没有可靠映射，引擎会失败，而不是假装执行成功。
