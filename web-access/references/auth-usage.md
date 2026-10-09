# Companion 与远程使用

Node >=20.18.1。在 `skills/agent-harness/web-access/runtime` 运行 `npm ci --ignore-scripts`；命令为 `node bin/web-access.js <command> --key=value`，可用本地 `npm link` 提供 `web-access` 命令。私有包名 `@a4x/saas-access`；尚未发布，不能使用公共 `npx @a4x/...` 假装已经安装。独立安装 tarball 的认证 CLI 需要已安装 AddX plugin，显式设置 `SAAS_SKILLS_ROOT=<plugin的绝对skills目录>`；平台 profile 和适配器继续读取专用 Skill 正本，不复制进 npm 包。未找到目录时返回 `PROFILE_CATALOG_REQUIRED`。独立 `web-view` 画面程序不依赖这些平台文件。

## 埋点平台：用户电脑

用户用平台原有飞书登录，保留同一浏览器 Session 与原有 callback。只连接用户为本任务选定的独立 tab；不接管已有共享 CDP 会话。OpenCLI extension 模式需明确 profile 与 tab，仅支持读取；写入用显式 loopback CDP 的单次执行。启动/配置浏览器依照 OpenCLI 官方文档；不得公开 CDP 端口。

```bash
export OPENCLI_CDP_ENDPOINT=http://127.0.0.1:9222
export OPENCLI_CDP_TARGET=https://us-analytics-management.theunismart.com/spm/list/
# subject 是目标平台 userId，不是飞书 open_id；App 取当前任务的 applicationId。
node bin/web-access.js tunnel --tool=tracking --expected-subject=123 --application-id=456 --session=tracking-personal --ssh-target=devbox --remote-credentials=/home/me/.saas-access/tracking.json
```

