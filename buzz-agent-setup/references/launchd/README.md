# GitLab → Buzz 同步调度（owner macOS `launchd`）

本页是 [systemd runbook](../systemd/README.md) 的 macOS 等价 wiring。同步仍是无 LLM 的 Desk-owned Agent Step；config、manifest、0600 env、0700 state、固定 release、单 writer、dry-run 与 L4 门禁完全相同。只替换调度器，不改变 sync／route／outbox／binding 协议。

## 1. 前置边界

- 先按 systemd runbook 的第 1、2 节准备 immutable 40 位 commit release、配置、manifest 与共享白名单 launcher。
- launcher 固定使用 `/usr/bin/python3`；plist 只含 env 文件路径，不含 token、私钥或 auth tag。
- `~/Library/LaunchAgents` 中的 plist 必须是 owner 所有、mode 0600；`~/.config/buzz/sync/logs` 为 0700，stdout／stderr 文件预创建为 0600。
- 同一 Channel 只能存在一个调度者。安装前确认没有 systemd timer、Desk heartbeat、公开 schedule Workflow、旧 launchd label 或手工常驻 loop。
- 多仓 Agent 的 plist/launcher 只能把 owner 固定的 `BUZZ_GITLAB_PROJECT_TOKEN_MAP` 路径加入白名单；不要从 Issue、Buzz 消息或模型参数拼接 map 路径。

## 2. plist 模板

将 `<home>`、`<channel>`、`<desk>` 与 `<immutable-release>` 替换为规范化绝对路径／小写短名。保存为 `~/Library/LaunchAgents/ai.addx.gitlab-buzz-sync.<channel>.plist`。

<!-- template:sync-launchd-plist -->
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.addx.gitlab-buzz-sync.&lt;channel&gt;</string>
  <key>ProgramArguments</key>
  <array>
    <string>&lt;home&gt;/.config/buzz/sync/gitlab-buzz-sync-launch.sh</string>
    <string>/usr/bin/python3</string>
    <string>&lt;immutable-release&gt;/scripts/gitlab_buzz_sync_timer.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>BUZZ_SYNC_ENV_FILE</key>
    <string>&lt;home&gt;/.config/buzz/agents/&lt;desk&gt;.env</string>
  </dict>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>&lt;home&gt;/.config/buzz/sync/logs/gitlab-buzz-sync-&lt;channel&gt;.out.log</string>
  <key>StandardErrorPath</key>
  <string>&lt;home&gt;/.config/buzz/sync/logs/gitlab-buzz-sync-&lt;channel&gt;.err.log</string>
</dict>
</plist>
```

`launchd` 不会并发运行同一 label 的第二个实例；入口的 per-project lock 仍是绕过调度器并行执行时的最后防线。停机期间的变化由下一轮 cursor 补齐，不逐轮重放。

## 3. 阶段 0：真实 launchd 探针后立即卸载

```bash
set -euo pipefail
mkdir -p "$HOME/.config/buzz/sync/logs" "$HOME/Library/LaunchAgents"
chmod 700 "$HOME/.config/buzz/sync/logs"
touch "$HOME/.config/buzz/sync/logs/gitlab-buzz-sync-<channel>.out.log" \
  "$HOME/.config/buzz/sync/logs/gitlab-buzz-sync-<channel>.err.log"
chmod 600 "$HOME/.config/buzz/sync/logs/gitlab-buzz-sync-<channel>."{out,err}".log" \
  "$HOME/Library/LaunchAgents/ai.addx.gitlab-buzz-sync.<channel>.plist"
plutil -lint "$HOME/Library/LaunchAgents/ai.addx.gitlab-buzz-sync.<channel>.plist"

# 从生产 plist 派生无 StartInterval 的一次性探针，避免超时任务结束后再次被周期调度。
DOMAIN="gui/$(id -u)"
LABEL="ai.addx.gitlab-buzz-sync.<channel>"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROBE_PLIST="$(mktemp "$HOME/Library/LaunchAgents/$LABEL.probe.plist.XXXXXX")"
PROBE_SUFFIX="${PROBE_PLIST##*.}"
PROBE_LABEL="$LABEL.probe.$PROBE_SUFFIX"
PROBE_LOADED=0
cleanup_probe() {
  if [ "$PROBE_LOADED" -eq 0 ]; then
    rm -f "$PROBE_PLIST"
    return 0
  fi
  if ! CLEANUP_SNAPSHOT="$(launchctl print "$DOMAIN/$PROBE_LABEL" 2>/dev/null)"; then
    echo "launchd probe state unreadable; retained $DOMAIN/$PROBE_LABEL and recovery plist: $PROBE_PLIST" >&2
    return 1
  fi
  if ! printf '%s\n' "$CLEANUP_SNAPSHOT" | grep -Eq 'last exit code = -?[0-9]+([[:space:]]|$)'; then
    echo "launchd probe not terminal; left loaded one-shot $DOMAIN/$PROBE_LABEL for safe completion; recovery plist: $PROBE_PLIST" >&2
    return 0
  fi
  launchctl bootout "$DOMAIN/$PROBE_LABEL" || {
    echo "launchd probe bootout failed; retained $DOMAIN/$PROBE_LABEL and recovery plist: $PROBE_PLIST" >&2
    return 1
  }
  PROBE_LOADED=0
  rm -f "$PROBE_PLIST"
}
trap cleanup_probe EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cp "$PLIST" "$PROBE_PLIST"
/usr/libexec/PlistBuddy -c 'Delete :StartInterval' "$PROBE_PLIST"
/usr/libexec/PlistBuddy -c "Set :Label $PROBE_LABEL" "$PROBE_PLIST"
chmod 600 "$PROBE_PLIST"
PROBE_LOADED=1
if ! launchctl bootstrap "$DOMAIN" "$PROBE_PLIST"; then
  PROBE_LOADED=0
  exit 1
