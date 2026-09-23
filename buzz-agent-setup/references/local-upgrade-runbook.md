# 本机升级清单：skill 合并进 main 之后，本机哪几处要跟着动

skill 合并进 main，本机**不会**自动跟上：同步脚本钉在只读的 release 目录里，agent 读的是各自 harness 的插件副本，prompt 里写死了责任人 helper 的路径，沙箱又按这些路径放行读取。漏动一处，就是「之前的更新忘了同步」：2026-09-21 一次全量对齐发现 12 类 gap，很多是这一类。本页只管「本机怎么跟上」，每一处的细节回到各自的 reference。

release 目录用 main 上的 **40 位 commit** 建，放在 `~/.local/share/buzz-agent-setup/releases/<40 位>`；不用分支名、`latest` 或开发者的工作树。

## 1. release 钉在几处

| # | 钉的地方 | 钉的是什么 | 细节 |
|---|---|---|---|
| 1 | GitLab → Buzz 同步 runner：manifest（路径在 Desk env 的 `BUZZ_DESK_RUNNER_MANIFEST`，如 `desk-runner.json`）与 `gitlab-buzz-sync-<channel>.service` | manifest 的 `release_dir`；unit `ExecStart` 里的 `<immutable-release>`。两处必须是同一个目录 | [systemd/README.md](systemd/README.md) |
| 2 | 飞书镜像 `buzz-feishu-<channel>.service`、个人 todo 同步 `gitlab-todo-sync-<name>.service` 的 systemd 单元 | `ExecStart` 里的脚本路径 | [feishu-group-sync.md](feishu-group-sync.md)「systemd --user 示例」、[systemd/personal-todo-sync.md](systemd/personal-todo-sync.md) |
| 3 | 每个 agent 的 prompt | 责任人 helper 的路径 `<release>/scripts/buzz_send_with_responsible_mentions.py` | [runtime-setup.md](runtime-setup.md)「发消息前的责任人注意力门禁」 |
| 4 | 每个 agent 的沙箱 settings 与 `BUZZ_RESPONSIBLE_CONFIG` 指向的配置 | `allowRead` 里的 release 目录；配置里的 `people_file` 路径（也要在 `allowRead` 里） | [agent-sandbox.md](agent-sandbox.md) |
| 5 | 每个 harness 的插件副本 | agent 实际读到的 skill 文本 | [runtime-setup.md](runtime-setup.md)「验证运行时实际加载的 Skill revision」 |

`<release>` 根下直接是 `scripts/` 与 `references/`：runner、todo 单元和 helper 都按 `<release>/scripts/<name>.py` 找脚本，脚本还会去找 `<release>/references/scripts/`，所以解包时去掉前两层目录（见第 3 节）。飞书镜像那份例子（`releases/feishu-group-sync-<短 sha>/skills/buzz-agent-setup/…`）保留了完整路径前缀，是另一种布局，两种别放进同一个目录。

## 2. 改了什么，该动哪几处

先看这次升级动了哪些脚本：

```bash
git diff <旧 40 位> <新 40 位> -- skills/buzz-agent-setup/scripts skills/buzz-agent-setup/references/scripts
```

| diff 里出现 | 该动 |
|---|---|
| `gitlab_buzz_sync.py`、`gitlab_buzz_route_reply.py`、`gitlab_buzz_summary_publish.py`、`gitlab_buzz_desk_runner.py`、`gitlab_buzz_sync_timer.py` | 同步 runner：manifest 的 `release_dir` 与 unit 的 `ExecStart` 一起切到新 release（第 1 行） |
| `buzz_feishu_group_sync.py` | 每个频道的飞书镜像单元 `buzz-feishu-<channel>.service` 的 `ExecStart`（第 2 行） |
| `gitlab_todo_sync.py` | 个人 todo 同步单元 `gitlab-todo-sync-<name>.service` 的 `ExecStart`（第 2 行） |
| `buzz_responsible_mentions.py`、`buzz_send_with_responsible_mentions.py`，或 `references/scripts/buzz-responsible-mentions.example.json`（配置契约） | 每个 agent 的 prompt（helper 路径）＋沙箱 `allowRead`（新 release 目录、`people_file`）＋ `BUZZ_RESPONSIBLE_CONFIG` 指向的配置，并**重启**：走第 4 节 |
| `SKILL.md`、`references/*.md`（skill 文本，不在上面的 diff 里，随时都要更新） | 每个 harness 的插件副本，并**重启** agent（第 5 行） |
| `provision_gitlab_agent_token.py`、`mint-agent.py` 等一次性运维脚本 | 不钉在任何地方，下次从新 release 直接跑 |

同步 runner、飞书镜像、todo 同步和责任人 helper 各自钉着自己的 release 目录，目录里各有一份它们共同 import 的 `gitlab_buzz_sync.py`（todo 同步和 helper 还 import `buzz_responsible_mentions.py`）。只有这些共享脚本变了，其它几处不换就继续用旧的那份，是自洽的；想让它们吃到改动才换。

**helper 要不要动**：看 diff 里 `buzz_responsible_mentions.py`、`buzz_send_with_responsible_mentions.py` 和 `buzz-responsible-mentions.example.json` 有没有变。都逐字相同，说明 helper 与配置契约没变，agent 继续用旧 release，**不用重装 prompt、沙箱和配置，也不用重启**，只更新 runner、单元和插件副本。2026-09-21 的 `da298c11` → `15f888b6` 就是这样：改了同步脚本、飞书镜像脚本和 token 签发脚本，helper 两个脚本与配置示例逐字相同，没有动任何 agent 的 prompt 和沙箱。

## 3. 顺序：先做不破坏的部分

