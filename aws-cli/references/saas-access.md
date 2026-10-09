# aws：认证与官方能力路由

平台 owner：`aws-cli`。业务与门禁正本：[SKILL.md](../SKILL.md)；[认证 profile](auth-profile.json)；[认证发现事实](auth-discovery.json)。provider 保留 `delegated`，这表示委派现有契约，不表示统一 runtime provider 已接入。

## 目标与已有凭据

Official aws CLI selected existing profile; ~/.aws config/credentials consumed by CLI only, no secret/profile content enumeration.

环境/域名/profile 以 owner SKILL 为准；只使用本次目标，不扫描其它 profile 或失败后自动换环境。凭据通过批准机制私密注入；不在命令参数、日志、聊天、仓库或回执中输出。

## 缺凭据与登录恢复

Only selected target profile: renew existing IAM Identity Center session with aws sso login --profile when SSO configured; otherwise administrator injects approved STS/access credentials via official setup, no default/legacy CN fallback.

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)。网页 Session、平台 Token、CLI credential 是不同能力；不因为某个入口已登录就宣称另外两种可用。创建/撤销凭据必须另有明确授权。

## 身份、权限与生命周期

aws sts get-caller-identity --profile <target>, Account equals requested account, then service-specific authorized read. STS identity success does not establish resource permission.

SSO cache/STS temporary credential expiry; aws sso logout affects cached sessions and must be scoped/authorized. Long-lived IAM key deactivation/delete by owning admin, not local config deletion.

只读探针失败按该实例的身份/权限/网络分别定位；HTTP 200、配置存在和本地模拟都不是完整资源权限证明。这里只描述探针，本轮没有执行。

## 部署版本与官方入口

aws --version; hosted services use per-service API models, selected region/partition; no shared server semver. AWS CN historical only; current CN Tencent.

[官方认证/CLI reference](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)。完整业务 API 按部署版本查询官方资料和 owner SKILL，保留 AddX 环境规则/私有扩展，不复制公开 API 全集。首次接入或具体版本、认证、接口漂移交给 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)；日常复用先读本 owner reference。

## 证据边界

源码和官方文档只证明候选方法；安装、版本及配置存在不证明目标身份。真实验收分别保存本机／远端的所选 profile、服务器身份、授权资源读取与清理结果。任务日期、具体身份、测试数量及报告路径写入任务报告，不写入通用方法。