fi
launchctl kickstart "$DOMAIN/$PROBE_LABEL"

# kickstart 只触发、不等待。waiting/running 可能显示 `(never exited)`；仅数字 exit code 是 terminal。
attempt=0
while :; do
  SNAPSHOT="$(launchctl print "$DOMAIN/$PROBE_LABEL")"
  printf '%s\n' "$SNAPSHOT" | grep -Eq 'last exit code = -?[0-9]+([[:space:]]|$)' && break
  attempt=$((attempt + 1))
  [ "$attempt" -lt 240 ] || { echo "launchd probe timed out" >&2; exit 1; }
  sleep 1
done
printf '%s\n' "$SNAPSHOT"
printf '%s\n' "$SNAPSHOT" | grep -Eq 'last exit code = 0([[:space:]]|$)'
tail -n 50 "$HOME/.config/buzz/sync/logs/gitlab-buzz-sync-<channel>.out.log" \
  "$HOME/.config/buzz/sync/logs/gitlab-buzz-sync-<channel>.err.log"
if ! cleanup_probe; then
  trap - EXIT HUP INT TERM
  exit 1
fi
trap - EXIT HUP INT TERM
! launchctl print "$DOMAIN/$PROBE_LABEL" 2>/dev/null
```

必须在 300 秒内完成探针并 `bootout`。探针使用随机后缀的独立 label，不得触碰同名生产／残留 job。只有明确读到 terminal `last exit code` 才可 bootout；`running`、`waiting` 或未知状态均由 trap 保留无 `StartInterval` 的一次性 job，让当前 writer 安全完成且不会再次调度。超时后按错误输出中的 probe label 轮询到 terminal，再 bootout 该 probe label 并删除对应 recovery plist。`last exit code` 非 0 时任务已停止，由 trap 卸载后失败退出。探针需证明 exit 0、空轮零 Channel 消息、失败时非零且业务 Channel 静默，并保存 plist／launcher／release／manifest digest。探针后 probe label 不存在才进入评审；超时恢复完成前不得启用周期 job。

## 4. L4 receipt v3 通过后启用

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/ai.addx.gitlab-buzz-sync.<channel>.plist"
launchctl print "gui/$(id -u)/ai.addx.gitlab-buzz-sync.<channel>"
```

启用后观察至少两个周期：一次空轮、一次受控变更；核对唯一进程、唯一 root／reply、binding readback、cursor 推进和日志脱敏。`status=locked` 只允许来自显式并行负测，常规周期出现即按第二 writer 调查。

## 5. 停用与回滚

```bash
# 先阻止新一轮；当前进程若仍在写，等待它自行结束，不使用 kill -9。
launchctl bootout "gui/$(id -u)/ai.addx.gitlab-buzz-sync.<channel>"
launchctl print "gui/$(id -u)/ai.addx.gitlab-buzz-sync.<channel>"  # 预期找不到 label

# 修改 plist 与 manifest 指向上一个已验证 immutable release 后重新校验、探针、评审。
plutil -lint "$HOME/Library/LaunchAgents/ai.addx.gitlab-buzz-sync.<channel>.plist"
```

cursor、outbox 与 binding 保持原样；config、env、state、Desk membership 和 GitLab token 都不删除。回滚也必须重新跑阶段 0 与 L4，不直接 bootstrap 周期任务。

## 6. 必须保存的 macOS 证据

除 systemd runbook 的通用证据外，L4 receipt v3 还记录：macOS 版本、`launchctl print` 脱敏输出、plist SHA-256、launcher SHA-256、固定 release commit、manifest digest、stdout／stderr 文件 mode、两个周期时间、探针后的 bootout 结果，以及不存在其它 scheduler 的单 writer 证据。进程环境只记录变量名；不得记录 token、私钥、auth tag 或业务标题。
