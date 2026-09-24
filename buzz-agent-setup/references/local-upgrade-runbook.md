# 本机升级清单：skill 合并进 main 之后，本机哪几处要跟着动

skill 合并进 main，本机**不会**自动跟上。一次完整升级必须把同一个 main 上的 **40 位 commit** 收敛到本机所有 Buzz 运行面，并用只读审计器证明没有漏项。release 放在 `~/.local/share/buzz-agent-setup/releases/<40 位>`；不用分支名、`latest` 或开发者的工作树，短 SHA 也不接受。

默认采用 **full convergence**：同步、飞书镜像、todo、入群申请、责任人 helper、ACP 图片代理和实际在用的 harness 插件都来自同一个目标 SHA。`git diff` 只决定需要额外做哪些迁移／live canary，不再作为“哪些旧 pin 可以继续留下”的理由。这样多重依赖和间接 import 不会因人工判断漏更新。

这是同一 owner UID 内的**确定性配置漂移门禁**，不是抵抗同 UID 恶意进程的安全证明：owner 仍能 chmod/替换自己的 release、unit 与审计器。canonical builder 会重新从独立 Skills Git object 导出同一 target commit 并逐文件比对，防止误把任意目录贴成该 SHA；若 threat model 包含已经取得 owner UID 写能力的 Agent，必须另行使用独立 OS principal／root-owned verifier 与 release，不能把本 P1 receipt 当成供应链签名。

`scripts/audit_local_alignment.py` 是升级的离线、只读 P1 门禁，必须由可信父进程用 `/usr/bin/python3 -I` 启动。它从当前 systemd 用户单元发现 agent／后台组件，不硬编码数量；校验共享 launcher、完整 unit 模板、manager 实际加载值与 `NeedDaemonReload=no`，并遍历 systemd user lookup 路径检查同名 shadow 与 unit／类型／dash-prefix drop-in（只要存在就报 `unknown`，不读内容），再校验 release 内容清单、manifest、env 的**键名**、prompt、责任人配置、固定 Buzz CLI 的二进制哈希、沙箱 settings 和实际 enabled 的插件。对入群审批还要求配置精确覆盖所有适用业务 Agent、逐个绑定正确 env/unit、每个 env 有 `BUZZ_ACP_CHANNELS`，并在审计真实本机时读取 `buzz-agent-join.timer` 的 enabled/active 状态。输出 JSON 或摘要，不输出 env 值、token、pubkey 或 registry 原值。它也只按文件名发现 `/run/user/<uid>/systemd/transient/` 下的 Buzz service/timer，因瞬时 unit 可能内嵌整份密钥环境，**绝不读取其内容**，而是报 `unknown` 促使迁到持久 unit。任一 `fail` 或 `unknown` 都返回非零。它不读 relay／GitLab，也不替你停 timer、改文件、重启或 enable；这些仍按本文明确执行。

检查结果按 [local-alignment-gap-taxonomy.md](local-alignment-gap-taxonomy.md) 的 12 类归档；其中 `fail`、`unknown`、`not_applicable` 含义不同，报告和验收不得混写。

## 1. release 钉在几处

**适用的新功能默认启用**：合并并纳入 release 的后台能力，只要适用于本机现有 agent，升级就必须同时生成或迁移配置、安装并 enable 对应 service/timer、手动跑一轮，再读回 unit 和业务状态。不能把新能力留成“文档里有、代码里有、运行时没有”的可选项，也不能静默跳过；无法安全生成配置时，本次升级应明确失败，写出具体缺项与补救方法。平台类 agent 或 executor 等明确不适用的角色仍按各自 fail-closed 策略处理。

