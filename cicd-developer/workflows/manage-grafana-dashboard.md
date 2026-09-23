---
name: manage-grafana-dashboard
description: 在隔离的（isolated checkout）grafana-dashboards-as-code checkout 中创建、更新、校验、计划或明确删除 Dashboard 源码；本地不访问 Grafana。
---

# Workflow：manage-grafana-dashboard

## 目的

管理 Grafana Dashboard **源码**，而不是 Grafana 实例。唯一目标仓库是
`DEV/grafana-dashboards-as-code`；其 `config/` 和 `scripts/` 是 Dashboard
policy、数据源选择和部署行为的唯一事实来源。

本 workflow 支持：`create`、`update-source`、`validate`、`diff/plan`、以及
用户明确要求的 `remove-source`。它不生成 K8s manifest、不 chain
`interview-cd-requirements.md`、不修改调用方仓库，也不读取或使用任何 Grafana
凭据。

开始前读：

- `references/grafana/repository-contract.md`
- `references/grafana/workspace-isolation.md`
- `references/grafana/security-and-deletion-policy.md`

## Step 1. 解析 Dashboard 请求和所有权

[precondition]
  - 用户请求明确指向 Grafana Dashboard、PromQL Dashboard 或 Dashboard-as-Code

[action]
  - 将操作归为一个：
    - `create`：创建新 Dashboard JSON；
    - `update-source`：改受管 Dashboard 的查询或增加 JVM 堆内存 Panel；
    - `validate`：检查现有 Dashboard 源码；
    - `diff/plan`：生成与 `origin/main` 的 tokenless 变更计划；
    - `remove-source`：仅当用户明确说删除 Dashboard 源码时使用。
  - 收集非敏感输入：`team`、`environment`、`cluster`、
    `victoria-metrics-ref`、`namespace`、`service` 或 `dashboard path`。
    `cluster` 只用于受控的数据源映射和 Dashboard ownership tag；绝不把它当作
    PromQL 必填标签、绝不自动写进开发者的查询。
  - 服务名优先级：开发者明确的自定义名称 → 调用方仓库中的 Rollout 名称 →
    Deployment 名称。多个候选时要求 `--workload-name`，绝不猜测。
  - 不接受 Grafana URL、Folder UID、data-source UID 或任何凭据作为输入。

[validate]
  - `environment + cluster + victoria-metrics-ref` 必须由目标仓库目录映射为唯一 UID；
    映射不存在或不唯一时 STOP，要求先审查目标仓库 `config/`。
  - `remove-source` 没有明确删除意图时 STOP；不得把普通“更新”理解成删除。

[output]
  - `$operation`、`$team`、`$environment`、`$cluster`、
    `$victoria_metrics_ref`、`$namespace`、`$service` / `$dashboard_path`

## Step 2. 建立隔离 Dashboard 工作区

[precondition]
  - Step 1 输入完整；调用方目录记为 `$caller_root`

[action]
  - 生成 feature branch：`grafana/<team>-<service>-<environment>`；更新使用
    `grafana/update-<uid>`，删除使用 `grafana/remove-<uid>`。
  - 只通过下面命令创建临时 checkout；不要在 `$caller_root` 下 clone，不要添加
    submodule、subtree 或 remote：

    ```bash
    python3 "$skill_root/scripts/grafana_workspace.py" prepare \
      --caller-root "$caller_root" \
      --branch "$grafana_branch"
    ```

  - 从 JSON 输出保存 `$dashboard_workspace`。之后所有 Dashboard Git 和 Python
    命令都必须显式以该目录为 root。

[validate]
  - 运行：

    ```bash
    python3 "$skill_root/validators/check_grafana_workspace.py" \
      --caller-root "$caller_root" --workspace "$dashboard_workspace"
    ```

  - 检查 `$caller_root` 未产生 `.gitmodules`、Dashboard 子目录、Git diff、staged
    文件或 remote 变化。失败即 STOP。

[output]
  - `$dashboard_workspace`、`$grafana_branch`；其 origin 只允许目标 Dashboard 仓库

## Step 3. 创建、修改或移除纯 JSON 源码

[precondition]
  - Step 2 的隔离校验通过

