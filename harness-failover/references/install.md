# 安装与启动脚本要求

## 安装（本机）

```bash
bash skills/harness-failover/scripts/install.sh            # 复制到 ~/.local/lib/buzz-agents/harness-failover/ 并启用定时器
bash skills/harness-failover/scripts/install.sh --print-units   # 只看 unit 内容
```

为什么复制：插件缓存路径每次更新都变（`~/.claude/plugins/cache/<marketplace>/addx/<rev>/`），定时器不能指向它。
**更新 skill 后要重新运行 install.sh。** 它会覆盖同名的 `harness-failover.service/.timer`（包括手工装过的旧版本）。

定时器每 5 分钟跑 `switch --to auto --apply --probe --retry`：只读本机历史（每轮约 3 秒 CPU），**健康的一轮不探测**；当前 harness 没耗尽就什么都不做（并且距上次切换 < 20 分钟也不动）。
返回码 2（stuck）、3（有 agent 没加载新配置）、4（retry 有要人处理的项）、6（认不出当前 harness）都会让 unit 失败，
可用 `systemctl --user --failed` 看到；同时会通过飞书主动通知你（见 [notify.md](notify.md)）。`TimeoutStartSec=30min`（远大于 5 分钟的间隔，同一时间只会有一个实例）覆盖 22 次重启（每次最多 30 秒）加上探测与提醒。
同一时间只允许一个修改类实例（`<home>/.config/buzz/harness-failover/lock`），手工运行与定时器不会互相踩。

## 启动脚本必须认 `HARNESS_CLAUDE_WRAPPER`

claude 系 profile 是否生效取决于启动脚本怎么设 `CLAUDE_CODE_EXECUTABLE`。若脚本写死了某个 wrapper，切过去的 agent 会**悄悄继续走旧的**。
`switch` 在切 claude 系 profile 前会检查所有启动脚本（`run-agent.sh` 及不经它的 `run-*.sh`）是否含 `HARNESS_CLAUDE_WRAPPER`，不含则拒绝切换。

```bash
if [ "${BUZZ_ACP_AGENT_COMMAND:-}" = "$HOME/.local/lib/buzz-agents/node_modules/.bin/claude-agent-acp" ]; then
  CLAUDE_WRAPPER="${HARNESS_CLAUDE_WRAPPER:-$HOME/.local/bin/claude-glm}"   # 未设置时保持旧默认，回滚旧 env 不受影响
  [ -x "$CLAUDE_WRAPPER" ] || { echo "claude wrapper 不可执行: $CLAUDE_WRAPPER" >&2; exit 1; }
  export CLAUDE_CODE_EXECUTABLE="$CLAUDE_WRAPPER"
fi
```

`run-agent.sh` 用「白名单」放行 env 文件里声明的变量，所以写进 env 的 `HARNESS_CLAUDE_WRAPPER` / `CODEX_HOME` 会被保留。

## 回滚

- `harness-failover switch --to <上一个 profile> --apply --force`；
- 或恢复备份：`<agent>.env.bak.<ts>-failover`（0600）。
- 停用自动切换：`systemctl --user disable --now harness-failover.timer`。

## 启用飞书通知（安装后做一次）

```bash
harness-failover notify --setup   # 从 `lark-cli auth status` 读你的 open_id，写 ~/.config/buzz/harness-failover/notify.json（0600）；不发消息
harness-failover notify --status  # 确认 enabled
harness-failover notify --test    # 发一条测试消息给你（需要你确认后再跑）
```

定时器的 PATH 里没有 nvm 的 node，所以配置里记录 `lark-cli` 的**绝对路径**，运行时把它所在目录加进子进程 PATH。
`lark-cli` 升级、换 node 版本导致路径变化后，重新运行 `notify --setup`。
