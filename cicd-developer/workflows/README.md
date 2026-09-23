# workflows/

每个 workflow 都是一个可执行的、单一意图的步骤合同。路由清单的唯一事实来源是
[`references/data/routes-build.yaml`](../references/data/routes-build.yaml)；不要在本文件维护第二张清单或靠文件名猜流程。

## 执行合同

1. 先按 `SKILL.md` 选择 Build 模式，再从路由表选中全部显式请求的能力。
2. 完整读取每个选中的 workflow。新服务先跑主 workflow，附加能力按路由表顺序追加；
   不能把两个 workflow 中途混写，也不能把未知意图塞进“最近”的 workflow。
3. 每个 step 的 `[precondition]`、`[action]`、`[validate]`、`[output]` 都是门禁。
   前置条件不成立、信息缺失、校验非零或渲染失败时，立刻 STOP 并保留原始错误。
4. 写 YAML 时只使用该 workflow 明确引用的 recipe 与已声明的变体；填 slot，不重画模板，
   也不新增没有 recipe 的资源 kind。
5. 所有环境、集群、Vault、成本与权限事实都从 workflow 指定的 YAML/reference 读取，
   不能从别处的例子、旧 README 或记忆推导。

模式专用的分步执行补充放在 `workflows/supplements/`，不放在仅保存稳定事实与 resolver 的
`references/`。补充文件不是独立路由；顶层已路由 workflow 必须逐步显式链接它，并说明主
workflow 与补充合同同时适用、冲突时从严。

## 输出与边界

- Build 只创建 workflow 明确列出的文件和 Ops Todo；是否需要改应用仓、`crossplane-infra`、
  `argocd-apps`、CI 或 Dockerfile 由 workflow 决定。
- Grafana Dashboard workflow 只在隔离的 Dashboard-as-Code checkout 写受管 JSON、创建 GitLab
  MR 并返回预期 URI；它不能改调用方仓库或直接访问 Grafana。
- VictoriaMetrics scrape workflow 只写调用方应用仓 overlay，不能投递 Dashboard 源码。
- 对需要外部协调或尚未实现的能力，Ops Todo 要写清资源、原因、所需输入和验收标准。
- Workflow 只生成 GitOps 变更；不把 `kubectl apply`、`helm install` 或直接修改线上资源写成部署步骤。
- 触及 prod 前，必须执行 `references/cost-tiering/_global.yaml -> prod_self_check`。
- 结构性自检运行
  `python3 "$skill_root/validators/check_routes.py" "$skill_root"`；渲染 manifest 的校验运行
  `bash "$skill_root/validators/validate.sh" <output-dir>`。两者不能互相替代。

## 维护规则

新增或删除 workflow 时，必须同一变更更新 `routes-build.yaml`，并运行
`check_routes.py`。planned route 必须保留明确的 `planned_stop`，而不是让模型临场生成 YAML。