| # | 钉的地方 | 钉的是什么 | 完整升级要做什么 |
|---|---|---|---|
| 1 | GitLab → Buzz 同步：Desk env 的 `BUZZ_DESK_RUNNER_MANIFEST` 与 `gitlab-buzz-sync-<channel>.service` | manifest 的 `release_dir`，以及 canonical launcher 后的 `gitlab_buzz_sync_timer.py` 路径 | 两处都切到同一个 release；unit 固定走 `~/.config/buzz/sync/gitlab-buzz-sync-launch.sh /usr/bin/python3 <release>/scripts/gitlab_buzz_sync_timer.py`，不能改成自制 launcher |
| 2 | 飞书镜像 `buzz-feishu-<channel>.service`、个人 todo `gitlab-todo-sync-<name>.service`、`buzz-agent-join.service` | `ExecStart` 里的脚本路径 | 三类 unit 都切到新 release；timer 与 service 必须成对发现，todo/sync launcher 必须与文档模板一致 |
| 3 | 每个 agent 的 prompt | `<release>/scripts/buzz_send_with_responsible_mentions.py` | helper 路径切到新 release；同时检查通用 prompt 条款和角色条款 |
| 4 | 每个 agent 的沙箱 settings 与 `BUZZ_RESPONSIBLE_CONFIG` | `allowRead` 里的 release 目录、责任人配置、配置的 `people_file` | 三条路径都放行；责任人配置保持 v2、0600、非 symlink |
| 5 | 每个实际在用的 harness 插件副本 | agent 真正加载的 Skill revision | 审计器不执行任何 harness／wrapper：Claude 按 launcher 显式校验的 `CLAUDE_CONFIG_DIR` 静态合并 user、project、remote 与 managed settings，任一高优先级来源禁用插件即 fail；Grok 读 registry；Codex 静态读取完整 canonical `CODEX_HOME/config.toml` 的 enabled plugin／Git marketplace，再要求 `plugins/cache/addx/addx/` 只有一个安装目录，回读其 `.codex-marketplace-install.json` 和完整 Skill tree；多份／异常 cache、非 canonical remote/main 或另一个 PATH 命中一律 fail closed；更新后重启使用它的 agent |
| 6 | 每个 agent 的 ACP 图片代理 | `BUZZ_ACP_AGENT_COMMAND` 指向的代理文件摘要，以及 `BUZZ_ACP_MEDIA_ADAPTER_COMMAND`／`BUZZ_ACP_MEDIA_BUZZ_CLI` | 代理内容必须与目标 release 的 `buzz_acp_media_proxy.py` 完全一致，目录名是其 64 位 SHA-256，真实 adapter 与固定 Buzz CLI 都回读为可信 executable |

所有 agent 共用的 `~/.config/buzz/agents/run-agent.py` 必须逐字安装自 `<release>/references/scripts/run-agent.py`（0500）；每个 `buzz-local-<agent>.service` 由固定 `/usr/bin/python3 -I` 直接执行它并传入唯一的 agent 名，不再保留 shell wrapper。unit 还必须用 canonical `UnsetEnvironment=` 在解释器／loader 之前删除 LD／Python／shell 注入变量；`-I` 再隔离 Python user site。launcher 用同一 fd 读取 0600 env、按 literal 解析并从空环境构造子进程。机器相关的 PATH 放进该 agent 的 `BUZZ_AGENT_SAFE_PATH`，且每个目录必须 canonical、可信 owner、组和其他人不可写；最终 `buzz-acp` 则必须另以 `BUZZ_ACP_BINARY` 和 `BUZZ_ACP_BINARY_SHA256` 固定，不能从 PATH 回退。

`<release>` 根下直接是 `scripts/` 与 `references/`：runner、todo 和 helper 都按 `<release>/scripts/<name>.py` 找脚本，并会继续读取 `<release>/references/scripts/`，所以解包时去掉前两层目录。历史 `releases/feishu-group-sync-<短 sha>/skills/buzz-agent-setup/…` 是旧布局；两种别放进同一个目录，新升级统一用 40 位 SHA 布局。

## 2. 改了什么，该动哪几处

先看变更，用于决定迁移与 live canary；不用于豁免 full convergence：

```bash
git diff <旧 40 位> <新 40 位> -- skills/buzz-agent-setup/scripts skills/buzz-agent-setup/references/scripts
```

