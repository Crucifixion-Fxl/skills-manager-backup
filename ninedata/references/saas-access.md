# ninedata：认证与官方能力路由

平台 owner：`ninedata`。业务与门禁正本：[SKILL.md](../SKILL.md)；[认证 profile](auth-profile.json)；[认证发现事实](auth-discovery.json)。provider 保留 `delegated`，这表示委派现有契约，不表示统一 runtime provider 已接入。

## 目标与已有凭据

NINEDATA_API_KEY + NINEDATA_SECRET_KEY override protected external config at XDG_CONFIG_HOME/addx/ninedata/config.json; --config or NINEDATA_SKILL_CONFIG supported.

环境/域名/profile 以 owner SKILL 为准；只使用本次目标，不扫描其它 profile 或失败后自动换环境。凭据通过批准机制私密注入；不在命令参数、日志、聊天、仓库或回执中输出。

## 缺凭据与登录恢复

选定 `https://ninedata.addx.live`，企业SSO组织域 `a4x`。优先使用已有批准AK/SK，核验owner/scope后私密注入；缺凭据时请用户指定已有授权来源，不扫描私人配置、不自动创建Key/管理Token。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)。网页 Session、平台 Token、CLI credential 是不同能力；不因为某个入口已登录就宣称另外两种可用。创建/撤销凭据必须另有明确授权。

## 身份、权限与生命周期

Existing scripts list authorized datasources, no SQL execution probe; verify AccessKey owner in console and datasource allowed scope separately.

access-key-id/timestamp/signature per openapi-auth.md: SHA256(path + '/' + secret + '&' + timestamp), not generic HMAC-SHA256. Revoke/rotate pair in authorized console key management; exact deployed UI must be confirmed.

只读探针失败按该实例的身份/权限/网络分别定位；HTTP 200、配置存在和本地模拟都不是完整资源权限证明。方法和源码不代表已经完成线上读取。

## 部署版本与官方入口

Managed /openapi/v1 contract plus target console/deployment build evidence; do not invent self-hosted version endpoint.

[官方认证/CLI reference](https://docs.ninedata.cloud/en/openapi/openapi_overview/)。完整业务 API 按部署版本查询官方资料和 owner SKILL，保留 AddX 环境规则/私有扩展，不复制公开 API 全集。首次接入或具体版本、认证、接口漂移交给 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)；日常复用先读本 owner reference。

## 证据边界

部署版本、当前subject、资源权限、凭据生命周期及清理结果应写入独立脱敏验收报告，不写入通用方法。官方资料仅证明公开契约，不证明选定私有部署兼容。原生OpenAPI需要AK/SK签名，现有client不提供Cookie模式。网页登录与官方认证并存；Session fallback须独立证明实际main/aux契约、CSRF与只读副作用，不能先宣称已实现或把网页样本当native结果。

双方直接原生请求均需实际成功；相同失败输出不算通过。只需元数据验收时不执行SQL，不读取数据库凭据。关闭页面、登出、任务profile删除与临时runtime/tunnel清理分别记录，已登出后需正常登录恢复或独立检查当前目标session，不沿用历史登录状态。
