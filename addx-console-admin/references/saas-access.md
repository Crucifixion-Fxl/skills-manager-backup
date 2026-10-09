# addx-console-admin：认证与源码入口

日常认证正本：[auth-discovery.json](auth-discovery.json)；执行配置：[auth-profile.json](auth-profile.json)；源码、候选版本与部署缺口：[source-discovery.json](source-discovery.json)。原业务契约：[SKILL.md](../SKILL.md)。

认证路由：Console native browser login → raw Authorization CONSOLE_TOKEN。

先复用 CONSOLE_TOKEN 并按双域 profile 请求 API；缺失时通过 web-access 复用或由用户完成 console 页面自身登录。userToken 是前端 storage key，不猜为 Casdoor/飞书。

Authorization 为 raw token（不添加 Bearer）；页面与 API 分属两域。

身份与权限：POST /api/user/info，code=0，data.userId；角色/菜单可见不等于目标 action 服务端权限。

失效与撤销：code 101/50008/50012/50014 为失效信号；恢复原页面登录；服务端 revoke/expiry 需由 LoginCacheService 与部署配置核实。

本轮仅文档/源码只读发现，未登录、创建 Token 或调用业务 API；研究 commit 不证明当前部署。已有 runtime profile/fixture 的验收边界保留，新的发现不自动使 delegated profile 可执行。

人工网页登录使用 [web-access](../../../agent-harness/web-access/SKILL.md)；首次接入或漂移使用 [platform-onboarding](../../../agent-harness/platform-onboarding/SKILL.md)，按 [source-policy](../../../agent-harness/platform-onboarding/references/source-policy.md) 分别记录 source/fixture/runtime。凭据只经宿主安全注入，不打印、不写聊天、仓库或命令行；本次扫描不读取私人凭据。业务写入继续服从原 owner 审批/发布/回读门禁。
