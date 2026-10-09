# 浏览器与画面准备

用户在本机可见浏览器时，沿用 Codex Desktop 内置浏览器、agent-browser、Playwright 或 Chrome CDP，优先协议控制，避免抢占系统鼠标。只有浏览器必须跑在人看不见的远程开发机时才按用户授权和可用通道选择接管；用户明确选择远端画面接管且工具可用时使用 VNC/noVNC，不默认安装 VNC/noVNC。Chrome remote debugging 同样须核对用户选择与任务范围。

## 首次远程使用：准备 SSH Host alias

远程模式不能假定用户已经配置 Host alias。Agent 先问清目标主机、SSH 用户名、端口及希望使用的别名；已有上下文直接沿用。密钥仅使用用户指定的既有本机路径或已授权的 SSH agent，不读取或收集私钥内容、密码、口令。缺少主机、账号或权限时，说明缺失项和获取途径，等待用户补充，不把它误报为 SaaS 认证失败。

1. **检查已有配置。** 只对选定目标执行 `ssh -G <alias>`，在内存中核对 hostname、user、port 和必要的身份/代理设置，不输出整个配置。`ssh -G` 返回零不证明别名存在：未知名字也会解析为同名 hostname。确认解析结果对应用户指定目标；不能仅凭命令退出码跳过首次引导。
2. **由 Agent 准备配置。** 缺少 alias 时，给用户说明将新增的 Host、HostName、User、Port 和既有认证方式，确认目标无歧义后由 Agent 执行。首先在独占 0700 临时目录、0600 配置中生成并验证；不覆盖已有同名 Host，不更改其它条目或代理。用户要求配置远程访问即授权必要的 alias 配置；新增密钥、修改服务器 authorized_keys 或安装软件不包含在其中。
3. **保存时保留现有会话。** 在写入前检查 `~/.ssh/config` 的所有者、symlink 与 Include/通配规则，备份本次要修改的普通配置；原子写入必要的新增块并再次执行 `ssh -G`，核对最终生效值。已有文件不做无关 chmod。无法安全保留原配置时保留临时方案并说明原因，不能静默重写整个文件。
4. **本人只完成信任和解锁。** 已知主机以 `StrictHostKeyChecking=yes` 进行有界连接。首次未知 HostKey 需要用户依据管理员提供的可信指纹核对；不能以 ssh-keyscan 的结果自动认定可信，不能使用 StrictHostKeyChecking=no/accept-new 或清空 known_hosts。需要密钥口令、系统 Keychain 或密码时将本任务终端置前并明确提醒用户直接输入，Agent 不读取秘密输入；无置前能力则给出真实终端入口，不声称已弹出。
5. **真实验证后继续。** 不带 SaaS 凭据执行一次有界无业务 SSH 探针：`ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=10 <alias> 'printf SSH_ALIAS_READY'`。分别记录解析、SSH 认证及连接结果。连接成功后才检查本任务所需远端消费者和转发能力；SSH 成功不是 SaaS 身份验证成功。失败按网络、认证、HostKey、转发权限说明恢复动作，需本人操作时等待并复查，不让用户反复重做网页登录。
6. **记录保存与清理。** 用户要长期使用时保留已确认 alias 并记录用途；一次性验证删除独占临时配置和目录，不删既有 alias、密钥、known_hosts 或共享隧道。

配置结构示例（演示值，不可直接连接）：

```sshconfig
Host my-devbox
  HostName host.example.invalid
  User my-user
  Port 22
  # IdentityFile 仅填写用户指定的既有密钥路径；SSH agent 可不填。
```

这是 Agent 的对话引导方法，不是运行库已实现的交互式安装向导。运行库 `--ssh-target` 仍消费已经准备好的 alias；首次准备必须由调用 Agent 执行，不能把底层错误提示当作完成引导。

## 远端准备

先核对本任务或可借用的 SSH 隧道、远端端口、VNC display 与资源 owner。沿用已确认的映射；借用连接不代表可复用他人 profile、Cookie 或账号。

技术 setup 由 Agent 或用户电脑上的 AI 执行：

