# GitLab → Buzz 同步 timer 部署（owner `systemd --user`）

本页是生产 wiring 契约。按 [ADR-0008](../../../../docs/05-adr/0008-run-gitlab-sync-from-owner-systemd-timer.md)，每个业务 Channel 一对 `gitlab-buzz-sync-<channel>.service`／`.timer`，由 Desk 所在主机、同一 Unix UID 的 `systemd --user` 运行，同步路径里没有 LLM。确定性步骤仍是 ADR-0004 的 **Desk-owned Agent Step**：以 Desk identity 发布，sync/route/outbox/binding 契约不变。Desk Agent 的 harness 是普通常驻 service，与本页无关；不部署常驻 sync daemon、listener 或 loopback。

## 边界

- `<immutable-release>` 是已评审的 **40 位 commit SHA** 固定副本（owner 所有、组／其他不可写），禁止 `current`、`latest`、branch checkout 或自动更新的 plugin cache。它必须包含 `scripts/gitlab_buzz_sync_timer.py`、`gitlab_buzz_desk_runner.py`、`gitlab_buzz_summary_publish.py`、`gitlab_buzz_sync.py`、`gitlab_buzz_route_reply.py` 及其依赖；manifest 的 `release_dir` 必须指向同一目录。
- Desk 身份的 owner 固定 0600 env（例如 `~/.config/buzz/agents/<desk>.env`）保存 `BUZZ_RELAY_URL`、Desk 私钥、`BUZZ_AUTH_TAG`、该 Desk 的 GitLab token 与 `BUZZ_DESK_RUNNER_MANIFEST`；值不进入 Git、Canvas、argv、unit 文件或日志。timer 与 Desk Agent 读同一份 env，同一身份、同一 token。
- launcher 只把白名单环境变量交给入口：`HOME`、`USER`、`LOGNAME`、`PATH`、`LANG`、`BUZZ_RELAY_URL`、`BUZZ_PRIVATE_KEY`、`BUZZ_AUTH_TAG`、`BUZZ_DESK_RUNNER_MANIFEST`、（多仓 Agent 才启用的）owner 固定 `BUZZ_GITLAB_PROJECT_TOKEN_MAP` 与配置的 GitLab token 变量。Desk env 里的 `BUZZ_ACP_*`、模型 provider 凭据等一律不传；map 路径不能从消息、Issue、Canvas 或模型参数构造。
- launcher 的 canonical path 校验在 macOS 只放行系统固定的 `/var/...`→`/private/var/...` 等价映射；`..`、其它 realpath 差异及用户可控 symlink 仍 fail closed。
- 同步与路由 config 为 Desk UID 所有、mode `0600`；repo／Channel state 为同 UID、mode `0700`，保存 lock、cursor 与 outbox。runner manifest 同样 0600。
- 同一 UID 下 0600 不隔离 Desk LLM 与 timer：强制边界是每 Agent 独立身份、最小 scope、固定命令、无 LLM 的调度路径、审计与 readback。
- `publisher_pubkey` 与路由 `sender_pubkey` 必须等于 Desk 私钥派生 pubkey。ADR-0006：频道成员即受众授权，配置不得再出现 `audience` 块。
- 默认路由由同一轮 runner 调用 `gitlab_buzz_route_reply.py --scan-once`，使用 Desk identity 和显式 Role `p` tag；不需要路由 Workflow、listener、ingress 或 bearer。
- Canvas 只允许编辑 route id、完整事实 header prefix、Role id 与 reason。Canvas admin allowlist、Role identity、Agent prompt、skills 与 SaaS scope 留在 owner 管理的配置。

GitLab credential 就是 Desk env 里唯一的 `GITLAB_TOKEN`，由管理员签发或明确授权：启用同步时取 reporter profile（Reporter·`api,read_repository`），未启用同步的 Desk 是 Planner·`api`（见 [agent-credentials.md](../agent-credentials.md)「GitLab 角色最小档」）；只为读取事实和写确定性 binding note，不带 repository write、merge 或部署权限。

## 1. 准备配置与 manifest

1. 从 `references/scripts/gitlab-buzz-sync.example.json` 创建每个 repo／Channel 的同步配置；设置 `publisher_pubkey` 为 Desk pubkey。
2. 从 `references/scripts/gitlab-buzz-route-writer.example.json` 创建 route config；`sender_pubkey` 与 `publisher_pubkey` 都设为同一 Desk pubkey。
3. 写 owner 0600 runner manifest（`version`、`release_dir`、`sync[]`、`route`），并在 Desk env 中加入 `BUZZ_DESK_RUNNER_MANIFEST=<manifest 绝对路径>`。
4. 在 launcher 同样的白名单 env 中先跑 dry-run：同步 `--dry-run` 和路由 `--dry-run`，计数合理再继续。