1. **建只读 release**（main 上的 40 位 commit，`git archive` 出来，不用工作树）：

   ```bash
   git -C <skills 克隆> fetch origin
   SHA=$(git -C <skills 克隆> rev-parse origin/main)
   REL=~/.local/share/buzz-agent-setup/releases/$SHA
   mkdir -p "$REL" && git -C <skills 克隆> archive "$SHA" skills/buzz-agent-setup | tar -x --strip-components=2 -C "$REL"
   chmod -R a-w "$REL"      # 只读；旧 release 目录保留，回滚要用
   ```

2. **预检**：对每份 sync 配置，用与 launcher **相同的白名单 env** 跑 `--dry-run`。不要从自己的交互 shell 直接跑：它带着个人变量，预检结果不代表定时器里的行为。

   ```bash
   ( set -a; . <Desk 的 0600 env>; set +a
     exec env -i HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \
       BUZZ_RELAY_URL="$BUZZ_RELAY_URL" BUZZ_PRIVATE_KEY="$BUZZ_PRIVATE_KEY" BUZZ_AUTH_TAG="$BUZZ_AUTH_TAG" \
       BUZZ_DESK_RUNNER_MANIFEST="$BUZZ_DESK_RUNNER_MANIFEST" GITLAB_TOKEN="$GITLAB_TOKEN" \
       /usr/bin/python3 "$REL/scripts/gitlab_buzz_sync.py" --config <sync 配置> --state-dir <sync state> --dry-run )
   ```

   子 shell 里 source，值不进外层 shell；`GITLAB_TOKEN` 换成该配置 `gitlab.token_env` 的名字。一份配置一份配置地跑，任何一份报错都先别切。

3. **备份后再改**：按 [systemd/README.md](systemd/README.md)「5. 停用与回滚」的顺序，先 `disable --now` timer、确认 service 不在跑；把 manifest 和 unit 各备份成 `<原名>.bak.<时间>`，再改 `release_dir` 与 `ExecStart`，`daemon-reload`。
4. **手动跑一轮，读 `status`**：`systemctl --user start gitlab-buzz-sync-<channel>.service`，`journalctl --user -u gitlab-buzz-sync-<channel>.service -n 50` 里那行 JSON 的 `status` 是 `ok`、计数合理，再 `enable --now` timer。飞书镜像单元同理手动 `start`，读报告里 `errors` 为 0（[feishu-group-sync.md](feishu-group-sync.md)「首轮之后怎么验收」）。

## 4. 破坏性变更窗口：责任人 helper 配置 v1 ↔ v2

!962 起 helper 只认 v2 配置（`version` 是整数 `2`；`channels` 由对象改成 UUID 数组；新增 `people_file`），v1 一律拒绝；旧 helper 只认 v1、不认 `person` locator，v2 helper 又不认 `canvas_alias`。**两边互不认**，所以一个 agent 上「prompt 里的 helper 路径」「`BUZZ_RESPONSIBLE_CONFIG` 指向的配置」「沙箱 `allowRead`」三样必须一起换、随即重启：中间任何一段错配，这个 agent 的责任人 @ 全部失败关闭。

**逐个 agent**，在它空闲时：

1. 备份 prompt、配置和沙箱 settings（`*.bak.<时间>`）。
2. 一次装好：prompt（helper 路径换成新 release）、v2 配置、沙箱设置（`allowRead` 加新 release 目录和 `people_file`）。
3. **立即重启**该 agent（prompt 和 env 只在启动时读一次）。
4. 按第 5 节验证，通过再换下一个。

频道里的顺序：

- Workflow 正文里的 `canvas_alias` locator 改成 `person`，要在**被它唤醒的那个 agent 切换之后**才改。
- Canvas 里的别名表**最后**才删：所有 agent 都切完、回读通过之后。删早了，还没切的 agent 就没有别名表可查，也没法回滚到 v1。

## 5. 验证

- agent 服务 active（`systemctl --user is-active buzz-local-<name>.service`），启动日志时间晚于 prompt、配置和沙箱设置的改动时间。
- 沙箱内，在 [agent-sandbox.md](agent-sandbox.md)「离线探针」的做法上再补两条：`wc -c < <people_file>` 输出非零字节数；helper 的 `load_config` 能加载 `BUZZ_RESPONSIBLE_CONFIG`（沙箱里用一行 `importlib` 加载 `<release>/scripts/buzz_send_with_responsible_mentions.py` 再调 `load_config`）。这两条探针写进 agent-sandbox.md 的事由见 skills#141；在那之前照这两条手工跑。
- 同步 runner 手动一轮 `status` 为 `ok`；飞书镜像报告 `errors` 为 0；插件副本用 `resolve_plugin_install.py` 读出的 `git_commit_sha` 等于新 SHA。
- 已部署 prompt 是否漂移、整套本机是否对齐，目前靠手工对照；检查脚本见 skills#137（prompt）、skills#140（全量审计）。

## 6. 回滚

- **release 目录不删、不覆盖**：新版本装进新目录，旧目录原样保留，回滚就是把路径改回去。
- 所有要改的文件先备份成 `*.bak.<时间>`：manifest、unit、prompt、责任人配置、沙箱 settings。
- 同步 runner、飞书镜像、todo 同步：按 [systemd/README.md](systemd/README.md)「5. 停用与回滚」，先停 timer，还原 `*.bak.*`（或把路径改回旧 release），`daemon-reload`，手动 `start` 一轮，再启用 timer。
- 责任人 helper 回到 v1 同样是破坏性的：这个 agent 的 prompt、配置、沙箱设置三样一起还原，立即重启。cursor、outbox、binding 都不受影响。Canvas 别名表还在，才回得去。
- v1 → v2 的配置迁移工具见 skills#138；在它合入之前，迁移脚本按「先 `--dry-run`、写前备份、写后用新 helper 的 `load_config` 回读」自己写。
