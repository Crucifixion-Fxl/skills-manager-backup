# Buzz agent 排错手册

按「症状 → 根因 → 修法」组织，均为实战验证过的问题。

## agent 在 Desktop 里 @ 不到（但频道成员列表里有它）

**根因**：Desktop 不查 REST，而是从 relay 事件流自建 agent 目录，对 agent 类身份有一道 mention 准入（`shouldHideAgentFromMentions`）——普通人类成员不过这道门，agent 必须过。三个条件缺一即被隐藏：

1. 频道成员表（kind:39002）里 role = `bot`
2. agent 的 kind:0 档案带有效 NIP-OA owner 背书（`BUZZ_AUTH_TAG`）
3. **owner 签发的 kind:30177 策略事件**存在且 `respond_to` 有值

第 3 条最易漏：Desktop 建的 managed agent 会自动发 30177，CLI 建的没人发。

**发布 30177 的方法**（owner 签名的 NIP-33 可替换事件）：

- `kind`: 30177
- `tags`: `[["d", "<agent_pubkey_hex>"]]`
- `content`: `{"name":"<agent名>","parallelism":1,"respond_to":"anyone"}`
- `respond_to` 取值：`owner-only`（默认）/ `allowlist` / `anyone`，须与 harness 的 `BUZZ_ACP_RESPOND_TO` 一致

发布需 NIP-42 认证：连 WS → 收 `AUTH <challenge>` → 回 kind:22242 事件（tags 含 `relay` 和 `challenge`）→ 再发 `["EVENT", <event>]`，收到 `["OK", <id>, true]` 即成功。

**relay 0.2.0 无此机制**：那个版本没有 agent 目录，任何带 auth tag 的 agent 在 Desktop 都会被隐藏，只能靠 `buzz-admin add-member` 注册成普通成员（sre-investigator 就是这么配的）。升级到 ≥0.2.1 后按上述三条件即可。

## agent 收到 @ 有 reaction，但永远不回复

同一症状有两个已验证根因，先看 agent 用的是哪个 adapter：

### 根因 A（Claude Code）：zed 旧 adapter 丢 turn 完成信号

用 zed 的 `@zed-industries/claude-code-acp` 时，agent 实际算完了答案（`~/.claude/projects/<cwd>/…jsonl` 转录里能看到完整回复），但 turn 完成信号传不回 harness，回复永不发布。日志先兆：

```
Error handling request { method: 'session/set_config_option' } { code: -32601, message: '"Method not found"' }
```

**修法**：换成官方 `@agentclientprotocol/claude-agent-acp`。换对后日志会显示 `steering_supported=true`（能力更全）。

### 根因 B（Codex）：会话沙箱与权限档不匹配，回帖命令不能联网

用 `@agentclientprotocol/codex-acp` 时，Buzz 启动日志显示 `bypassPermissions`，但实际 Codex 会话是 `workspace-write` + `network_access: false`——Buzz 的权限档没有传到内层会话。外层 agent 能收消息、能加 👀/💬 表情，转录里回复已生成，但它执行 `buzz messages send` 时 DNS 解析失败、连不上 relay。宿主机 DNS 与 relay `/health` 此时均正常（先核对这两样，别去查网络）。

**修法**（实测有效）：在该 agent 的 0600 env（`codex-acp` 子进程启动环境）加一行并重启：

```bash
INITIAL_AGENT_MODE=agent-full-access
```

重启后验证新会话实际是 `approval_policy: never` + `sandbox_policy: danger-full-access`——开放网络与本机文件，才与 `bypassPermissions` 的意图一致；只给本来就配全权限档的 agent 用。验收顺序：先 DNS / relay `/health` 只读检查，再验证回帖。

**两个坑**：

- 只改 `~/.codex/config.toml` 不可靠——adapter 每轮都会显式传入自己的沙箱策略，覆盖全局配置。
- 根因在 Buzz → ACP 的权限档传递；正式修复是 Buzz 在创建/恢复 ACP 会话后用 `session/set_mode`（`modeId: agent-full-access`）显式设置并校验结果。Buzz 修好之前，这个 env 是 Codex agent 的必要配置，不是可选项。

## agent 用了 owner 的身份发消息（冒名）

**根因**：`buzz` CLI wrapper 无条件 `source` owner 的 env 文件，agent 子进程调 CLI 时 owner 私钥覆盖了它自己的身份。

**修法**：wrapper 改为仅在 `BUZZ_PRIVATE_KEY` 未设置时才加载 owner env。**排查方法**：读频道消息的原始 `pubkey` 字段（不是显示名），确认是谁签的名。

## agent 启动即退出

```
Error: Claude Code cannot be launched inside another Claude Code session.
```

**根因**：从 Claude Code 会话里启动 agent，继承了 `CLAUDECODE` 环境变量，嵌套检测拒启。

**修法**：启动脚本里 `unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SSE_PORT`。

另一种启动失败是 Node 版本过低（adapter 需 ≥20，系统自带可能是 18）：`export PATH="$HOME/.nvm/versions/node/vXX/bin:$PATH"`。

## relay 拒绝：`relay_membership_required` / `restricted: relay-only kind`