| diff 里出现 | 额外关注（所有 pin 仍统一切新 SHA） |
|---|---|
| `gitlab_buzz_sync.py`、`gitlab_buzz_route_reply.py`、`gitlab_buzz_summary_publish.py`、`gitlab_buzz_desk_runner.py`、`gitlab_buzz_sync_timer.py` | 同步 manifest 的 `release_dir` 与 unit 入口一起切；launcher 保持 canonical 模板；逐配置 dry-run、阶段 0 与失败 oracle |
| `buzz_feishu_group_sync.py` | 每个 `buzz-feishu-<channel>.service` 的 `ExecStart`，手动一轮读 `errors` |
| `gitlab_todo_sync.py` | 每个 `gitlab-todo-sync-<name>.service` 的 `ExecStart`，手动一轮核对去重与回写门禁 |
| `buzz_agent_join_requests.py` | `buzz-agent-join.service` 的入口与 timer，手动一轮核对 `status`／`drift` |
| `buzz_acp_media_proxy.py` | 为所有 agent 安装新摘要目录，原子更新三个 `BUZZ_ACP_MEDIA_*`／command 键，逐 agent 重启并做图片 L3；真实图片效果仍按独立 L4 |
| `buzz_responsible_mentions.py`、`buzz_send_with_responsible_mentions.py` 或 `buzz-responsible-mentions.example.json` | prompt＋沙箱 `allowRead`＋`BUZZ_RESPONSIBLE_CONFIG` 一起切，逐 agent 重启；配置契约变化必须带迁移窗口 |
| `SKILL.md`、`references/*.md` | 所有实际在用的插件副本升级并重启 agent；不能只更新 owner 的交互 harness |
| `provision_gitlab_agent_token.py`、`mint-agent.py` 等一次性运维脚本 | 不钉在任何地方；下一次从新 release 直接跑 |

过去用“两个 helper 脚本逐字相同，所以不用动 prompt／沙箱”的优化，能减少重启，但会长期保留多个 release pin，也无法覆盖间接依赖。完整模式不再保留这个分叉；行为没变时 canary 可以缩小，revision 仍统一。

## 3. 顺序：先做不破坏的部分

1. **冻结目标并建只读 release**（只接受 canonical remote `main` 的 40 位 commit；旧 release 原样保留）：不要信任日常开发工作树作为发布源；先在已验证祖先链下的 `0700` 临时目录从固定 remote 做 shallow bare clone，再从 commit 对象取出不超过 1 MiB 的目标 builder，由它用受限 `ls-tree`＋`cat-file` 逐 blob 物化。这样既不复制工作树，也不经过可被 repo-local attributes 改写的 `git archive`。所有被校验路径的非 sticky 祖先目录都必须属于 root／当前 UID 且无 group/world write；不能用“当前组看起来只有自己”代替这一条件，因为 ACL 或存量 supplementary GID 仍可能允许另一 UID 替换路径。以下 `verify_chain` 在任何写入或执行前 fail closed；若已有祖先不合格，先人工确认目标再单独修权限并从头重跑，命令块不会自动递归改权限。

   ```bash
   set -euo pipefail
   export PATH=/usr/bin:/bin
   SOURCE_REMOTE=git@gitlab.addx.ai:engineering/skills.git
   TARGET_REF=refs/heads/main
   BASE="$HOME/.local/share/buzz-agent-setup/releases"
   verify_chain() {
     /usr/bin/env -i HOME="$HOME" PATH=/usr/bin:/bin /usr/bin/python3 -I -c '
import os, stat, sys
from pathlib import Path
target = Path(sys.argv[1])
if not target.is_absolute() or ".." in target.parts:
    raise SystemExit(2)
uid = os.geteuid()
current = Path("/")
for part in target.parts[1:]:
    current /= part
    try:
        metadata = current.lstat()
    except FileNotFoundError:
        break
    mode = stat.S_IMODE(metadata.st_mode)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, uid}
            or (mode & 0o022 and not mode & stat.S_ISVTX)):
        raise SystemExit(2)
' "$1"
   }
   verify_chain "$BASE"
   install -d -m 700 "$BASE"
   verify_chain "$BASE"
   SOURCE=
   TMP=
   BOOTSTRAP=
   cleanup() { if [ -n "${TMP:-}" ] && [ -d "$TMP" ]; then chmod -R u+w "$TMP"; rm -rf -- "$TMP"; fi; if [ -n "${BOOTSTRAP:-}" ]; then rm -f -- "$BOOTSTRAP"; fi; if [ -n "${SOURCE:-}" ] && [ -d "$SOURCE" ]; then chmod -R u+w "$SOURCE"; rm -rf -- "$SOURCE"; fi; }
   trap cleanup EXIT
   SOURCE=$(mktemp -d "$BASE/.source.XXXXXX")
   umask 077
   ( ulimit -f 524288
     exec /usr/bin/timeout --signal=TERM --kill-after=10s 300s \
       /usr/bin/env -i HOME="$HOME" PATH=/usr/bin:/bin LANG=C.UTF-8 \
       SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-}" GIT_TERMINAL_PROMPT=0 \
       GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
       /usr/bin/git clone --bare --no-tags --no-local --single-branch \
       --branch main --depth 1 "$SOURCE_REMOTE" "$SOURCE/repo" )
   SHA=$(/usr/bin/timeout --signal=TERM --kill-after=5s 30s \
     /usr/bin/env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 \
     GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
     /usr/bin/git --no-replace-objects -C "$SOURCE/repo" rev-parse "$TARGET_REF^{commit}")
   test "${#SHA}" -eq 40
   case "$SHA" in *[!0-9a-f]*) exit 2;; esac
   REL="$BASE/$SHA"
   TMP=$(mktemp -d "$BASE/.release-$SHA.XXXXXX")
   BOOTSTRAP=$(mktemp "$BASE/.builder-$SHA.XXXXXX")
   ( ulimit -f 2048
     /usr/bin/timeout --signal=TERM --kill-after=5s 30s \
       /usr/bin/env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 \
       GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_ATTR_NOSYSTEM=1 \
       /usr/bin/git --no-replace-objects -C "$SOURCE/repo" cat-file blob \
       "$SHA:skills/buzz-agent-setup/scripts/build_release_manifest.py" >"$BOOTSTRAP" )
   test -s "$BOOTSTRAP"
   test "$(/usr/bin/wc -c <"$BOOTSTRAP")" -le 1048576
   chmod 0500 "$BOOTSTRAP"
   umask 022
   /usr/bin/python3 -I "$BOOTSTRAP" --extract \
     --release "$TMP" --commit "$SHA" --source-repo "$SOURCE/repo"
   rm -f -- "$BOOTSTRAP"
   BOOTSTRAP=
   chmod -R a-w "$TMP"
   exec 9>"$BASE/.publish.lock"
   flock -x 9
   test ! -e "$REL"
   mv -T "$TMP" "$REL"
   TMP=
   flock -u 9
   chmod -R u+w "$SOURCE"
   rm -rf -- "$SOURCE"
   SOURCE=
   trap - EXIT
   ```

