# crowdin：认证与官方能力路由

平台 owner：`crowdin`。业务与门禁正本：[SKILL.md](../SKILL.md)；[认证 profile](auth-profile.json)；[认证发现事实](auth-discovery.json)。provider 保留 `delegated`，这表示委派现有契约，不表示统一 runtime provider 已接入。

## 目标与已有凭据

CROWDIN_TOKEN Bearer for entry flow; approved project pull wrapper consumes project crowdin/.env without modifying it.

环境/域名/profile 以 owner SKILL 为准；只使用本次目标，不扫描其它 profile 或失败后自动换环境。凭据通过批准机制私密注入；不在命令参数、日志、聊天、仓库或回执中输出。

## 缺凭据与登录恢复

Account Settings API Personal Access Tokens; user creates token for project, project.source.string, project.translation as required. No automatic key provisioning.

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)。网页 Session、平台 Token、CLI credential 是不同能力；不因为某个入口已登录就宣称另外两种可用。创建/撤销凭据必须另有明确授权。

## 身份、权限与生命周期

GET /api/v2/user plus /api/v2/projects/{projectId}; verify user and selected project/target languages, token scope and membership.

User creates/revokes PAT in Account Settings; expiry from token settings. Pull flow stays through pull.sh and never rewrites project .env.

只读探针失败按该实例的身份/权限/网络分别定位；HTTP 200、配置存在和本地模拟都不是完整资源权限证明。这里只描述探针，本轮没有执行。

## 部署版本与官方入口

Hosted API v2; string-based project reference is separate from file-based. Fetch applicable official OpenAPI before business operation.

[官方认证/CLI reference](https://support.crowdin.com/developer/api/v2/string-based/)。完整业务 API 按部署版本查询官方资料和 owner SKILL，保留 AddX 环境规则/私有扩展，不复制公开 API 全集。首次接入或具体版本、认证、接口漂移交给 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)；日常复用先读本 owner reference。

## 证据边界

2026-10-07：source-verified = 已读取 owner 契约并研究官方入口；fixture 未运行（仅 reference 变更）；runtime pending = 未登录、未创建线上 Token、未做线上业务调用。当前部署版本、实际 subject、目标资源权限和撤销效果尚未验收。