manifest 内部固定、不由任何消息提供的子命令形如：

```text
python3 <SKILL_DIR>/scripts/gitlab_buzz_sync.py --config <SYNC_CONFIG> --state-dir <SYNC_STATE_DIR>
python3 <SKILL_DIR>/scripts/gitlab_buzz_route_reply.py --config <ROUTE_CONFIG> --state-dir <ROUTE_STATE_DIR> --scan-once
```

消息正文、GitLab 文本和 Canvas 不得提供或覆盖 `<SKILL_DIR>`、config、state、project、token env name 或任意 CLI 参数。Desk 的 prompt 只含 [普通 Desk 片段](../gitlab-buzz-sync.desk-prompt.md)，不运行这些命令。

## 2. launcher（环境变量白名单）

保存为 `~/.config/buzz/sync/gitlab-buzz-sync-launch.sh`，owner 所有、mode 0700。`GITLAB_TOKEN_ENVS` 必须与 manifest 中每份 sync config 的 `gitlab.token_env` 一致。launcher 出错只写 stderr（进 journal），stdout 为空，不打印任何值。

<!-- template:sync-timer-launcher -->
```bash
#!/bin/bash
# gitlab-buzz-sync-launch.sh: owner-fixed whitelist launcher for gitlab_buzz_sync_timer.py (ADR-0008).
set -euo pipefail
umask 077
GITLAB_TOKEN_ENVS=(GITLAB_TOKEN)
PROJECT_TOKEN_MAP_ENV=BUZZ_GITLAB_PROJECT_TOKEN_MAP
SAFE_PATH=/usr/local/bin:/usr/bin:/bin

fail() { printf 'gitlab-buzz-sync-launch: %s\n' "$1" >&2; exit 2; }
canonical_path() {
  "$PYTHON" -c 'import os,sys
raw=sys.argv[1]
real=os.path.realpath(raw)
raise SystemExit(0 if real == raw or (sys.platform == "darwin" and raw.startswith("/var/") and real == "/private" + raw) else 1)' "$1"
}

[ "$#" -eq 2 ] || fail "usage: /usr/bin/python3 <immutable-release>/scripts/gitlab_buzz_sync_timer.py"
[ "$1" = /usr/bin/python3 ] || fail "interpreter must be /usr/bin/python3"
PYTHON="$1"
case "$2" in
  /*/scripts/gitlab_buzz_sync_timer.py) ;;
  *) fail "entrypoint must be <immutable-release>/scripts/gitlab_buzz_sync_timer.py" ;;
esac
[ ! -L "$2" ] && [ -f "$2" ] || fail "entrypoint must be a regular non-symlink file"
canonical_path "$2" || fail "entrypoint path must be canonical"

ENVFILE="${BUZZ_SYNC_ENV_FILE:-}"
[ -n "$ENVFILE" ] || fail "BUZZ_SYNC_ENV_FILE is required"
[ ! -L "$ENVFILE" ] || fail "env file must not be a symlink"
[ -f "$ENVFILE" ] || fail "env file must be a regular file"
canonical_path "$ENVFILE" || fail "env file path must be canonical"
[ "$("$1" -c 'import os,sys; print(os.stat(sys.argv[1], follow_symlinks=False).st_uid)' "$ENVFILE")" = "$(id -u)" ] || fail "env file must be owned by the current user"
[ "$("$1" -c 'import os,stat,sys; print(format(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode), "o"))' "$ENVFILE")" = 600 ] || fail "env file mode must be 0600"

RUN_HOME="${HOME:?}"
RUN_USER="${USER:-$(id -un)}"
RUN_LOGNAME="${LOGNAME:-$RUN_USER}"
set -a
# shellcheck source=/dev/null
. "$ENVFILE"
set +a
KEEP=" HOME USER LOGNAME PATH LANG BUZZ_RELAY_URL BUZZ_PRIVATE_KEY BUZZ_AUTH_TAG BUZZ_DESK_RUNNER_MANIFEST $PROJECT_TOKEN_MAP_ENV ${GITLAB_TOKEN_ENVS[*]} "
# 单仓同步继续只依赖 config.gitlab.token_env；多仓 runtime wrapper 若被调用，
# 会在自身入口强制要求这个 owner pin，并拒绝调用方用 --config 覆盖它。
for key in BUZZ_RELAY_URL BUZZ_PRIVATE_KEY BUZZ_AUTH_TAG BUZZ_DESK_RUNNER_MANIFEST "${GITLAB_TOKEN_ENVS[@]}"; do
  [ -n "${!key-}" ] || fail "missing $key in env file"
done
# Whitelist by removal: every exported name not in KEEP is dropped, whatever it is called.
# Values stay in the environment (0400 /proc/<pid>/environ) and never become argv.
for name in $(compgen -e); do
  case "$KEEP" in *" $name "*) ;; *) unset "$name" ;; esac
done
export HOME="$RUN_HOME" USER="$RUN_USER" LOGNAME="$RUN_LOGNAME" PATH="$SAFE_PATH" LANG=C.UTF-8
cd /
# bash re-exports SHLVL and _ to children; env -u drops them without carrying any value in argv.
exec /usr/bin/env -u SHLVL -u _ "$1" "$2"
```

