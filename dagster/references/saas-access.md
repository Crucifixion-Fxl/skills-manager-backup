# dagster：认证与官方能力路由

平台 owner：`dagster`。业务与门禁正本：[SKILL.md](../SKILL.md)；[认证 profile](auth-profile.json)；[认证发现事实](auth-discovery.json)。provider 保留 `delegated`，这表示委派现有契约，不表示统一 runtime provider 已接入。

## 目标与已有凭据

DAGSTER_URL=https://dagster.addx.live + DAGSTER_TOKEN. Existing owner distinguishes Dagster-Cloud-Api-Token for Plus from Bearer for internal OSS gateway.

环境/域名/profile 以 owner SKILL 为准；只使用本次目标，不扫描其它 profile 或失败后自动换环境。凭据通过批准机制私密注入；不在命令参数、日志、聊天、仓库或回执中输出。

## 缺凭据与登录恢复

First determine OSS versus Dagster+ and upstream gateway from deployment owner; Plus user tokens are managed in UI. Do not send one credential to both schemes merely to see which works.

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)。网页 Session、平台 Token、CLI credential 是不同能力；不因为某个入口已登录就宣称另外两种可用。创建/撤销凭据必须另有明确授权。

## 身份、权限与生命周期

Read-only GraphQL query { version } on selected /graphql plus target repository/location metadata; subject/permissions must come from gateway or Plus membership, not schema version alone.

Plus user token creation/revocation in Manage tokens UI. OSS core does not establish the recorded Bearer issuance/revocation route; gateway owner must supply it.

只读探针失败按该实例的身份/权限/网络分别定位；HTTP 200、配置存在和本地模拟都不是完整资源权限证明。这里只描述探针，本轮没有执行。

## 部署版本与官方入口

GraphQL version and deployed image; match installed Dagster version. OSS proxy auth remains a separate contract.

[官方认证/CLI reference](https://main.archive.dagster-docs.io/deployment/dagster-plus/management/tokens/user-tokens)。完整业务 API 按部署版本查询官方资料和 owner SKILL，保留 AddX 环境规则/私有扩展，不复制公开 API 全集。首次接入或具体版本、认证、接口漂移交给 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)；日常复用先读本 owner reference。

## 证据边界

2026-10-07：source-verified = 已读取 owner 契约并研究官方入口；fixture 未运行（仅 reference 变更）；runtime pending = 未登录、未创建线上 Token、未做线上业务调用。当前部署版本、实际 subject、目标资源权限和撤销效果尚未验收。