[action]
  - `create`：运行：

    ```bash
    python3 "$skill_root/scripts/grafana_create_dashboard.py" \
      --repo-root "$dashboard_workspace" \
      --application-root "$caller_root" \
      --team "$team" --environment "$environment" --cluster "$cluster" \
      --victoria-metrics-ref "$victoria_metrics_ref" \
      --namespace "$namespace" [--service "$service"] [--workload-name "$workload_name"] \
      --panel-specs-json '[{"title":"<panel title>","expr":"<developer PromQL>","unit":"short"}]'
    ```

    开发者提供 PromQL 时，必须以 `--panel-specs-json` 原样传递：不得补
    `cluster`、`namespace`、`service` 等 label，不得包 `sum` / `rate`，不得改写
    聚合或空白。仅当开发者没有提供任何查询且明确要求“标准模板”时，才可省略该参数
    使用基础模板；基础模板同样不含 `cluster` selector。Panel 的 data source 必须由
    目标仓库目录自动解析；不要传手工 UID 覆盖它。
  - `update-source`：先确认目标 JSON 含 `managed-by-grafana-skill`，再使用：

    ```bash
    python3 "$skill_root/scripts/grafana_update_dashboard.py" "$dashboard_path" \
      --repo-root "$dashboard_workspace" \
      --panel-id <stable-panel-id> --promql '<bounded-promql>'
    ```

    `--promql` 必须保持开发者原文。增加 JVM 堆内存 Panel 时改用
    `--add-jvm-heap --jvm-promql '<developer-promql>'`；没有原始查询就 STOP 询问，
    不得从现有 Panel 推断、添加 `cluster` 或构造新 selector。显式 per-Pod 需求应
    使用 `sum by (pod)` 或 `sum by (source_pod)`，不添加 Dashboard 变量。
  - `remove-source`：再次向用户确认文件路径后，才运行：

    ```bash
    python3 "$skill_root/scripts/grafana_remove_source.py" "$dashboard_path" \
      --repo-root "$dashboard_workspace" --confirm-source-removal
    ```

    这一步只删除 Git 源码；绝不调用 Grafana API。

[validate]
  - 生成或更新必须输出 Dashboard 相对路径、解析的数据源 UID、建议 branch 和
    commit message。
  - 基础查询校验只检查非空、受控数据源、无凭据/URL，以及至少一个非空、非全通配
    的 label matcher；不假设固定标签名。查询不合规时 STOP 并保留原文，不得用
    `cluster` 或其他自动 selector 修补。该本地校验不证明指标在 VictoriaMetrics 中
    有数据或业务语义正确。
  - 更新或删除不带 `managed-by-grafana-skill` 的 Dashboard 时 STOP。
  - 新建、更新、删除都不能触及 `$caller_root`。

[output]
  - `$changed_dashboard_paths[]`、`$dashboard_uid`、`$expected_dashboard_uri`、
    `$summary_path`、建议的 ASCII commit message。URI 只能由受控 helper 依据稳定 UID
    和批准的静态模板生成，不能由用户提供或手工拼接。

## Step 4. 用目标仓库的 SSOT 校验并生成计划

[precondition]
  - Step 3 产生的变更已在 `$dashboard_workspace`

[action]
  - 从隔离 checkout 运行：

    ```bash
    python3 "$skill_root/scripts/grafana_validate_dashboard.py" \
      --repo-root "$dashboard_workspace" --config
    python3 "$skill_root/scripts/grafana_validate_dashboard.py" \
      --repo-root "$dashboard_workspace" --file "$changed_dashboard_path"
    (
      cd "$dashboard_workspace"
      python3 -m scripts.validate_all --repo-root .
      python3 -m unittest discover -s tests -v
      python3 -m scripts.generate_deploy_plan --repo-root . --base origin/main
      git diff --check origin/main...HEAD
    )
    ```

  - 读取 `reports/deploy-plan.json` 和 `reports/deploy-plan.md`。删除请求必须显示
    `removedDashboards` 和 `deleteAfterMerge: true`。