2. **先留升级前原始盘点，再改任何部署文件**：release 发布后立即用它的审计器对目标 SHA 跑一次，只读保存 JSON receipt。此时不要先创建角色映射，也不要先补 prompt、env、unit 或 settings；否则 receipt 已经不是升级前事实。审计器用 `systemd-analyze --user unit-paths` 和 user manager 的 effective inventory／`FragmentPath`／effective properties 核对真实加载面，也扫描 `/run/user/<uid>/systemd/transient/` 的 unit 文件名，但绝不读取其内容；它只静态解析部署数据，不 import／执行 target release 的其它 Python module，也不执行 Claude／Codex／Grok wrapper。任何 effective 面无法确定、`NeedDaemonReload=yes`、瞬时 Buzz unit 或 drop-in 都报 `unknown`。旧 pin 的 `revision_mismatch`、缺 `BUZZ_ACP_BINARY*`、缺角色映射以及 Buzz CLI／ACP 固定证据不足，均是这份 receipt 中的待办，不在盘点前偷偷修掉。JSON 的 `gaps` 必须逐一列出 LA-01 到 LA-12 的状态和计数。

   ```bash
   RECEIPTS="$BASE/receipts"
   install -d -m 700 "$RECEIPTS"
   PRE_RECEIPT="$RECEIPTS/pre-$SHA-$(date -u +%Y%m%dT%H%M%SZ).json"
   RECEIPT_TMP=$(mktemp "$RECEIPTS/.pre-$SHA.XXXXXX")
   set +e
   ( unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP BASH_ENV ENV GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR
     exec /usr/bin/python3 -I "$REL/scripts/audit_local_alignment.py" --expected-sha "$SHA" --json ) >"$RECEIPT_TMP"
   AUDIT_RC=$?
   set -e
   if [ "$AUDIT_RC" -ne 0 ] && [ "$AUDIT_RC" -ne 2 ]; then rm -f -- "$RECEIPT_TMP"; exit "$AUDIT_RC"; fi
   /usr/bin/python3 -I -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); assert set(value) >= {"ok","expected_sha","inventory","inventory_ids","summary","gaps","checks"}; assert value["expected_sha"] == sys.argv[2]; assert set(value["gaps"]) == {f"LA-{n:02d}" for n in range(1,13)}' "$RECEIPT_TMP" "$SHA"
   mv -T "$RECEIPT_TMP" "$PRE_RECEIPT"
   ```

