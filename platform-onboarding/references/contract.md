# 每个 SaaS 的接入成果

来源与版本维护规则见 [source-policy.md](source-policy.md)。

## 正本与索引

平台 owner Skill 中保存 `references/saas-access.md`（使用入口）、`auth-profile.json`（可执行 Provider 或声明 delegated/pending）、既有认证/API文档、commands/coverage 与验证回执。通用 `tools.json` 只定位 owner、profile、策略和核验状态。跨多个业务 Skill 的同一平台共用一份 profile，不复制 Token 来源。

一个可执行 profile 至少包含 HTTPS origin、Provider、稳定 subject 探针、资源权限探针、租户/项目绑定、凭据消费方式、来源版本和证据。仅有 OAuth redirect 不足以配置 JWT Provider；仅有列表可读不足以证明 identity。未知内容保持 `pending`，不能猜接口后发真实请求。

自建平台或真实能力缺口的每个功能条目记录菜单/路由/角色、输入和输出、副作用、当前官方能力、所选策略与原因、鉴权、scope、命令、前置/结果探针、状态和证据。开源/公开 API 平台默认以对应部署版本的官方 reference 路由为准，不复制完整 API；实例定制和缺口才登记操作条目。全站覆盖也包括无法使用的功能、角色阻塞、缺失后端及旧模板。官方能力更新后重比覆盖，能替代的 adapter 退出维护。

源码版本或文件 digest 变化使相关 `source-verified` 证据待复核；本地 fixture 不能升格线上验收。运行回执包含候选版本、目标环境/部署版本、时间、身份与权限范围（无凭据）、测试场景、结果和未覆盖事项。上线写行为只有原业务授权与门禁满足后才可验收。

## OpenCLI 上游

官方项目：[jackwener/opencli](https://github.com/jackwener/opencli)。本仓库 npm lockfile提供复现实验环境；用户实际安装版本允许变化，发生差异时按公开 API 适配，不导入 OpenCLI 私有 `dist/src/browser/*` 模块。

安装包目录定位：在 `web-access/runtime` 运行 `node -p "require.resolve('@jackwener/opencli')"`，其 `dist/src/main.js` 对应包根的 `skills/`。按任务读取官方 adapter、sitemap、autofix Skill。生成 sitemap trace 和每个命令的可观测验证；不能以命令数量证明功能完成。

已核验厂商来源：Sentry [sentry-for-ai](https://github.com/getsentry/sentry-for-ai)（旧 sentry-agent-skills 已迁移），Stripe [stripe/ai](https://github.com/stripe/ai)，飞书本机 `@larksuite/cli` 随包 Skills。核验来源不代表它们覆盖 AddX 所有实例和任务；选择前比较实际范围。保留 AddX 特有 URL、授权和运维门禁。

## 与旧 Skill 的关系

`tracker-manager` 原目录仅为 DEPRECATED redirect，活跃 Skill 调用与 AGENTS 工具索引已迁移到 `tracking-lifecycle` 后删除该 stub；历史文档中的原名保留为历史记录。其他能力不按年龄删除，必须核对替代覆盖与调用者。`tool-skill-creator` 仍负责工具类 Skill 的一般编写，新 SaaS 接入委派此 Skill。`web-access` 是日常认证与人工网页验证统一入口，覆盖本机/远端、原生 Token/Session 和浏览器/屏幕交互；`feishu-auth` 仅提供效能业务 OAuth；普通飞书资源使用已安装 `@larksuite/cli` 及其官方 Skills，按宿主身份策略执行，不能切换至业务 OAuth；凭据管理、业务授权、应用 SDK 集成各自保留。

## 原生 REST 到 OpenCLI 的维护入口

已核验 owner profile 的读操作和声明 CRUD 写入契约的操作可通过 `platform-onboard opencli-install --tool <owner> --root <isolated-directory>` 生成适配器；默认目录为用户 OpenCLI 注册目录，先在隔离目录验收。生成器绑定平台 origin、输入 schema 和租约资源，权限/身份失败使访问状态退出 READY；写入要求明确 allow-writes、operation allowlist、输入 schema、前置探针和结果回读；部署/审批/发布等 workflow 沿用 owner 门禁，不通过通用生成器绕过。

Payload 源码可用 `scripts/scan-payload.py --source-root <repository> --output <owner-reference>` 扫描注册集合与 endpoint 候选。结果含源码 SHA/digest；集合输出 actionCandidates 与 permissionVerification=unknown，候选 CRUD 不成为可执行授权或已验证能力，endpoint 候选不证明已注册或可上线。调用者显式选择的 source-root 可为目录别名，启动时解析为 canonical 根并固定目录描述符；根下每一级路径在读取前都拒绝符号链接、仓库外路径与特殊文件，不能先读取再校验。通用扫描摘要包含 owner 的 source-capabilities.json，源码变化使相关能力待复核。

## 平台 owner 与认证 SSOT

每个真实 SaaS 必须有专用 owner Skill。业务流程 Skill 在 registry 的 businessConsumers 中声明消费关系，引用 owner 的认证正本；不复制 auth-profile/auth-discovery 或登录教程。registry 每个 tool ID 只出现一次，扫描器拒绝重复平台和同 tool 的多份认证 profile。公开平台可路由官方 Skill/CLI/MCP/API，AddX owner 只补实例与权限事实；未知地址或运行权限保持 pending。维护索引从 owner 生成，不成为另一份凭据/登录正本。