[validate]
  - 任何目标仓库校验、单元测试、Diff 检查或计划生成失败即 STOP；不得降级、绕过
    或另写一套本地 Dashboard policy。
  - 计划中 data source UID、cluster 和 VictoriaMetrics ref 必须与 Step 1 一致。
  - `remove-source` 的计划缺少安全删除条目时 STOP。

[output]
  - 校验结果、Dashboard 路径、变更摘要、tokenless 部署计划路径

## Step 5. 自动提交、推送并创建运维审核 MR

[precondition]
  - Step 4 全部通过
  - `$operation` 是 `create`、`update-source` 或 `remove-source`；`validate` 与
    `diff/plan` 不产生 Git 提交或 MR

[action]
  - 对每个成功的 Dashboard 源码变更，使用受控 helper 一次性只提交 Step 3 的明确
    JSON 路径、推送受限 feature branch，并向固定目标仓库创建 MR：

    ```bash
    python3 "$skill_root/scripts/grafana_workspace.py" submit \
      --caller-root "$caller_root" --workspace "$dashboard_workspace" \
      --file "$changed_dashboard_path" --message "$ascii_commit_message" \
      --title "$mr_title" --description "$mr_description"
    ```

  - 从 `submit` 的 JSON 输出保存精确的 `$mr_url`；保留 Step 3 source helper 已输出的
    `$expected_dashboard_uri`。将它们原样返回给用户，并明确按以下顺序提示：
    **“请把此 MR 地址发送给运维审核；不要自行合并、不要绕过 CODEOWNERS 或受保护
    主分支。”**
    **“如该 MR 合并且目标仓库的 deploy、verify Pipeline 成功，Grafana Dashboard
    预期访问地址为：`$expected_dashboard_uri`。”**
  - `remove-source` 不承诺 Dashboard 可访问；改为明确提示：**“如该 MR 合并且删除
    验证成功，原 Dashboard URI `$expected_dashboard_uri` 预期返回 404/Not found。”**
  - 这是目标 `DEV/grafana-dashboards-as-code` 仓库的 MR；绝不提交、推送或修改
    `$caller_root`，也绝不把 Dashboard 仓库作为 caller 的 submodule、subtree 或目录提交。

[validate]
  - helper 必须拒绝 `main`、force push、非目标 origin、未隔离 workspace、
    `git add .` 产生的意外路径和非 `dashboards/**/*.json` 提交。
  - 输出中的 `mergeRequest` 必须是 GitLab MR URL；helper 成功但没有返回 URL 时也
    必须失败，不能把自由文本当作结果。
  - `$expected_dashboard_uri` 必须来自 `grafana_dashboard_uri.py` 的批准 URI 模板和
    当前 Dashboard UID；不得读取 `GRAFANA_URL`、调用 Grafana、接受用户提供的 URL
    或在链接中携带认证信息。
  - `glab` 未认证或没有创建 MR 权限时 STOP，并报告安全的本机登录
    `glab auth login --hostname gitlab.addx.ai` 或 GitLab UI 操作；不要求用户在
    对话中提供 Token。

[output]
  - 目标仓库 branch、commit SHA、`$mr_url` 和 `$expected_dashboard_uri`；`validate` /
    `diff/plan` 则明确输出“没有 Dashboard 源码变更，未提交、未推送、未创建 MR”

## Step 6. 说明 Merge 后的受保护 CI 行为

[precondition]
  - Step 5 创建了 MR，或用户执行的是无变更的 `validate` / `diff/plan` 并询问部署预期

[action]
  - 说明 MR pipeline 只运行 lint、test、plan，且没有生产 Grafana Token。
  - 说明受保护主分支合并后由目标仓库 CI 执行部署和远程验证；新增/修改执行
    create/update/skip，明确删除的源码才可能触发受管远端删除。

[validate]
  - 不把 MR 通过、HTTP 200 或本地生成等同于 Grafana 部署成功。
  - 远程部署和删除仍由目标仓库 CI 验证 folder、UID、managed tag、Panel 数量和
    规范化内容。

[output]
  - 下一阶段的 GitLab pipeline 预期、交给运维审核的 MR URL、合并并验证成功后的
    预期 Dashboard URI，以及本地 Skill 未访问 Grafana 的确认
