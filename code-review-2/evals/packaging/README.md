# 设计交付物分项行为评测

这是中央 code-review 专项的 synthetic 场景评测，不是 GS001 真实 E2E，也不是 PDF/Adobe 软件验收。
`cases.json` 中“已读取”的文字描述是给定观察，不代表本次跑过原生设计软件。

## 冻结与执行

`frozen.json` 保存修改前 commit 和 cases/rubric 摘要；先生成并冻结输入，再修改 skill。
两次独立 subagent 各自读取 skill 与 cases，未给 rubric、设计文档或另一轮输出。
baseline 入口取自冻结 commit 的快照，候选读取修改后的 skill 与新 reference。
两轮均只评估设计交付分项，调用方已完成的 Issue 门禁及其他维度不重复测；禁止外部写入。
运行引擎为本次 Codex 会话继承模型，工具未提供可靠型号/版本标识，不虚构模型版本。

独立任务要求每例输出 id、status、findings、gaps、release_authorized、rationale。
结果原文保存在 results/baseline.json 和 results/candidate.json。
这不是完整受限 CI 工具轨迹，也不证明已发布到生产 reviewer。

## 评分

运行 `python3 skills/quality/code-review/evals/packaging/grade.py` 检查输入未漂移、标签一致性和未授权放行。
该脚本只比较决策状态，不靠关键词判语义正确；rubric 的 must 项由主评估者逐条对原文审定。
NEEDS_HUMAN 与 NOT_CHECKED 的边界单独记录，不能把标签修正夸大为修复了错误放行。
CI helper/真实PDF与原生软件运行由配套仓的独立测试/真实案例承担。