```bash
ROOT=$(mktemp -d)
chmod 700 "$ROOT"
python3 <skill_dir>/scripts/remote_web_session.py plan \
  --mode vnc --batch login-1 --port 5900 --root "$ROOT" \
  --chrome /usr/bin/google-chrome --ssh-user owner --ssh-host build-host \
  --site <site-label> --reason login --action 完成登录或验证 --retention once
```

脚本返回 PLANNED、profile、browser_argv、desktop_argv、argv、viewer_argv、viewer_ssh 和 handoff；它分配 profile，但不启动进程、不证明画面或登录就绪。按当前脚本实际输出启动本任务拥有的资源。VNC 的 Xvfb 禁用屏保；x11vnc 使用 `-listen 127.0.0.1 -localhost -nopw -forever -shared`。Chrome 保持 sandbox，不加 --no-sandbox。

root 为当前用户拥有的绝对路径、0700、非 symlink；profile 同样私有。复用已有桌面时只在该 display 启动本批次 Chrome，跳过计划里的 Xvfb/x11vnc，不重置其密码。CDP 仅监听 `--remote-debugging-address=127.0.0.1`。localhost 不能隔离其他本地用户，原始 CDP/VNC 不作为对外 API。

画面 helper 为本 Skill runtime/ 的 web-view；先 npm ci。用户电脑上的 AI 使用：

```text
web-view --ssh-target=<SSH Host alias> --port=<远端 viewer port>
```

它以私有 SSH Master 清除继承转发，并为本机选择空闲端口。SSH -L 的本机监听和远端目标均为127.0.0.1；实际本地端口可以不同。setup 输出与原始转发信息只给执行 setup 的 AI。普通用户只得到实际 noVNC 链接、站点、本人动作、完成回复与保留方式。

## 就绪、恢复与清理

通过 [实际渲染门禁](viewer-acceptance.md) 后才交给用户；连接成功和 canvas 存在不算画面就绪。人工操作期间暂停页面自动化，用户完成后调用者检查同一浏览器的目标身份/内容。

once 按批次临时分配、用完即删；save 要绑定站点、账号和期限。两者结束或失败均关闭本任务创建的浏览器、服务、短时租约和隧道；save 保留私有 profile，once 再调用 delete。借用桌面、隧道与其他浏览器保持原状。Mac/helper 和开发机分别记录清理结果。

不打印 cookie，不打印 token，不打印 DevTools websocket 或 DevToolsActivePort UUID。飞书开放平台、Canva 和普通网站都遵循同一接管流程；业务操作、审批和发布沿用调用者授权。


浏览器控制请求、接管探针及任务私有接收端都设置总期限，不让 socket 无限 accept/read。使用环回的一次性私有接收端时，先确认属于本任务的进程已启动且 READY，限制一次连接、输入大小和接收期限；失败或到期关闭并清理内存。RPC 超时不等于登录失败：分别确认页面登录态、消费者启动、接收完成和目标身份探针；没有接收证据不能报告转发成功。先诊断实际断点，避免重复触发正常 OAuth 或私有会话转交。

### 私有 IPC 与工具输出边界

网页到 loopback 的转交受浏览器 CSP、HTTPS/本地网络权限与网络请求期限影响；网页 token 存在不证明转交成功。只使用用户授权且工具允许的私有通道，不能为了转交改共享浏览器安全设置或文件根权限。

如果工具明确支持将结果直接写入本机 IPC 且不返回原值，先核对实际实现不会创建额外普通/临时凭据文件、输出不会回显值、扩展名处理和授权临时根；确认任务独占 reader READY 后才转交。FIFO 是流，不是凭据文件：必须核验类型、owner、权限、大小和期限。带 O_CREAT 的 exporter 还须防止 reader 超时后把缺失 FIFO 变成普通文件：保护父目录禁止新建，保持既有 FIFO 到写出器确认终态，超时不降级为普通文件，确认写出器结束后再清理。工具的“已保存”提示不是消费成功，最终以远端身份及真实只读回执判定。
