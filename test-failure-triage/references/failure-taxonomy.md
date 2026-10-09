# 根因与阶段判定

这是分析输出的受控枚举。枚举帮助统计，不能代替内容分析；允许附加 secondary_causes。

## 运行与失败阶段

`run_kind=GENERATION`：实际执行入口/清单明确为 Devium AI implement、规划/生成任务。
`run_kind=EXECUTION`：实际入口明确为既有用例的 execution / server-opt 严格执行。
没有可靠运行契约时为 UNKNOWN；生成标签、计划名称、状态与 planType 只是线索。

`failure_stage=GENERATION`：规划、结构化输出、生成 Step 绑定校验、骨架/脚本产物阶段失败。
`failure_stage=EXECUTION`：已开始调用动作/Step，定位、效果或断言失败；可能发生在生成任务内。
`failure_stage=INFRASTRUCTURE`：调度、资源、bootstrap、认证/模型传输等阻断，还没执行目标操作。
无法定位最早失败阶段时 UNKNOWN。

## 根因枚举与证据

| root_cause | 判定依据 / 易混淆边界 | 优化归属 |
| --- | --- | --- |
| GENERATION_OUTPUT_INVALID | 生成器输出解析/schema/必填字段校验失败；不是 HTTP/模型服务故障 | Devium AI 输出契约/模型策略 |
| GENERATION_STRATEGY_ERROR | 规划阶段选错能力/路径或前置策略；进入执行后优先使用已证实的具体操作根因，生成来源写 secondary_causes | Devium AI 规划/能力检索 |
| FRAMEWORK_UNSUPPORTED | 实际版本缺所需执行模型/平台或操作；有能力目录/registry/源码证据 | Client/framework 适配 |
| STEP_BINDING_MISSING | 已有可兼容 Action/实现，所用通用 Step 未暴露或未注册；排除拼写/参数错误 | Bridge/绑定 |
| PLUGIN_ACTION_MISSING | 实际兼容目录缺动作，目标硬件/协议支持且独立预期明确 | 所属设备 Plugin |
| VERSION_BEHIND | 已发布兼容能力存在，消费版本较旧 | 空闲升级与回读验证 |
| WEBVIEW_CONTEXT_ERROR | 操作发生于错误 context，或切换失败；已有 WebView 能力不等于本次成功使用 | Step/生成策略/driver 会话 |
| WEBVIEW_DEBUGGING_UNAVAILABLE | build/调试接口或 driver attach 证据明确无法取得 WebView DOM | App build / 测试环境 |
| DRIVER_COMPATIBILITY | Appium/Chromedriver/WebView/系统版本明确不兼容 | driver / 环境 |
| APP_SEMANTICS_NOT_EXPOSED | 已确认正确页面/窗口、目标可见，实际树缺少目标语义；结合 WebView/Flutter/Canvas 和 backend 排除错误取树 | App accessibility/语义树；已有视觉策略可作为独立建议 |
| LOCATOR_ERROR | 已确认目标存在，给定 locator/属性/坐标与现场不符 | 测试数据/生成策略/locator |
| PRECONDITION_OR_OVERLAY | 没到预期页面、账号/数据前置错误或遮挡；不能当成 accessibility 缺失 | 场景前置/弹窗处理 |
| PRODUCT_DEFECT | 明确原 AC 与前置成立，产品行为不满足；有实际产品错误证据 | 业务 App/服务 |
| RESOURCE_OR_LEASE_FAILURE | 分配失败、离线、busy、owner/租约失效 | 平台资源/租约 |
| AUTH_PERMISSION_FAILURE | 明确认证失败/缺 scope；区分执行主体与分析取数主体 | 对应主体权限 |
| NETWORK_OR_SERVICE_FAILURE | HTTP/连接/模型服务故障等明确通信证据；timeout 本身不够 | 环境/服务 |
| EVIDENCE_INSUFFICIENT | 缺关键观测、版本/能力支持证据或结果互相矛盾 | 采集改进；不自动开发 |

主根因来自最早可证实原因，不来自最终包装异常。对多因链写 primary + secondary 和因果链；
根因未确认时写 provisional / unknown 并列出可区分的下一项证据。不要仅根据异常类或单个
关键字把此表当作 regex 分类器。

例如 implement 生成错误 locator 并调用 click：run_kind=GENERATION、failure_stage=EXECUTION，
若现场证实 locator 与目标不符，主因是 LOCATOR_ERROR；生成策略可以作为次因和优化归属。
同理，错误 context 的主因是 WEBVIEW_CONTEXT_ERROR，不因操作来自生成器而改名。

## 能力候选

候选是后续自动优化的输入，不是已经批准的开发任务。主根因为 FRAMEWORK_UNSUPPORTED、
STEP_BINDING_MISSING、PLUGIN_ACTION_MISSING，或有可复现公共能力缺陷证据时，检查：
平台/Plugin 归属、实际兼容版本、原需求/独立预期、最小复现、恢复方法与证据引用是否齐全。
齐全才标 true。App semantics、业务 defect、环境或权限问题先路由到对应层；缺字段时 false
并列 NEEDS_EVIDENCE/NEEDS_SPEC。实现与真机验证进入 Devium AI lifecycle 新计划/Job。
