# noVNC 画面验收与已知坑

2026-10-06 真实 Mac→SSH→noVNC→x11vnc 验收发现：页面提示“完成登录或验证”，但画面全黑。noVNC RFB 已连接、canvas 已创建，仍不代表有可见画面。直接原因是响应 CSP 只有 `default-src 'self'`，阻止 noVNC `Display.imageRect()` 生成的 `data: image/jpeg;base64,...`；浏览器报告 `img-src` violation。远端 Xvfb 默认 600 秒屏保也会造成无内容画面。

## 必须通过的门禁

- HTTP 页面、模块和 WebSocket 可达仅算连接检查。实际画面必须显示预期浏览器/登录页；检查截图与 canvas 的可见像素，同时没有图片解码/CSP 错误，才能给用户“画面就绪”的 prompt。
- 画面服务 CSP 限定脚本为 self，允许图片 `img-src 'self' data: blob:`；不因解码需要放开外部脚本、eval 或任意网络地址。HTTP/WS 仍只接受 loopback Host 与同源 Origin。
- 使用真实 noVNC `Display.imageRect()` 解码 JPEG 并断言 canvas RGB 的浏览器回归测试。不能以“存在 canvas”或“触发 connect 事件”的测试代替渲染验收。
- Xvfb 带 `-s 0` 关闭屏保；已运行的独占桌面可用 `DISPLAY=<本批次display> xset s off`、`xset s noblank`、`xset s reset` 唤醒。只操作本批次 display，不修改其他用户桌面。
- `LOCAL_PORT_IN_USE` 可能来自 SSH Host 的其他 LocalForward。使用私有 ControlMaster、ClearAllForwardings，再通过独立 mux 请求仅建立本次转发。用户电脑的端口占用可自动选择空闲端口，使用程序实际输出的 url；不能连续猜新端口让用户反复试。
- 服务端修复 CSP 后，用户刷新画面页面即可，不需要重新下载仅负责 SSH 的客户端包。保留正在操作的同一浏览器，不刷新掉 OAuth/验证码页；浏览器会话确已过期或需重建时说明原因，并沿用用户一次性/保存选择。

回归：`SAAS_VIEWER_BROWSER_FIXTURE=1 node --test skills/agent-harness/web-access/runtime/tests/viewer-browser-fixture.test.js`。依赖本仓库 tracking-lifecycle 的 Playwright 与本机 Chrome；它调用真实 noVNC Display，不模拟图片解码。旧 CSP 已复现 `JPEG blocked: ["img-src"]`，修复后断言实际绿色 JPEG 像素通过。

## 任务画面授权

loopback Host/Origin继续校验，但不能单独隔离其他本地用户。server为当前任务创建私有capfile（目录0700、文件0600），通过一次性bootstrap获取HttpOnly、SameSite=Strict cookie和256-bit的origin-scoped证明。Cookie不按localhost端口隔离，因此VNC WebSocket必须同时验证cookie和对应origin的sessionStorage证明；只有cookie不能读取或控制画面。未授权、错任务、重放、过期均拒绝，撤销/TTL关闭已建立的连接。

用户helper只私下读取所选任务capfile，使用本机私有launch.html打开浏览器；能力经fragment到达bootstrap后立即从history清除。正常stdout URL不含能力，argv只含私有文件路径，无query/token。SSH先成功建立私有mux forward再读capfile或执行readiness检查，失败不接入其他服务。关闭时清理本任务launcher、控制socket、listener及尚未消费的capfile，不删除其他任务文件。

Bootstrap一次性消费后，已授权tab可重连；新建helper需要owner重新签发/重启本任务viewer。此授权与平台Session的once/save选择独立，保存平台Session不延长viewer租约。相同UID或私有文件系统已失守不在上述隔离证明范围内。真实Chrome跨port Cookie泄漏仍不能建立RFB的测试、实际SSH接管及TTL/撤销/跨task拒绝均有回执；不把fixture升级为任意线上平台登录通过。