- `relay_membership_required`（403）：agent 未注册且未带 `BUZZ_AUTH_TAG`。带上正确的 auth tag 即通过；带了但 tag 已被 shell 破坏则是下一节的症状。
- `restricted: relay-only kind`（400）：该 kind 当前 relay 版本不支持（如 0.2.0 的 NIP-MP `buzz projects`）。升级 relay。

## agent 启动后立即 `Auth failed: restricted: not a relay member`，每 5 秒重启一轮

**根因**：env 里的 `BUZZ_AUTH_TAG` 没加单引号。`mint-agent.py` 输出的 `auth_tag` 是裸 JSON（`["auth","<owner_pub>","","<sig>"]`）；直接拼进 0600 env，启动器或手工 `set -a; . env` 用 `source` 读时，shell 把里面的双引号吃掉，变成 `[auth,<hex>,,<sig>]`，owner 背书损坏，relay 认不出这个 agent 的归属，按非成员拒绝。日志里只有这一行拒绝，本地没有别的错误。

更隐蔽的是**用同一份坏 env 跑的 `buzz users set-profile` 也返回 `accepted:true`**：relay 接受了这份 profile，但里面的 NIP-OA 背书是坏的，之后 Desktop 里 @ 不到它（见第一节条件 2）。所以修 env 之后**必须重跑 set-profile**，不能只重启。

**修法**：

1. env 里改成整段单引号：`BUZZ_AUTH_TAG='["auth","<owner_pub>","","<sig>"]'`（`runtime-setup.md`「4. Env 与注册」的示例本来就是这样写的，铸身份时照抄过来）。
2. 自检：`( set -a; . <name>.env; set +a; echo "$BUZZ_AUTH_TAG" | cut -c1-8 )` 必须输出 `["auth",`；输出 `[auth,` 说明引号还是丢了。
3. 用这份 env 重新跑 `buzz users set-profile --name <name> --about "..."`，再重启进程，核对启动日志不再出现 `Auth failed`。

## workflow 模板变量渲染不出来

- **只展平顶层字段**：`{{trigger.x}}` 取 body 顶层的 `x`，嵌套对象渲染成整坨 JSON 字符串。外部系统原生 payload 必须先拍平。
- **保留字段名冲突**：`author`、`text`、`timestamp`、`channel_id`、`emoji`、`message_id`、`is_reply` 是标准 trigger 变量，自定义 payload 里用同名 key 会被遮蔽（实测：`author` 传了但渲染为空，改名 `by` 后正常）。
- 未知 `{{key}}` 不报错，原样保留为字面量——所以渲染出 `{{trigger.foo}}` 字样就说明字段没传对。

## 自己把自己杀了

`pkill -f 'buzz-acp'` 的模式会匹配到执行该命令的 shell 自身命令行，导致 shell 被杀（exit 143/144）。用 `pkill -x buzz-acp`（精确匹配进程名），或 `[b]uzz-acp` 括号技巧。

## GitLab 同步：MR 的更新在某个 Issue Thread 里看不到，只有一条「MR 关联」

**现象**：MR 关闭（或提到）了几个 Issue，其中一个 Issue 的 Thread 里只有一条 `🔗 **MR 关联 · 事实在别处**`，之后 MR 的新提交、评论、流水线、合并都没有出现在那里。

**根因**：不是丢消息，是规则（[ADR-0015](../../../docs/05-adr/0015-deliver-an-mr-to-one-thread-and-cross-link-the-others.md)，2026-09-21 起）：MR 的事实只发进 binding 指向的一个 Thread，其余关联的 Thread 只在 MR 首次出现时收一条交叉链接（header `change:xref`）。点交叉链接第二行的 `buzz://` 链接，就是 MR 事实所在的 Thread；MR 的 GitLab 页面里同步 bot 写的 binding 备注指向同一处。

**说明**：

- 落点顺序：`closes_issues` 的第一个 Issue → 分支名白名单 Issue → 第一条 origin → 分支族 → MR 自己的门牌。`related_merge_requests` 反查（Issue 文字里提到了 MR 号）只出链接，不决定落点。
- MR 已经有了所属的 Thread 之后才出现的 Issue，既没有事实也没有交叉链接。
- 2026-09-21 之前已按旧规则发到多个 Thread 的 MR：旧 Thread 里的消息不动，停在最后一次的状态（例如 MR 已合并那里仍写「已打开」），之后只在 binding 的那个 Thread 更新。
- 想让某个 Issue 的 Thread 承接 MR 的事实：在 MR 建出来**之前**写好关闭声明或分支名，或在描述里把它放在第一条 origin。已绑定的 MR 不会搬家。

## 常用排查命令

```bash
# 看频道消息的真实作者（不看显示名）
buzz messages get --channel <CH> | python3 -c "
import json,sys
for m in json.load(sys.stdin)[-5:]: print((m.get('pubkey') or '?')[:8], (m.get('content') or '')[:80])"

# 看 agent 档案（确认 display_name/about 已注册）
buzz users get --pubkey <hex>

# 看频道成员及角色（确认 bot 角色）
buzz channels members --channel <CH>

# 看 harness 日志关键行
grep -aE 'agent initialized|owner resolved|subscribed|agent_returned|ERROR' <agent>.log
```
