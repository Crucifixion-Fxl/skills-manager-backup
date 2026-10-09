# Memory 写入入口审计（2026-10-07）

范围：本仓库 `skills/` 内的 SKILL.md、references 及脚本；检索 AGENTS.md、CLAUDE.md、auto-memory 和 memory 写入措辞，再逐一区分读/写入口。非本插件第三方 skills 不在修改范围。

| Skill | 写入范围 | 本次处理 |
|---|---|---|
| dev-infra | 根/一级目录规则 | 极简规约；子目录≤20行 |
| dev-workflow | 初始化/阶段规则 | 极简规约 |
| architect | 一级目录/auto-memory | 极简规约；保留既有≤20行模板 |
| story-craftsman | docs/AGENTS.md | 模板缩为3条 |
| mylibrary | 根规则/CLAUDE引用 | 模板缩为4条；写作详情放README |
| tool-skill-creator | 工具/认证索引 | 极简规约；保留每工具一行 |
| tool-exploration | 获授权的auto-memory | 极简规约；保留显式选择条件 |
| data-driven-investigation | 调查结论auto-memory | 极简规约；结论/范围/证据入口 |
| ops-guardrails | 已拍板决策memory | 极简规约；不放宽门禁 |
| feishu-integration-testing | 推荐的项目测试约束 | 极简规约；流程留在skill |
| code-review/migration-review | 建议的迁移规范 | 登记表/checklist放文档；memory一句入口；只读不写 |

其余命中项是读取规则、历史案例、文档来源或内存指标，不新增写入行为。无需因本次审计批量改写用户已有 memory；不把此报告写进用户 memory。

最新决定：Git 大文件规则不写项目 memory，只保留在 `gitlab-mr` 的 reference；`code-submit` / `code-review` 引用检查。上述三个入口不再因大文件规则成为 memory writer。