3. **同步预检**：对每份 sync 配置，用与 launcher **相同的白名单 env** 跑 `--dry-run`。不要从自己的交互 shell 直接跑；它带着个人变量，不能代表 timer 行为。

   ```bash
   ( set -a; . <Desk 的 0600 env>; set +a
     exec env -i HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \
       BUZZ_RELAY_URL="$BUZZ_RELAY_URL" BUZZ_PRIVATE_KEY="$BUZZ_PRIVATE_KEY" BUZZ_AUTH_TAG="$BUZZ_AUTH_TAG" \
       BUZZ_DESK_RUNNER_MANIFEST="$BUZZ_DESK_RUNNER_MANIFEST" GITLAB_TOKEN="$GITLAB_TOKEN" \
       /usr/bin/python3 "$REL/scripts/gitlab_buzz_sync.py" --config <sync 配置> --state-dir <sync state> --dry-run )
   ```

   子 shell 里 source，值不进外层；`GITLAB_TOKEN` 换成该配置 `gitlab.token_env` 声明的名字。一份一份跑，任何失败都不切换。

4. **先完整备份，再补契约并分批切换**：在这一步第一次写部署面之前，先把所有将改的 manifest、unit、env、prompt、责任人配置和沙箱 settings 备份成 `<原名>.bak.<时间>`，并记录每个 harness 的旧 revision。以下未版本化的共享部署面也必须逐一备份（不存在则记录为 absent）：`~/.config/buzz/agents/run-agent.py`、`~/.config/buzz/agents/local-alignment-roles.json`、`~/.config/buzz/sync/gitlab-buzz-sync-launch.sh`、`~/.config/buzz/todo/gitlab-todo-sync-launch.sh`；任何 prompt／角色映射修补都必须在备份之后。根据升级前 receipt 逐一处理实际路径：从 leaf 到 `$HOME` 的每个非 sticky 祖先都要回读 owner/mode，并先移除 group/world write；不要递归改业务文件权限，也不要把 sticky 目录当成普通目录收紧。然后维护 0600 的 `local-alignment-roles.json`（`{"version":1,"roles":{"gitsecops-desk":"platform-desk","nh-desk":"desk","nh-dev":"dev"}}` 仅为结构示例；实际 `roles` 逐一覆盖本机发现的所有 agent），把 [agent-prompt-contract.md](agent-prompt-contract.md) 对应片段装进 prompt，再开始以下分批切换：
   - 同步：先 `disable --now` timer、确认 service 不在运行；备份 manifest 与 unit，把 `release_dir` 和 canonical launcher 后的 timer 入口一起改为 `$REL`；从 [systemd/README.md](systemd/README.md) 重装 launcher／service／timer 模板，`daemon-reload` 后手动 start 一轮。
   - 飞书镜像／todo／入群申请：先停 timer，备份 service，把 `ExecStart` 切到 `$REL`；todo launcher 从 [systemd/personal-todo-sync.md](systemd/personal-todo-sync.md) 重装，`daemon-reload`，手动 start 一轮。
   - harness：分别更新 Claude／Codex／Grok 实际在用的插件副本；用 [runtime-setup.md](runtime-setup.md) 的 resolver 回读 revision。
   - agent：先解析当前真实目标 `ACP=$(readlink -f -- "$(command -v buzz-acp)")`，确认 `file "$ACP"` 是 ELF、它是可信 owner 的 canonical regular executable 且组／其他人不可写，再取 `ACP_SHA=$(sha256sum -- "$ACP" | cut -d' ' -f1)`。逐个备份 0600 env，用保持 0600、同目录临时文件＋`fsync`＋`os.replace` 的方式同时写入 `BUZZ_ACP_BINARY=$ACP` 和 `BUZZ_ACP_BINARY_SHA256=$ACP_SHA`（不能原地 `sed -i`，不能打印 env）；Claude env 还要显式写入与 wrapper 匹配的 canonical `CLAUDE_CONFIG_DIR`。先跑审计确认该 agent 的 `launcher_preflight_ok`。然后以 `install -m 0500 "$REL/references/scripts/run-agent.py" ~/.config/buzz/agents/run-agent.py` 重装共享 launcher，把机器 PATH 固定在 `BUZZ_AGENT_SAFE_PATH`；再逐个在空闲窗口把 unit 切到带 canonical `UnsetEnvironment=` 的 `/usr/bin/python3 -I` direct `ExecStart`，并把 prompt helper 路径、沙箱 release `allowRead`／`permissions.deny`、责任人配置（若契约变了）与 [acp-media-proxy.md](acp-media-proxy.md) 的新代理摘要目录一起切换，立即重启并验收，再换下一个。

