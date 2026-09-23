# troubleshooting/

这里存放 A4x 部署栈已知失败模式的**只读诊断** playbook：ArgoCD、Crossplane、ESO、
镜像、GitLab Runner、Sentry、日志管道等，也包括只读部署历史和回滚候选查询。

路由的唯一事实来源是
[`references/data/routes-troubleshoot.yaml`](../references/data/routes-troubleshoot.yaml)。
按症状选择 playbook；多个候选同样合理时先澄清，未匹配时先收集证据，不能从相似 playbook
临场编造修复步骤。

## 模式边界

| Build | Troubleshoot |
|---|---|
| 用户要创建、部署、添加、迁移或明确准备 pin/release MR | 用户在问现有部署为什么失败、上一版本或回滚候选 |
| 输出受 workflow 约束的 GitOps 文件和 Ops Todo | 输出诊断/版本候选、证据和 Git-first 修复建议 |

同一次请求可以先完成当前 Build 的安全输出和 Ops Todo，再明确切换到 Troubleshoot；两个模式的步骤
不能混写。

## 只读证据采集

ArgoCD CLI 在诊断中只用于取证：

```bash
argocd app get <app> -o json
argocd app resources <app>
argocd app history <app>
argocd app manifests <app>
argocd app diff <app>
```

诊断模式**禁止**自行执行会改变状态的命令。下列命令仅用于说明用户可能选择的后续操作，
不是 playbook 的隐含授权：

```bash
argocd app sync <app>
argocd app sync --force --replace <app>
argocd app rollback <app> <revision>
argocd app terminate-op <app>
argocd app delete <app>
argocd app refresh <app> --hard
```

无 CLI 登录态时，用 ArgoCD UI、`kubectl -n argo-cd get app <app> -o yaml` 或 playbook 中的
其他只读命令补证。诊断完成后，只有用户明确要求实施，才能重新按相应操作 skill/guardrail
确认目标、影响、回滚方式，并优先提出 GitOps 修复。

## Playbook 合同

每份 playbook 必须：

1. 以用户可观察到的**症状或只读查询意图**命名，并给出最小只读证据链。
2. 区分相邻失败模式，给出可验证的根因和建议修复。
3. 只运行其 route 声明且对当前输入适用的 `related_validators`；不能用全仓
   `validate.sh` 代替。
4. 把 mutation、线上操作和跨团队协调明确标记为需要单独授权。

新增可复用的失败模式时，在同一维护变更中新增 playbook、路由和验证；不要把事故纪念或
无关平台教程塞进现有 playbook。