`source` 会执行 shell，所以先硬性确认 env 是当前用户所有、regular、非 symlink、canonical 路径、权限恰为 0600。路径、owner 与 mode 使用固定 `/usr/bin/python3` 的 **python3 realpath/stat**，避免依赖 GNU `realpath -e`／`stat -c`，同一 launcher 可在 Linux 与 macOS 使用。入口 `gitlab_buzz_sync_timer.py` 自身的 argparse 也拒绝任何参数。macOS 调度见 [launchd runbook](../launchd/README.md)。

## 3. unit 模板

替换 `<channel>`（小写短名）、`<desk>` 与 `<immutable-release>` 后保存到 `~/.config/systemd/user/`。unit 文件本身不含 secret，也不使用 `EnvironmentFile=`（那会把 Desk env 的所有键原样传进来）。

<!-- template:sync-timer-service -->
```ini
# gitlab-buzz-sync-<channel>.service
[Unit]
Description=GitLab to Buzz sync for <channel> (Desk identity, deterministic, ADR-0008)
After=network-online.target

[Service]
Type=oneshot
UMask=0077
NoNewPrivileges=yes
TimeoutStartSec=20min
Environment=BUZZ_SYNC_ENV_FILE=%h/.config/buzz/agents/<desk>.env
ExecStart=%h/.config/buzz/sync/gitlab-buzz-sync-launch.sh /usr/bin/python3 <immutable-release>/scripts/gitlab_buzz_sync_timer.py
```

<!-- template:sync-timer-timer -->
```ini
# gitlab-buzz-sync-<channel>.timer
[Unit]
Description=Run GitLab to Buzz sync for <channel> every 300 seconds

[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitActiveSec=300
Persistent=true
Unit=gitlab-buzz-sync-<channel>.service

[Install]
WantedBy=timers.target
```

- `OnUnitActiveSec=300` 以上一轮开始为基准；`Type=oneshot` 的一轮没结束时 systemd 不会并发启动第二轮。
- `Persistent=true` 按 ADR-0008 保留；systemd 只对 `OnCalendar=` 持久化错过的触发，单调计时器靠 `OnActiveSec`／`OnBootSec` 在 timer 启动或开机后补跑一轮，所以停机期间的变化由下一轮从 cursor 补齐，而不是逐轮重放。
- `systemd --user` 默认随登录会话退出；无人登录的主机必须先 `loginctl enable-linger <user>`。

## 4. 启用、手动运行、观察

```bash
loginctl enable-linger "$USER"
systemctl --user daemon-reload
# 阶段 0：先手动跑一轮，确认 exit 0、业务 Channel 符合预期、journal 无错误
systemctl --user start gitlab-buzz-sync-<channel>.service
journalctl --user -u gitlab-buzz-sync-<channel>.service -n 50 --no-pager
# L4 receipt v3 通过后才启用自动 timer
systemctl --user enable --now gitlab-buzz-sync-<channel>.timer
systemctl --user list-timers 'gitlab-buzz-sync-*'
```

- 每轮 stdout 是一个 JSON（`status`、计数、`published`），失败时带脱敏 `error`；systemd 把它记进 user journal，这是唯一的私有失败记录，业务 Channel 保持静默。
- 本轮 service 还在运行时再 `systemctl --user start`，只会并入正在进行的同一个 oneshot job，不会起第二个进程。
- `status=locked` 退出码 0：只在绕过 systemd 并行直接运行入口（例如手工执行 launcher／入口、或同 Channel 重叠项目的另一份 manifest）时出现，说明另一进程还持有 per-project lock，下一轮自动续上。
- 启用 timer 前必须停掉 Desk 旧的同步入口：Desk env 移除旧 heartbeat 间隔与 prompt file 变量，prompt 换成普通 Desk 片段并重启 Desk；公开 schedule Workflow 保持不存在。

## 5. 停用与回滚

