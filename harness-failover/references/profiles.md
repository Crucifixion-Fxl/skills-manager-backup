# Profiles 与多账号

一个 profile = **adapter 命令 + wrapper + 账号 home + model + effort**。默认值在 [../assets/profiles.default.json](../assets/profiles.default.json)，
用户覆盖在 `~/.config/buzz/harness-profiles.json`（同结构，按 `id` 合并；不含密钥）。

```json
{"profiles": [
  {"id": "claude-work", "harness": "claude", "model": "sonnet", "effort": "medium", "priority": 25,
   "wrapper": "~/.local/bin/claude-work", "home": "~/.claude-work"},
  {"id": "glm", "enabled": false},
  {"id": "claude-buzz", "effort": "high"}
]}
```

| 字段 | 说明 |
|:--|:--|
| `id` | 唯一；重复会被拒绝 |
| `harness` | `grok` / `claude` / `codex`（决定 adapter 命令） |
| `model` / `effort` | 写进 `BUZZ_ACP_MODEL` / `BUZZ_ACP_EFFORT_LEVEL`；effort ∈ default low medium high xhigh max ultra |
| `wrapper` | claude 系：写进 `HARNESS_CLAUDE_WRAPPER`，启动脚本据此设 `CLAUDE_CODE_EXECUTABLE` |
| `home` | 账号目录：claude 系 = `CLAUDE_CONFIG_DIR`（wrapper 内设置）；codex = `CODEX_HOME`（写进 env） |
| `provider` | 例如 `glm`，仅用于说明 |
| `priority` | 越小越优先；故障切换按此顺序找第一个可用的 |
| `enabled` | `false` 从候选中移除 |

## 加一个账号

1. **claude**：写 wrapper（只设目录，不放密钥）——`exec env CLAUDE_CONFIG_DIR="$HOME/.claude-work" "$HOME/.local/bin/claude" "$@"`，`chmod 700`；
   然后**人工登录一次**：`! CLAUDE_CONFIG_DIR=~/.claude-work claude /login`。
2. **codex**：`! CODEX_HOME=~/.codex-work codex login`（交互式，只能本人做）。
3. 在 `harness-profiles.json` 加一项，`detect --probe` 确认能出 token，再依赖它。

同一账号不要用两个 profile 冒充两个备选：切换会**跳过与已耗尽 profile 同账号**的候选（claude 用 `.claude.json` 里 `oauthAccount.accountUuid` 判断）。
注意「配置目录名 ≠ 账号」：2026-09-17 `.claude-buzz` 吃到的是 GLM 的 1308，因为当时它用的是 GLM key。

## adapter 取值（ACP 握手实测，2026-09-19）

| adapter | `model` 可选值 | effort 配置项（category `thought_level`） |
|:--|:--|:--|
| claude-agent-acp 0.70.0 | default, opus[1m], claude-fable-5-1[1m], sonnet, haiku | `effort`: default low medium high xhigh max |
| codex-acp 1.11.0 | gpt-5.6-sol, gpt-6-astra, gpt-5.6-terra, gpt-5.6-luna, gpt-5.5 | `reasoning_effort`: low medium high xhigh max ultra |

claude-agent-acp **没有** `session/set_model`，model 与 effort 都走 `session/set_config_option`。buzz-acp 用适配器声明的 `thought_level` 类别找 effort 的 configId，所以 `BUZZ_ACP_EFFORT_LEVEL` 对两者通用。
新增 harness 时先用 ACP 握手（`initialize` → `session/new` → 读 `configOptions`）核实这两组取值，再加 profile。
