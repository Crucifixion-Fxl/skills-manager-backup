# CI 集成约束与进度标记协议

> 本文档定义 CI 环境下 code-review skill 的运行约束和进度标记协议。
> 仅在 CI 场景（`$REVIEW_DATA_DIR` 存在，或 prompt 中包含 `ci_project_id`）下生效。
> 本地执行 `/code-review` 时忽略本文档。

## Review job 与 Pipeline 独立性

Code Review job 与 MR aggregate Pipeline 是两条并行链路：

- Review job 不应等待 Pipeline 先变绿才启动；Pipeline `running`、`failed` 或 `blocked` 不构成 Review job 的前置条件。
- Review job 只依赖 review-data 预处理结果或自身所需的 diff 数据，不应使用 `when: on_success` 因前置 Pipeline 失败而跳过 Review。
- Pipeline 失败由 MR merge gate 负责阻塞合并；Review 结果只表达代码审查结论，不重复记录 Pipeline 失败，也不把 aggregate status 当作 Review 结论。
- 只有用户明确要求 CI 诊断，或 `.gitlab-ci.yml` 在 diff 中时，才审查 CI 配置/具体 job 证据；这属于显式 CI/platform finding，不改变 Code Review 的独立结论。
- 固件专项的 F01–F04 基线可检查受控 review-data 中已提供、绑定目标 SHA/profile 的构建与发布证据；不要求相关配置必须被修改。本范围不扩大下述取数权限，也不轮询 Pipeline；未提供的证据列待核验。

---

## CI 环境约束

当检测到 CI 环境时，以下约束生效：

**不可用的命令行工具**：`jq`、`python`、`python3`、`pip`（CI 镜像中未安装）。
**可用的命令行工具**：`node`（带 fs/path 等内置模块）、`curl`、`grep`、`awk`、`sed`。
**Git 限制**：Runner 不保证存在业务仓 checkout。MR scope 和 diff 内容由 `$REVIEW_DATA_DIR` 提供，不得用工作目录中的 Read/Glob 或 `git log main..HEAD` / `git diff main..HEAD` 证明业务源码存在或缺失。

禁止编写或执行 Python/jq 脚本来处理数据。如需解析 JSON，使用 `node -e` 或 Read 工具直接读取。

**可用脚本**：

- `<skill-path>/scripts/render_review_result.js`：只校验并渲染 `${CODE_REVIEW_WORKSPACE}/review-result.json`，不联网；CI 发布器在 Agent 退出后调用。
- `<skill-path>/scripts/create_mr_note.sh`：MR 评论 create-only 回写；不同 review 新增评论，同一 `review_run_id` 串行重试只读回、不更新。**CI 中仅允许 ci-templates 的确定性发布器调用；Agent 不得调用。**
- `<skill-path>/scripts/validate_behavior_coverage.cjs`：只校验 stdin 中的覆盖执行记录与固定 SHA，不取 Git 数据、不联网、不执行记录中的内容；不受下方两个 Git helper 的用途限制。
- `.gitlab-ci/scripts/code-review/materialize-review-diff.sh "$REVIEW_DATA_DIR" <file-id>`：只按完整清单中的数字 ID，从固定的 base/head SHA 物化一个 diff。
- `.gitlab-ci/scripts/code-review/verify-source-path.sh "$REVIEW_DATA_DIR" <relative-path>`：只对固定的 head SHA 返回 `EXISTS`、`ABSENT` 或 `UNKNOWN`。

只允许执行上述两个受控 helper 来访问 review Git 数据；禁止自行编写脚本、发起网络请求或改变 revision。两个 helper 都绑定预处理阶段固定的 base/head SHA。`UNKNOWN` 不是 `ABSENT`，不得用于缺失型红线。同一 `review_run_id` 必须由单一 CI job 写入；评论脚本不承担并发 writer 的互斥。

生产调用前必须按环境供应链门禁核验脚本内固定的 `/usr/bin/curl` 和平台 Node runtime，并把批准值传为 `CODE_REVIEW_CURL_SHA256`、`CODE_REVIEW_NODE_SHA256`；Linux 按 `/usr/bin/node`、`/usr/local/bin/node` 顺序选择固定路径，root-owned system runtime 的值来自受保护配置，批准的用户态 macOS Node 还必须匹配脚本内置 allowlist。脚本会复核 canonical path、owner/mode/hash。生产模式不接受工具或 CA 路径 override；只有使用非生产 `test-token` 的显式单元测试模式可以注入 mock executable。Node/curl 从空环境启动，禁用 curlrc、代理、继承 CA、Node options 与动态 loader 注入，禁止通过 PATH 或用户配置改变固定 GitLab 网络边界。

**C1 项目身份解析**：CI 不执行随包的 Python resolver。直接 Read `$REVIEW_DATA_DIR/meta.json`，并按 [`code-chinese-literal-review.md`](code-chinese-literal-review.md) 的 canonical project 决策表归一化；若 meta 应存在但缺失/无效，项目身份必须为 `unknown`，不得回退成 scanner 自身的 `$CI_PROJECT_PATH`。

## CI 场景输出约束

**L3 范围与证据**：按 `l3-release-gate.md` 选择最小充分验证，接受 MR 描述、附件摘要、
仓库报告与 CI 链接。报告复用结合 diff 和作者说明评估；不要求可信采集器重复出具证据。
本地无法执行时的具体原因、已有验证和未验证部分可据此完成评审，记
`L3_EXECUTION_EXPLAINED`，不冒称执行成功。已知相关失败和实际代码缺陷独立处理。

