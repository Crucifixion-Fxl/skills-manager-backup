# 分步上手指南

按顺序走。每步都有验证动作，验不过别往下做。

## Step 1: 初始化 Python 项目

```bash
mkdir -p smoke/checks smoke/lib
cp /path/to/skill/assets/pyproject.toml.template smoke/pyproject.toml
cp /path/to/skill/assets/envs.py.template smoke/envs.py
cp /path/to/skill/assets/config.py.template smoke/config.py
cp /path/to/skill/assets/conftest.py.template smoke/conftest.py
cp /path/to/skill/assets/feishu.py.template smoke/lib/feishu.py
cp /path/to/skill/assets/test_health.py.template smoke/checks/test_health.py
touch smoke/__init__.py smoke/checks/__init__.py smoke/lib/__init__.py

# .gitignore
cat > smoke/.gitignore <<EOF
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
EOF
```

**替换占位符**：

- 每个含 `<service>` / `<company>` / `<your-core-endpoint>` 的文件都要填
- `pyproject.toml`：项目名
- `envs.py`：真实 URL
- `config.py`：本地 port discovery 的端口范围 + 探测端点

## Step 2: 验证本地安装

```bash
cd smoke
pip install -e .
```

**验证**：`python -c "from config import load_config; print('ok')"` 打印 `ok` 无报错。

常见失败：`Multiple top-level packages discovered in a flat-layout`。修复：确认 `pyproject.toml` 有 `[tool.setuptools.packages.find]` 且 `include = [...]` 列全你的 package。

## Step 3: 跑一次 staging 测试

```bash
python -m pytest checks/ --env=staging-us -v
```

**验证**：测试能跑。服务已部署 → pass；未部署 → `SKIP`。

常见失败：`SMOKE_ADMIN_URL_*` env var 找不到。修复：确认 `config.py` 的 `_load_ci_config` 是从 `envs.ENVIRONMENTS` 读，不是从 env var 读。

## Step 4: 改项目 `.gitlab-ci.yml`

把 `assets/gitlab-ci-monitor.yml.template` 的 smoke job 追加到项目 `.gitlab-ci.yml`。追加前先确认：

1. `stages` 列表包含 `test`（见 references/ci-templates-contract.md）
2. `workflow.rules` 允许 schedule / web / api pipeline source
3. 有 `PYTHON_IMAGE` 变量

缺哪个补哪个（别破坏现有规则）。

**验证**：

```bash
glab api -X POST "projects/<id>/ci/lint" \
  --data-urlencode "content=@.gitlab-ci.yml"
```

返回 `"valid": true`。

## Step 5: 配 CI 变量

GitLab → Settings → CI/CD → Variables → Add：

```
FEISHU_WEBHOOK_URL       [protected, masked]
FEISHU_WEBHOOK_SECRET    [protected, masked]
SMOKE_RP_ENDPOINT        [protected]
SMOKE_RP_PROJECT         [protected, 可选 masked]
SMOKE_RP_TOKEN           [protected, masked]
```

**用 API 验证**：

```bash
curl -s -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/variables" \
  | jq '.[] | select(.key | startswith("FEISHU_") or startswith("SMOKE_RP")) | {key, protected, masked}'
```

应该全部 `protected: true`。

## Step 6: Push 并验证 MR pipeline

```bash
git checkout -b feat/api-monitor
git add smoke/ .gitlab-ci.yml
git commit -m "feat(monitor): add API synthetic monitoring"
git push -u origin feat/api-monitor
glab mr create --target-branch <per-git-flow>  # 如果项目用 git flow，不要打到 master
```

**验证**：MR pipeline 过。重点看 `smoke:patrol` job。

如果 pipeline 显示 `0 jobs + failed + no yaml_errors` → 见 references/ci-templates-contract.md。

## Step 7: 临时 protect 分支（为了验证飞书）

如果 MR 目标不是 protected 分支（feature 分支、intercept 分支），需要临时 protect 源分支：

```bash
curl -s -X POST -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/protected_branches?name=feat%2Fapi-monitor&push_access_level=40&merge_access_level=40&allow_force_push=true"
```

否则 `FEISHU_WEBHOOK_URL` 不会注入，你会以为通知坏了。

## Step 8: 验证红路径（故意失败）

临时改 `smoke/envs.py` 打坏一个 URL：

```python
"admin_url": "https://<hostname>-DOES-NOT-EXIST.addx.live",
```

Push，触发流水线：

```bash
curl -s -X POST -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/pipeline?ref=feat%2Fapi-monitor"
```

**验证**：

1. Pipeline 在 `smoke:patrol` 失败
2. Job 日志里有 `[smoke notify] webhook_url_set=True` 和 `[smoke notify] Feishu card sent`
3. 飞书群收到红色卡片

如果第 2 步日志是 `webhook_url_set=False` → 分支保护没生效。

改回真实 URL，再 push，确认绿。

## Step 9: 建定时流水线

```bash
curl -s -X POST -H "PRIVATE-TOKEN: $TOKEN" \
  --data-urlencode "description=<service> Smoke Patrol" \
  --data-urlencode "ref=master" \
  --data-urlencode "cron=*/5 * * * *" \
  --data-urlencode "cron_timezone=UTC" \
  --data-urlencode "active=true" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/pipeline_schedules"
```

**验证**：等 5 分钟，master 上出现一条 `source=schedule` 的流水线，绿。

## Step 10: 按 Git Flow 合入

按项目约定（通常 `feat → intercept → staging → master`）依次开 MR。别走捷径直接合 master — 见 SKILL.md 的 Git Flow 警告。

## Step 11: 收尾

合并到 master 后：

```bash
# 取消 feat 分支 protect
curl -s -X DELETE -H "PRIVATE-TOKEN: $TOKEN" \
  "https://gitlab.addx.ai/api/v4/projects/<id>/protected_branches/feat%2Fapi-monitor"
```

如果开发期间 schedule 指向的是 feat 分支，把它改到 `master`。