Host alias `devbox` 在用户电脑的 SSH config 中解析，开发机无需固定 IP。首次没有 alias 时先执行[SSH alias 引导](browser-handoff.md#首次远程使用准备-ssh-host-alias)，不能直接启动凭据转交。认证反向隧道也使用独立私有 ControlMaster，清除继承转发后仅添加本次 -R；不修改用户其他连接。先验证 SSH 可达、已信任 HostKey、目标有 Node、允许 remote forwarding，且服务端 `GatewayPorts no`。客户端要求 `-R 127.0.0.1` 不能覆盖服务端强制公网绑定策略；远端 Node worker 检查实际监听只有 loopback（当前 Linux /proc 实现），其他 OS 在没有相应检查器时拒绝建立转发，不能仅根据客户端参数判定安全。任一前提不满足就报告恢复条件，不静默开放公网 listener。

Companion 与 tunnel 在前台存活，默认租约 30 分钟，`--ttl-seconds` 上限 8 小时。SSH 中断或租约到期会清理远端凭据文件，服务停止销毁租约；开发机不可转移该文件到其他机器。文件目录必须提前有安全父目录，最终目录自动创建为 0700，文件 0600。Windows 私有文件 custody 尚不支持；原生 CLI/keyring 认证仍可复用。

## 开发机：日常操作

```bash
export SAAS_AUTH_CREDENTIALS_FILE=/home/me/.saas-access/tracking.json
node bin/web-access.js ensure
node bin/web-access.js status
node bin/web-access.js call --operation=login.getCurrentUser
# context.list 等参数写入普通业务 JSON 文件，不含凭据。
node bin/web-access.js call --operation=context.list --input-file=context-query.json
node bin/web-access.js disconnect
```

`context-query.json`：`{"query":{"applicationId":456}}`。租约固定 App；不能用请求参数更换。默认只读，启用本地 `--allow-writes=true` 仅改变技术开关，不授予新增业务授权；工单/契约投影仍用 tracking-lifecycle 的 `post`/`create-workorder`。原始 CRUD 写入在代理模式需已有契约工作流或专用 scope 验证适配器，不接受仅靠请求自报 App 的写入；有权限 PAT 的本地 API 能力仍走原平台契约。某些未带 App 的平台管理操作无法证明资源范围，返回 `UNSCOPED_OPERATION`；只有确实授权整个账号时才显式选择 `--application-id=*`。

与原 tracking CLI 对接：设置 `TRACKING_PLATFORM_BASE_URL` 和 `TRACKING_BRIDGE_CREDENTIALS_FILE`，业务命令用 `--auth=bridge`；不用传 Cookie/Token。

## 纯 API 消费方

Superset 当前可执行 profile 只接受已注入 `SUPERSET_TOKEN` 或显式 `SUPERSET_ACCOUNT_TYPE=service` 的服务账号。个人 SSO 不进行密码登录。用 `serve --tool=superset --expected-subject=<Superset用户ID> --credentials-file=<绝对私有路径>` 或相同 tunnel，然后 `ensure` 与 `call --operation=me`。具体账号边界仍以 Superset Skill 为准。通用 operation registry 支持声明了类型/范围的 GET 分页、过滤和 ID 参数；租户绑定与返回归属均校验。复杂参数和写入仍由平台适配器处理。Marketing CMS 可使用 MARKETING_CMS_TOKEN 或显式 --transport=browser 的已核验 Cookie 契约；浏览器必须指定同一 session/profile/tab，身份仍由目标 /api/users/me 核验。维护者使用 bin/platform-onboard.js opencli-install --tool=marketing-cms 一次性安装受限查询入口；日常登录不安装、不扫描。Troubleshooting 的 TROUBLESHOOTING_TOKEN 使用 /api/current 的 result.data.userid 验证。

Device Provider 已有协议测试，当前无生产 SaaS Device profile。接入时由维护 Skill 把 discovery 获取的同 issuer HTTPS endpoint、client ID 环境变量名、验证 URL allowlist、grant 与目标 Token 接受证据写入平台 profile，再启用。遵守 `interval`/`slow_down`，拒绝、到期、撤销是终态，Token 留在内存；通用认证 `ensure` 仍须完成目标身份验证。

## 人工接管画面

`web-access` 提供 VNC 桌面/浏览器/保存策略；本运行库提供 `web-view` 的 noVNC 画面与本机 SSH -L 连接程序。服务端 `node bin/web-view.js --vnc-port=5900 --port=19827`，用户电脑 `web-view --ssh-target=<Host别名> --port=19827` 后打开画面 URL。只能监听 loopback，关闭服务/隧道后画面不再可达；不替代账号/权限探针。`SSH_AUTH_REQUIRED` 要先在用户电脑正常解锁 SSH 认证；程序不收集密码或关闭 HostKey 校验。画面连接使用独立私有 SSH ControlMaster：保留 Host 别名中的地址、身份与代理配置，清除继承的端口转发，然后仅建立本次画面转发，不复用或修改用户其他隧道。`--port` 是开发机的画面服务端口；本机优先使用相同端口，占用时自动选择空闲端口，打开程序输出的 `url` 即可。画面服务仅接受 127.0.0.1 Host 与对应同源 Origin，支持隧道两端端口不同。`LOCAL_PORT_IN_USE` 仍可能表示选端口到转发之间的竞争，可重新启动连接程序；`SSH_FORWARDING_DENIED` 由开发机 SSH 管理员恢复转发权限。初次 SSH alias 配置由调用 Agent 引导并执行；缺少主机权限或安装授权时再明确求助，日常给用户的 prompt 不包含 inspect、凭据或原始调试端口。

画面就绪必须通过 web-access 的 [实际渲染验收](../../web-access/references/viewer-acceptance.md)：连接与 canvas 不证明能显示 JPEG；CSP 的 img-src 必须允许 noVNC data: 图片，Xvfb 关闭屏保。

Provider 在返回结果前使用本次实际 Bearer 脱敏，包含普通诊断字段中的回显；Superset 服务密码回显也脱敏。Device 恢复 URL 的路径、查询和片段经百分号解码检查，不得包含私有 device_code。

## 画面授权与重连

画面服务启动后创建本任务私有capfile；Python任务计划的viewer_connect_argv提供所选capfile，默认端口模式使用同用户cache中的所选viewer文件。用户电脑helper经独立SSH mux读取这个任务文件，自动打开私有launcher；正常url不含能力，不要只复制url期待未授权浏览器已能访问画面。本机server用 --open 自动完成同样bootstrap。一次性bootstrap已消费后，新helper需owner重新签发本任务viewer；当前已授权tab可正常重连。cookie与origin-scoped证明一起保护VNC，TTL/关闭使现有画面通道失效。具体边界与真实回归见viewer-acceptance.md。

普通远端 CLI/API 消费使用 ssh -o BatchMode=yes -o ClearAllForwardings=yes <host>，避免 Host 配置的 LocalForward 在无关验收中自动建立或产生端口冲突。确需任务隧道时另行显式定义、核验监听和清理；不要让普通消费连接隐式继承隧道。