**L3 分支事实**：优先使用匹配 MR/head 的 `meta.mr_context`，不要求 reviewer 自行联网。
快照 unavailable/unknown 时注明来源限制，可结合 MR 描述和仓库配置评估，不仅因此阻断。
提交说明不是在线核验或生产操作授权；实际冲突应如实指出。测试材料问题不得改写成
`incomplete_reasons` 或行为覆盖 incomplete；真实源码未读取/审查执行失败另行处理。

CI 环境下，评论回写和输出必须遵守以下约束（本地场景不适用）：

1. Agent 必须写入 `${CODE_REVIEW_WORKSPACE}/review-result.json`，并符合 SKILL.md 的 `schema_version: "1.1"` 结构化结果契约（含非门禁 `tdd_assessment`）；不得写最终评论 Markdown。渲染器只为滚动升级兼容旧 `1.0` 输入；`tdd_assessment` 缺失或无效时 fail-open 为 `UNVERIFIED`，不得阻断核心 Review 发布。
2. Agent 禁止调用 `create_mr_note.sh`、curl 或任何 GitLab 写接口。CI 发布器是单一 writer：JSON 校验 → 固定五段 Markdown 渲染 → create-only 发布 → marker/作者/head 回读。
3. 渲染器固定生成 SKILL.md 的 5 个顶级章节；Agent 内容中的标题会被降级，不能破坏机器解析协议。
   评论末尾的 `code-review-tdd-assessment:v1` marker 是白名单化、限长后的非门禁 TDD 信号，可供后续聚合器读取。
   模型文本中的同名前缀必须转义；聚合器只接受评论中恰好一个 canonical marker，否则按 `UNVERIFIED` 处理。
4. 不要生成或依赖 `reports/code-review-report.md`、`reports/code-review-findings.json` 等 artifact 文件。结构化结果仅用于本次 CI 的确定性发布，不含完整 diff 或密钥。
5. 若无法读取 MR diff/commits，或无法写出有效结构化结果，CI 必须以 `deterministic_publish_failed` 失败；scope 为 partial/unknown 时仍可审查已观察到的正向问题，但缺失型断言不得作为确定性红线。

按 [行为覆盖规则](behavior-coverage-review.md) 选择深度；只有详细模式强制 JSON 和检查器，
简要模式不得仅因无 JSON 判未完成。详细记录只作本次核验临时输入，不含完整 diff 或敏感数据，
不提交、不上传，正文只摘要结果。实际代码入口核验的取证或对账未完成时（不含上述测试材料问题），只汇报已确认问题和审查缺口，不产生
通过结论或最终通过评论。校验器退出 0 不代表代码通过；本仓 CI 测试该校验器，也不等于
所有下游 Review job 已强制集成运行时覆盖门禁。

## CI 性能约束

CI 场景下 Step 0 先读取 `meta.json` + `file-list.md`；只对优先文件按需 Read diff 或调用受控 materialize helper。不得为了证明“没有”而遍历不完整窗口。

---

## CI 进度标记协议

在执行每个 Step 前后输出进度标记到标准输出。标记不属于最终评论内容，仅供 CI watchdog 追踪执行进度。

**格式**：`[CR-PROGRESS] step=N/5 status=<start|complete> [附加键值对]`

**必须在以下时点输出标记**：
1. Step 0 开始：`[CR-PROGRESS] step=0/5 status=start`
2. Step 0 完成：`[CR-PROGRESS] step=0/5 status=complete files=<文件数>`
3. Step 1 开始：`[CR-PROGRESS] step=1/5 status=start`
4. Step 1 完成：`[CR-PROGRESS] step=1/5 status=complete`
5. Step 2 / Step 3 同理（各 start / complete）
6. Step 4 开始/完成：`[CR-PROGRESS] step=4/5 status=<start|complete>`。finding 复现验证仍按 SKILL.md 的 CI 降级规则；行为覆盖按唯一入口选择简要自查或详细复核，均不能因零 finding 或跳过 P1/P2 而省略适用核验。进度标记不是完成证据。
7. Step 5（总结与评分）开始/完成同理

这些标记使 CI 心跳日志显示 Agent 当前进度（如 `skill_step=2/5`），让用户实时了解审查进展。

### 结果协议与门禁状态

- 中央提交工具使用 schema `1.1`，传递 `tdd_assessment` 与可选 `incomplete_reasons`；渲染器兼容旧 `1.0`。
- 测试材料和本地 L3 限制说明按 `l3-release-gate.md` 判断；平台未取证、附件不可访问或发布关系未知本身不进入 `incomplete_reasons`，不导致审查未完成。源码/审查执行本身的缺口与这些材料问题分开。
- 实际审查未执行完成而输出 `审查未完成` 时，必须为 `score=N/A`、`should_pass=false`。取证缺口写入 `incomplete_reasons`，已确认发现保留在 `red_lines`；不能为缺口自动添加 P0。
- CI 将未完成单独记录为 `execution_status=review_incomplete`、`review_verdict=unknown`、`gate_reason=review_incomplete`，退出 94，继续阻断合并。运行或发布失败仍优先记录为平台失败。
- `red_lines` 和 `suggestions` 保留原始严重级别；渲染器不推断或叠加 `[P0]` / `[P2]`。TDD 信号缺失或无效仍降级为 `UNVERIFIED`，不改变门禁。