5. **读 live 结果再恢复 timer**：同步 journal 读 `status`，其值是 `ok` 且计数合理；飞书镜像 `errors` 为 0；todo 的去重／可信作者门禁正常；入群申请的 `status`、积压与 `drift` 合理。通过后再 enable 对应 timer。

6. **最终审计必须归零并留 receipt**：再次使用第 2 步相同的 clean-parent 子 shell；最终 JSON 必须成功、目标 SHA 与 LA-01…LA-12 完整，且持久服务数量不能相对升级前 receipt 静默增减（确需改变 topology 时应先单独审批并重建 baseline）。`transient_services` 与 `lookup_only_units` 最终都必须为 0。

   ```bash
   POST_RECEIPT="$RECEIPTS/post-$SHA-$(date -u +%Y%m%dT%H%M%SZ).json"
   RECEIPT_TMP=$(mktemp "$RECEIPTS/.post-$SHA.XXXXXX")
   set +e
   ( unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP BASH_ENV ENV GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR
     exec /usr/bin/python3 -I "$REL/scripts/audit_local_alignment.py" --expected-sha "$SHA" --json ) >"$RECEIPT_TMP"
   AUDIT_RC=$?
   set -e
   if [ "$AUDIT_RC" -ne 0 ]; then rm -f -- "$RECEIPT_TMP"; exit "$AUDIT_RC"; fi
   ( unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH PYTHONHOME PYTHONPATH PYTHONINSPECT PYTHONSTARTUP BASH_ENV ENV GLIBC_TUNABLES GCONV_PATH LOCPATH NLSPATH MALLOC_TRACE RES_OPTIONS HOSTALIASES TZDIR
     exec /usr/bin/python3 -I "$REL/scripts/compare_local_alignment_receipts.py" \
       --before "$PRE_RECEIPT" --after "$RECEIPT_TMP" --expected-sha "$SHA" )
   mv -T "$RECEIPT_TMP" "$POST_RECEIPT"
   ```

   comparator 会先校验 receipt 的完整 schema、`checks`↔`summary`↔`gaps` 交叉计数、inventory↔identity 数量、可信父目录链和稳定读取，再比较升级前后持久 topology；缺 `checks`、空 inventory 或手工伪造 summary/gaps 都会失败。只有 receipt 中 `ok=true`、`fail=0, unknown=0`，且 LA-01…LA-12 每类都有确定状态，再叠加上一步 live 结果，才叫本机升级完成。审计器会拒绝未清理上述变量的父环境；`-I` 不能清掉 dynamic loader 变量。审计 PASS 只证明静态/P1 对齐，不冒充服务 active、真实消息效果或 GitLab/Buzz L4。

## 4. 破坏性变更窗口：责任人 helper 配置 v1 ↔ v2

!962 起 helper 只认 v2（`version` 是整数 `2`；`channels` 由对象改成 UUID 数组；新增 `people_file`）；旧 helper 只认 v1，v2 helper 又不认 `canvas_alias`，两边互不认。一个 agent 的 prompt helper 路径、`BUZZ_RESPONSIBLE_CONFIG` 和沙箱 `allowRead` 必须一起换、随即重启，中间错配会让责任人通知失败关闭。

**逐个 agent**，在它空闲时：

1. 备份 prompt、配置和沙箱 settings。
2. 一次装好新 prompt、v2 配置、新 release／责任人配置／`people_file` 的 `allowRead`。
3. **立即重启**。
4. 按第 5 节验证；通过再换下一个。