```bash
# 1. 先停调度：disable + stop timer，之后不会再启动新一轮
systemctl --user disable --now gitlab-buzz-sync-<channel>.timer
# 2. 确认没有正在写的一轮（inactive/failed 都可以；active 就等它结束，不要 kill）
systemctl --user is-active gitlab-buzz-sync-<channel>.service
# 3. 回到不理解 attempted/group 的旧版前，先用新版把 outbox pending 排空；配置 schema 有变化时同时还原配置备份
# 4. 回滚版本：改 unit 与 manifest 的 <immutable-release> 到上一个已验证 commit
systemctl --user daemon-reload
systemctl --user start gitlab-buzz-sync-<channel>.service
systemctl --user enable --now gitlab-buzz-sync-<channel>.timer
```

- 升级前除 manifest、unit 外，还要备份每份 sync config。回滚到不认识 `compact_status_updates` 的旧 release 时必须还原该 config 备份（或先删除新键并用旧版 `validate_config` 回读），不能只改代码路径。
- cursor、outbox 与 binding 的回滚策略不同：cursor 与 binding 保持原样（0700 state、GitLab binding note），不删除 env/state、Desk membership 或 GitLab token；回到不理解 `attempted` / group continuation 的旧 release 前，outbox 的 `pending` 必须由新版逐项恢复并排到空数组。即使剩下的 kind 只是旧版认识的 `buzz_message` / `buzz_diff`，`attempted:false` 也表示它尚未调用、只能由新版安全续跑；不能交给旧版猜测，更不能直接删 state。未知 kind 同样严禁回退。
- 任何时候一个 Channel 只有这一个调度者：先 disable timer 再做任何手动运行或版本切换，因此不会出现两个 writer；手动 `start` 与 timer 撞上时只会并入同一个 oneshot job；绕过 systemd 并行直接运行入口时，per-project lock 也只让一方写入，另一方返回 `locked`。
- 只停路由：timer 停用期间在 owner manifest 禁用 route mode，再按上面步骤恢复 timer。
- 不回退到 Desk heartbeat 同步；那会重新把 LLM 放进无人值守路径并形成第二个调度者。

## 必须保存的负向与 L4 证据

1. 记录 `systemctl --user cat` 的 service／timer 内容与 SHA-256、launcher SHA-256、固定 Skill revision、manifest digest；证明没有旧 sync daemon、loopback listener、Desk heartbeat 或公开 schedule Workflow。
2. timer 进程 env 只含白名单（读 `/proc/<pid>/environ` 只记录变量名）；其它 Agent 的 token 和私钥不可读。配置的 `publisher_pubkey`、route `sender_pubkey` 与 Desk 私钥派生值三者精确一致。
3. launcher 拒绝相对 `python3`、其它脚本、附加参数、非 0600 或 symlink env；入口拒绝任何 argv。Channel 消息正文、GitLab 标题或 Canvas 里的命令都不能改变实际 argv；Desk 收到「@Desk gitlab sync」不会运行同步。
4. private／internal 项目在预检和每次外部写入前都通过 visibility 检查；成员对账已按 ADR-0006 废除（频道成员即授权）。
5. 合法同步产生唯一 root／reply 与 binding note，author 都是 Desk；PENDING 在严格 readback 后成为 ACKED，首个失败停止后续写入，cursor 不推进，timer 退出码 1 且 Channel 零消息。
6. 手动 `start` 与 timer 重叠时只有一个 job／进程；绕过 systemd 并行直接运行入口、send 前／后崩溃和 relay readback 暂时失败都不产生第二条外部写入，并行直接运行的后到进程返回 `locked`。
7. 最新 Canvas 由 allowlisted admin 发布时，合法 Desk `change:routing` fact 得到唯一 Desk route reply，位于 canonical Thread 且只有一个 Role `p` tag。伪造作者、恶意最新 Canvas、unknown/executor Role、closed/unmatched fact 均不回复。
8. Role Agent 的 `respond_to` 明确允许 Desk identity；不得为兼容而静默改成 `anyone`。
9. 空轮零 Channel 消息；有 push 活动时恰好一条 `template_summary` 摘要，通过 publisher gates，无内部 ID、无重复。
10. disable timer 后不再有新一轮；重新 enable 后从兼容的 cursor／outbox 续上。所有 Agent 的业务消息遵守责任人注意力预算：只有需行动／评审／决定／解除阻塞才加入最多 3 个已验证 human `p` tag。

只有以上证据和分阶段 L4 场景都通过后，才可 `enable --now` timer；公开 schedule Workflow 永不恢复。`call_webhook` + HTTP route-reply listener 只是无法在 timer 中执行本地固定路由脚本时的降级适配器；它会引入 Channel 成员可读 bearer、ingress 与额外 L4，不能与本地 route gate 同时启用。
