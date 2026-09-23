# Git Flow 回溯 — API 主动巡检 已经直接合到 master 了怎么办

## 你面对的情况

有人（也许是你、也许是过去的你）在 `feat/` 分支上做完 API 主动巡检，开了 MR 直接合到 `master`，跳过了项目的标准 `feat → intercept → staging → master` 路径。MR 已经 merge 了。

现在 master 有 API 主动巡检。intercept 和 staging 没有。以后任何 `intercept → staging` MR（或其他分支同步 MR）都会出现混乱的 diff，把 smoke 相关文件显示为"被删除"（因为它们是通过另一条路径到 master 的，而 staging 的基础落后）。

## 症状

开 `intercept → staging` MR（或任何本该包含 smoke 的分支同步 MR），看到：

```
smoke/checks/test_health.py       | 24 ------------------------
smoke/envs.py                     | 19 ------------------------
.gitlab-ci.yml                    | 50 -------
```

你没删任何东西。git 只是在告诉你 intercept 缺少那些（通过另一条路径）下游存在的文件。

## 修复：用 merge 传播，不要 revert

**别 revert master**。master 没问题。问题是 intercept/staging 需要通过正规路径拿到 API 主动巡检 代码。

策略：**开新 MR，把 master 的 API 主动巡检 内容合进 intercept**。

### Step 1: 选一个有 API 主动巡检 内容的分支

可选：
- 原始的 `feat/api-monitor` 分支（如果还在）
- 从 master 开的新分支
- 基于 intercept 的分支 + cherry-pick smoke 相关 commit

### Step 2: 先把 intercept merge 进你的分支

反直觉但正确。把 intercept 的差异化工作先拉进你的分支，这样后续 MR 就干净了。

```bash
git checkout feat/api-monitor      # 或者你选的分支
git fetch origin feature/kb_edge_feature_intercept
git merge origin/feature/kb_edge_feature_intercept --no-commit --no-ff
```

手动解决冲突 — 典型冲突点：
- `.gitignore`（两边合并取并集）
- `.gitlab-ci.yml`（保留 intercept 的完整 CI + 加你的 smoke job）
- `Makefile`（保留 intercept 的完整 Makefile + 加 smoke 目标）
- `docs/**/overview.md` 的导航行（合并链接列表）

### Step 3: 在合并后的分支上验 CI

```bash
git push
# 等 MR 自动 pipeline 或手动触发，确认 smoke 通过
```

### Step 4: 开 MR 到 intercept

```bash
glab mr create --source-branch feat/api-monitor \
               --target-branch feature/kb_edge_feature_intercept \
               --title "feat(smoke): 回补 API 主动巡检 到 intercept"
```

CI 应该绿（Step 3 已验）。合。

### Step 5: 开 MR intercept → staging

现在 intercept 既有自己的工作也有 API 主动巡检。开：

```bash
glab mr create --source-branch feature/kb_edge_feature_intercept \
               --target-branch staging
```

这个 MR 应该无冲突、无"删除"行。intercept 的 delta 直接合并（或快进）到 staging，smoke 跟着过去。

### Step 6: staging → master 先不做

按用户偏好，把变更攒在 staging 上。等 prod ready 再开 staging → master 一次性同步。

## 下次预防

搞任何新基础设施变更 — smoke、CI 工具、可观测性 — 先看项目 git flow：

```bash
# master 有 .gitlab-ci.yml 和 Makefile 吗？
curl -s -o /dev/null -w "%{http_code}\n" -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/repository/files/.gitlab-ci.yml?ref=master"

# intercept/staging 有吗？
for ref in "feature/kb_edge_feature_intercept" "staging"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "PRIVATE-TOKEN: $TOKEN" \
    "https://gitlab.addx.ai/api/v4/projects/<id>/repository/files/.gitlab-ci.yml?ref=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$ref'))")")
  echo "$ref: $code"
done
```

如果 master 缺而 intercept/staging 有，说明 master 在 git flow 里落后。别直接往 master 加文件 — 要走正常路径进来。

## 经验原则

**基础设施代码跟产品代码走同一套 git flow**。"就是个 smoke test"不代表可以跳审查 — 恰恰相反，基础设施正是那种跳步会给队友留地雷的东西。