Workflow 正文里的 `canvas_alias` locator 改成 `person`，要在**被它唤醒的那个 agent 切换之后**再改；Canvas 里的别名表**最后**才删。所有 agent 完成并回读前删别名表，会让未切换的 agent 失效，也切断回滚。

未来任何“旧配置被新脚本拒绝”的变更，MR 必须同时提供迁移器或确定性迁移步骤、dry-run、备份／原子写／回滚和正负向测试，不能只改 schema。

## 5. 验证

- **静态总门禁**：只能使用第 2 步同一个 clean-parent 子 shell 和 `/usr/bin/python3 -I <release>/scripts/audit_local_alignment.py --expected-sha <40 位 SHA>`；release 内容清单、持久／瞬时发现集合、systemd lookup shadow／drop-in／manager 实际加载值、完整 unit 模板、共享 launcher、env/prompt 权限与角色映射、责任人 v2 与 `people_file`、固定 Buzz CLI 哈希、ACP 图片代理摘要、沙箱 `allowRead`、sync manifest、飞书/todo/join unit、Claude/Codex/Grok 实际插件 revision 任一不一致都非零。只有 `fail=0, unknown=0` 才通过；JSON 可保存为不含 secret、带 LA-01…LA-12 聚合结果的 receipt。
- agent 服务 active：`systemctl --user is-active buzz-local-<name>.service`；进程启动时间晚于 prompt、配置、沙箱和 plugin revision 的变更时间。
- 沙箱真实会话补探针：`wc -c < <people_file>` 非零；用 `importlib` 加载 `<release>/scripts/buzz_send_with_responsible_mentions.py` 并让 `load_config` 读 `BUZZ_RESPONSIBLE_CONFIG`。静态审计不能替代真实沙箱可读性。
- 同步 runner 手动一轮 `status=ok`；飞书镜像 `errors` 为 0；todo 与 join 手动一轮正常；插件副本用 `resolve_plugin_install.py` 回读，`git_commit_sha` 等于目标 SHA。
- 最终同时记录：目标 SHA、inventory 计数、静态审计摘要、各 service/timer 的 live 结果与尚未覆盖的 P2/P3。指标为 0、未部署某类组件、无法判断要分开写。

## 6. 回滚

- release 目录**不删、不覆盖**；旧 release 保留，回滚就是把所有 pin 收敛到同一个旧 SHA，而不是只退一部分。
- manifest、unit、prompt、责任人配置、沙箱 settings 写前都留 `*.bak.<时间>`；插件保留可重新安装的旧 revision。
- 同步／飞书／todo／join：先停 timer。回到不能理解新版 outbox 的旧 release 前，必须先用新版逐项恢复并排空 `pending`（包括旧／未知 kind）；存在 `pending` 或未知 kind 时禁止回退，不能把状态直接交给旧版或删除 state。排空后再恢复备份或旧 release 路径，`daemon-reload`，手动 start 一轮，再 enable。
- agent：原来的 prompt、责任人配置、沙箱**三样一起还原**，并同时还原 env（包括旧 `BUZZ_ACP_BINARY*` pin）、unit、`run-agent.py` 与 `local-alignment-roles.json` 后重启；sync/todo 也恢复各自的共享 launcher。备份时记为 absent 的共享文件要移除，不能遗留新版本。Canvas 别名表还在，才回得去（helper v1 仍依赖它）。若明确回到 stock text-only，必须按 runtime-setup 写 `BUZZ_ACP_MEDIA_MODE=stock_text_only`，不能只删代理键。
- **首次迁入本规范时的旧版本回滚是 legacy/degraded 回滚**：恢复全部备份、重新安装各 harness 真实旧 revision、`daemon-reload` 并回读 effective enabled 状态，再重启 agent 和逐个手动验证业务。因为旧 release 没有内容清单、canonical launcher 等新契约，当前 P1 审计预期仍会报告 drift，不能把它写成 full convergence PASS；稳定后要 forward-fix 回新版本。只有新旧两个 release 都已支持本规范时，回到**旧目标 SHA**、同步恢复其 binary/plugin pin 后，才可用第 2 步相同的 clean-parent 子 shell执行 `/usr/bin/python3 -I <旧 release>/scripts/audit_local_alignment.py` 并要求 `fail=0, unknown=0`。cursor、outbox、binding 和 state 目录始终不删除。
