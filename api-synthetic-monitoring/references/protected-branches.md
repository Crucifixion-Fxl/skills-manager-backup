# Protected 分支和 CI 变量注入

## 机制

GitLab CI 变量有两个相关标记：

- **Masked**：job 日志里把值遮掉。跟注入与否无关。
- **Protected**：变量**只在 protected 分支 / tag 的 pipeline 注入**。

AddX 里凭据通常两个都打开。这是安全策略 — 未 review 的 feature 分支不该拿到生产 secret。

## 哪些分支是 protected？

AddX 项目默认：

- `master` — 永远 protected
- `staging` — 永远 protected
- 其他 — 不 protected

查询：

```bash
curl -s -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/protected_branches" \
  | python3 -c "import sys,json; [print(b['name']) for b in json.load(sys.stdin)]"
```

## 为什么会咬 API 主动巡检

开发 API 主动巡检 时你在 `feat/api-monitor` 上 — 非 protected。跑 pipeline：

- `FEISHU_WEBHOOK_URL`、`FEISHU_WEBHOOK_SECRET` → 不注入
- `SMOKE_RP_TOKEN` → 不注入

你的 `pytest_sessionfinish` hook 看到 `os.environ.get("FEISHU_WEBHOOK_URL") == None`，静默 return。你以为通知坏了，其实代码没问题 — 变量根本不在。

模板里的 debug 日志（`[smoke notify] webhook_url_set=False`）就是专门用来让你能诊断这个的。

## 开发期间的变通

临时 protect feature 分支：

```bash
curl -s -X POST -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/protected_branches?name=feat%2Fapi-monitor&push_access_level=40&merge_access_level=40&allow_force_push=true"
```

- `push_access_level=40` = Maintainer
- `merge_access_level=40` = Maintainer
- `allow_force_push=true` = 开发期允许 rebase

验证完（或 MR 合并后）取消 protect：

```bash
curl -s -X DELETE -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/protected_branches/feat%2Fapi-monitor"
```

## 替代方案：别用 protected 变量

不推荐。如果把 `FEISHU_WEBHOOK_URL` 设为非 protected：

- 任何 feature 分支都能发飞书到监控群（OK，风险低）
- 但涉及影响面大的 token（比如生产 DB 密码 — 如果你用的话），非 protected 意味着 feature 分支贡献者可以通过 echo 外泄。

保持 protected + 开发期短期 protect 分支。

## 在 CI 日志里确认

在 protected 分支跑完 smoke job，日志应该有：

```
[smoke notify] webhook_url_set=True webhook_secret_set=True
[smoke notify] sending Feishu card: env=staging-us failures=1
[smoke notify] Feishu card sent
```

如果在你认为已 protect 的分支上看到 `webhook_url_set=False`，重新检查：
- 分支名真的在 protected 列表里（拼写、大小写）
- 变量真的 `protected=true`
- Pipeline ref 匹配分支（看 API 响应里的 `ref` — MR pipeline 是 `refs/merge-requests/N/head`，仍看源分支的 protect 状态）
