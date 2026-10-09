# Forward eval 记录

使用独立评价 Agent，20 个离线样例、数字 ID，不向 evaluator 提供 expected、assertions、
grader 或其它预测。baseline 只读现有 reportportal-analysis Skill；candidate 只读新 Skill
与 taxonomy/output contract。输入脚本是 `evals/prepare_inputs.py`。

| 评测 | 通过 / 总数 | 说明 |
| --- | --- | --- |
| 最终 baseline | 19 / 20 | 独立新 Agent；旧 Skill 未定义三维字段，仍按证据作出推断 |
| 最终 candidate | 20 / 20 | 第一轮独立数字 ID 盲测 19 条，再补一条明确 gate 的对照样例 |

迭代发现：

1. 早期 19 例基线 19/19，candidate 18/19：GENERATION_STRATEGY_ERROR 与执行时
   LOCATOR_ERROR 重叠。修正 Skill 的操作根因优先规则，不改该例独立预期。
2. 早期 case ID 带语义标签，成绩可能受提示影响，只作为探索性记录。正式输入只保留数字
   ID、prompt 和 evidence_ids；独立数字 ID 测试让歧义暴露。
3. binding 缺失例只说明 implement 与未注册，没有校验/dispatch 时点。candidate 正确保留
   failure_stage=UNKNOWN，暴露了原 gold 过度推断。按可观察证据修正 gold，并增加第20例：
   明确 gate_steps_registered 在生成校验阶段拒绝、尚未 dispatch，预期 GENERATION。

本目录包含完整最终预测和逐项 grade，可用 `evals/grade.py` 重算。
数字 ID 盲测 candidate 的前19例来自同一个独立 Agent，追加例不是重新独立评价整个套件。
这是有限合成样例的行为检验，不能据此声称线上根因准确率100%；真实日志、附件与周报
端到端验收单独记录。没有用生产模型 key 或设备资源做这批评测。
