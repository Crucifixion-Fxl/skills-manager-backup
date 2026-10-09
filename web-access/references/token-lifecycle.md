# 平台 Token 生命周期

只有 owner profile 的 tokenIssuers 已有源码/官方契约时，运行库才提供申请。登录、IdP Token 和目标平台 Token 是不同结果；没有 issuer 的平台继续使用官方工具/owner 原生流程或浏览器 Session，不猜 endpoint。

## 当前埋点实现

来源 backend d61e1692c3d28224e4d642f7bdf765a6a982ab0d，见 tracking-lifecycle/references/platform-auth.md。当前支持已登录本人的交互式 Cookie 会话申请：

| issuer | 期限 | 权限 |
|---|---|---|
| project | 30/90/180 天 | GET-only；helper 强制指定 appId，拒绝无应用绑定 |
| personal | 7/14/30 天 | 动态继承创建者当前角色；PAT 本身不绑定应用，无自造 scope |

PAT/Project Token 不能用于签发新 Token；创建必须来自交互式登录会话。该申请会产生平台凭据，必须在本次用户授权范围内。只读任务优先已有凭据，其次限定应用的 project；写业务时按 owner 使用 personal，业务审批/发布门禁保持有效。

需要不重试的 direct CDP 后端；公共 OpenCLI eval 后端可能重试导航，禁止承担 Token 申请/撤销。浏览器、tab 和目标 origin 必须属于当前任务。直接 CDP 由 helper 在本机/SSH loopback 内消费，普通用户使用 VNC 画面，无需 inspect。

## 使用

在运行库目录执行。输入文件不含凭据，例如：

```json
{"body":{"name":"本次任务只读","expiryDays":30,"appId":123}}
```

```bash
node bin/web-access.js issue-token --tool=tracking --issuer=project --expected-subject=34 --session=任务session --input-file=/绝对路径/request.json --secret-file=/私有目录/project.json --retention=once
```

父目录须由当前用户所有、权限700，目标文件必须不存在。文件先于上游请求预留，写入600的私有记录；公开结果仅为 ID、期限、资源范围和保存状态。明文 Token 不经过业务代理响应、聊天或 stdout。

申请后先用交互式会话回读本人 Token 列表，再用新 Token 请求 `/api/role/current` 确认真实 userId。Token Filter 构造的 `/api/user/getCurrentUser` 不填 userId，因此不能拿它作为 Token 身份证明。回读或身份验证失败保留 UNVERIFIED 私有记录并返回 TOKEN_SAVED_UNVERIFIED；业务消费拒绝此记录。

埋点原生调用无需把 Token 导出到命令行或聊天。下例 query.json 为 `{"query":{"applicationId":123}}`：

```bash
node ../../tracking-lifecycle/cli/bin/events-tdd.js api-call --operation=baseSchema.getAllBaseSchemas --request=/绝对路径/query.json --token-file=/私有目录/project.json --expected-subject=34 --application-id=123
```

以上相对命令从 skills/agent-harness/web-access/runtime 目录使用；可直接用安装 plugin 的绝对 CLI 路径。消费者固定 tool/origin/subject/appId、期限和已验证状态，在进程内注入 TMT_TOKEN；拒绝覆盖至另一个 origin 或 auth 类型。Project 原生消费复用埋点代理的实际 operation/参数/详情回读绑定，拒绝请求其他应用及不能证明资源范围的操作；不能只核对命令行 application-id。固定后端只是设置 project.appId 属性，未在全部路由强制执行，不能将服务端绑定当作已经覆盖全部接口。权限仍由平台与 owner 契约执行。

## 结束与失败

once 在本任务仍有交互式会话时先撤销并回读，然后清理平台 Token 文件，再关闭 profile/隧道；save 保留私有 Token/profile，关闭监听器、租约和隧道。平台最短 TTL 不等于一次性任务长度，once 必须显式撤销。

```bash
node bin/web-access.js revoke-token --tool=tracking --expected-subject=34 --session=任务session --secret-file=/私有目录/project.json
```

撤销先查本人列表。记录已不存在时只清理本地文件；仍存在才发送一次撤销，再回读。响应丢失、撤销未证实或会话失效时保留文件和未完成清理状态，不虚称已撤销。再次处理先查列表，不盲重放。申请响应丢失可能已创建 Token，但没有可靠 ID/明文；按本次名称和平台列表对账，不自动再申请。

首次保存失败返回 TOKEN_ISSUANCE_STORAGE_FAILED 和已知 recordId，不能声称 Token 已保管。按 recordId 在当前平台会话人工核对并撤销；没有私有文件时不能用 revoke-token 恢复。若验证阶段写盘失败，已保存的 UNVERIFIED 记录保持完整，禁止业务消费。

证据边界：实现和本地行为测试不等于平台部署或线上验收。首次平台 issuer 接入必须核对当前部署版本、权限与撤销机制；本轮不为证明流程创建线上 Token。
