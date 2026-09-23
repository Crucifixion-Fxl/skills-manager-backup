---
name: claude-code-permissions
description: 配置 Claude Code 权限模式——内置 permission-mode（auto/plan/bypassPermissions 等）或 allowlist 选择性自动允许。当用户说"配置权限"、"减少确认"、"auto allow"、"太多确认了"、"skip permissions"、"自动模式"、"permission mode"时触发。
---

# Claude Code 权限配置

## Description

Claude Code 提供多种权限模式和自定义 allowlist，本 Skill 帮助用户选择和配置最适合的方案。

## Rules

### Rule 0 — 内置 permission-mode

Claude Code 通过 `--permission-mode` 参数支持以下模式：

| 模式 | 启动方式 | 效果 |
|------|---------|------|
| `default` | `claude` | 每个工具调用都需确认 |
| `acceptEdits` | `claude --permission-mode acceptEdits` | 自动接受文件编辑（Read/Edit/Write），其他仍确认 |
| `plan` | `claude --permission-mode plan` | 规划模式，只读不写，用于分析和制定方案 |
| `auto` | `claude --permission-mode auto` | 自动模式，自动接受大部分安全操作 |
| `dontAsk` | `claude --permission-mode dontAsk` | 不再询问，自动处理所有操作 |
| `bypassPermissions` | `claude --permission-mode bypassPermissions` | 跳过所有权限检查（等同 `--dangerously-skip-permissions`） |

还有其他相关参数：
- `--dangerously-skip-permissions` — 跳过所有权限检查的快捷 flag
- `--allowedTools <tools...>` — 指定允许的工具列表（如 `"Bash(git:*) Edit"`）
- `--allow-dangerously-skip-permissions` — 允许在会话中切换到 bypass 模式，但不默认开启

### Rule 1 — 询问用户选择

```
你想怎么配置？

1. auto 模式 — 内置自动模式，大部分安全操作自动通过（推荐）
2. Allowlist 模式 — 在 settings.json 精确控制哪些命令自动允许
3. 两个都配 — alias 快速切换不同模式（推荐有多种场景的用户）
```

### Rule 2 — 推荐配置：Shell Alias 快速切换

在 `~/.zshrc`（或 `~/.bashrc`）中添加：

```bash
# Claude Code 模式 alias
alias claude-auto='claude --permission-mode auto'           # 自动模式
alias claude-plan='claude --permission-mode plan'           # 规划模式（只读）
alias claude-yolo='claude --dangerously-skip-permissions'   # 完全跳过确认（慎用）
```

使用方式：
- `claude` — 默认模式（带 allowlist 配置）
- `claude-auto` — 自动模式，安全操作自动通过
- `claude-plan` — 只分析不修改
- `claude-yolo` — 完全无确认（CI/CD、沙箱环境）

### Rule 3 — Allowlist 精确配置（可选，与 permission-mode 互补）

在 `~/.claude/settings.json` 中添加 `permissions.allow`，对默认模式下的特定命令自动放行：

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Edit",
      "Write",
      "Glob",
      "Grep",
      "Bash(git status*)",
      "Bash(git log*)",
      "Bash(git diff*)",
      "Bash(git branch*)",
      "Bash(git remote*)",
      "Bash(git show*)",
      "Bash(git rev-parse*)",
      "Bash(git ls-files*)",
      "Bash(git config*)",
      "Bash(ls*)",
      "Bash(pwd)",
      "Bash(which*)",
      "Bash(echo *)",
      "Bash(cat *)",
      "Bash(head *)",
      "Bash(tail *)",
      "Bash(wc *)",
      "Bash(du *)",
      "Bash(df *)",
      "Bash(find *)",
      "Bash(sort *)",
      "Bash(env*)",
      "Bash(printenv*)",
      "Bash(uname*)",
      "Bash(whoami*)",
      "Bash(date*)",
      "Bash(ccusage*)",
      "Bash(brew *)",
      "Bash(flutter pub get*)",
      "Bash(flutter analyze*)",
      "Bash(flutter test*)",
      "Bash(flutter clean*)",
      "Bash(pod install*)",
      "Bash(pod update*)",
      "Bash(npm install*)",
      "Bash(npm test*)",
      "Bash(npm run *)",
      "Bash(npx *)",
      "Bash(node *)",
      "Bash(python3 *)",
      "Bash(xcodebuild *)",
      "Bash(swiftlint*)",
      "Bash(dart *)",
      "Bash(cd *)",
      "Bash(mkdir *)",
      "Bash(touch *)",
      "Bash(cp *)",
      "Bash(mv *)",
      "Bash(basename*)",
      "Bash(dirname*)",
      "Bash(realpath*)",
      "Bash(sed *)",
      "Bash(awk *)",
      "Bash(grep *)",
      "Bash(jq *)",
      "Bash(curl -s*)",
      "Bash(gh *)",
      "Bash(g++ *)",
      "Bash(gcc *)",
      "Bash(make*)",
      "Bash(cmake *)",
      "Bash(./gradlew *)",
      "Bash(gradle *)",
      "Bash(bazel *)"
    ]
  }
}
```

### Rule 4 — 执行配置

根据用户选择执行：

1. **读取** `~/.claude/settings.json` 和 `~/.zshrc`，确认当前状态
2. **写入** settings.json 的 allowlist（保留已有配置不变）
3. **追加** alias 到 `~/.zshrc`（检查是否已存在，避免重复）
4. **验证**：

```bash
# 验证 allowlist
cat ~/.claude/settings.json | jq '.permissions.allow | length'

# 验证 alias
grep 'claude-auto\|claude-plan\|claude-yolo' ~/.zshrc

# 提示生效
echo "运行 source ~/.zshrc 或重启终端后 alias 生效"
echo "settings.json 的 allowlist 重启 claude 会话后生效"
```

### Rule 5 — 追加规则

用户可以随时：
- 追加 allowlist 规则到 `permissions.allow` 数组
- 添加新的 alias 组合
- 用 `--allowedTools` 临时指定单次会话的允许工具

## Examples

### Good

```
用户: "太多确认了"
AI: 展示 permission-mode 选项 → 用户选 auto → 配置 alias + allowlist → 验证
```

```
用户: "我想要全自动模式"
AI: 配置 claude-yolo alias → 提示 source ~/.zshrc → 用 claude-yolo 启动
```

```
用户: "日常开发用什么模式好"
AI: 推荐 auto 模式 + allowlist 组合 → 配置两者 → 日常用 claude-auto
```

### Bad

```
用户: "配置自动模式"
AI: 直接把 --dangerously-skip-permissions 设为默认启动参数
→ 应该用 alias 区分场景，不应让危险模式成为默认
```
