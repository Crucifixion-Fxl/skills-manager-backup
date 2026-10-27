# 存量应用的运行入口

路由 `env_keyword` 选择集群，不等于业务仓 overlay 目录、Application 名或 Pod env。
DB migration 和日志维护使用本文解析已存在应用的运行入口。其他仍限定标准布局的
workflow，遇到非标准入口须先取得专属适配方案或 STOP，不因解析成功自动扩展其能力。
维护前先从精确目标 Application 解析运行入口。不能把
`prod-cn-tke-2231` 机械拼成目录，也不能改用指向 cn-main 的旧 keyword。

1. 按 `env-keywords.yaml` / `clusters.yaml` 选择 allowed 目标，核验实际
   argocd-apps checkout 和该集群目录中的准确 Application。
2. 核对 Application source 的 credential-free repository identity 与应用 checkout
   origin、revision/工作树及 owner。只读渲染实际 `spec.source.path`；保存完整 render。
   运行目录与 `kustomization.yaml` 的真实路径均须位于应用 checkout 内，入口本身须为
   非符号链接的普通文件。即使叶子链接指向仓库内文件，DB/日志修改入口时也可能写到
   其他 overlay 或共享文件；普通维护须 STOP，核实实际写入位置及所有消费者后转专属
   handoff，不把共享入口的修改授权交给此解析器。
   日志补标签时使用 `--allow-label-repair`，以已绑定 Application 的 app/env 为期望身份；
   只允许缺失 app 或缺失/错误 env 作为待修复状态，错误 app owner 仍拒绝。普通维护保持
   严格模式，日志修复后也必须重新严格解析完整 render。
   单 source Kustomize 之外的结构、远程 destination、缺失 owner 或身份不一致时 STOP，
   转目标专属 handoff，不猜测目录，也不新建替代 overlay。
3. 调用只读解析器：

   ```bash
   python3 "$skill_root/validators/resolve_existing_runtime.py" \
     --env-keyword <routing-keyword> --app <canonical-app> \
     --argocd-root <argocd-apps-checkout> --application-file <exact-Application-file> \
     --app-root <application-checkout> --source-repo-url <verified-credential-free-origin> \
     --rendered <exact-runtime-render.yaml>
   ```

4. 将 JSON 的 `runtime_source_path`、`application_name`、`namespace`、
   `runtime_env_label` 保存到对应 `$target`；所有维护写入、render、validator 和 summary
   使用同一 `runtime_source_path`。`env_keyword` 仅保留为路由选择器。复杂 source/destination
   仍需要专属核验，解析失败不授权降级为 `k8s/overlays/<keyword>`。

`env-keywords.yaml` 是集群路由登记，不是历史 Pod env 的白名单。解析器的 phase 检查
只核对维护环境，严格模式仍要求完整 render 的 env 与精确 Application 逐字一致；
即使二者都声明未登记的 `prod-unknown`，输出该身份也不批准部署或日志映射。
cn-prod 日志门禁只接受目标兼容表里核实过的标签，其余标签必须 STOP。

新建服务尚无 Application 时，由父 workflow 在创建候选 overlay 时明确记录
`runtime_source_path=k8s/overlays/<keyword>`、`runtime_env_label=<keyword>`、新 Application
名与 namespace，交给子 workflow；此分支不能用于维护已有 Application。父流程也须先过
[目标日志门禁](logging/README.md#cn-prod-日志映射门禁)。

解析器只描述已存在身份，**不授予 namespace 例外、Deployment 例外、日志就绪或部署权限**。
完整 namespace、资源、身份和仓库边界 validator 仍必跑。跨仓字段相同不证明对应资源
已经获准；不能用这份 JSON 绕过未完成的源/目标归属、准入或切流门禁。

## cn-prod 存量 namespace 决策

固定 [engagement Application](https://gitlab.addx.ai/DEV/argocd-apps/-/blob/7daa17dc17dc7a0270c23b0062ee8605b1ec8fec/tencent-100052802231-cn-prod/engagement-service-prod-cn.yaml)
保留 `backend/k8s/overlays/prod-cn-tke`、`env=prod-cn`、`namespace=prod-cn`。
当前命名规则要求 `prod-engagement-service`；registry 尚无该目标精确例外，因此应 STOP。
本次路由登记及路径解析均不批准历史 namespace。

若负责人批准迁移期保留，另行登记完整 repository/path/Application/app/cluster/namespace
双端身份及 owner、tracking、到期日、退出方案和负向测试。当前例外 parser 仅接受
`k8s/[...]/overlays/<target>/kustomization.yaml`，无法表示 `backend/k8s/...`；批准后必须先
扩展安全的完整路径合同，不能剥掉 backend 或改成宽泛后缀匹配。否则协调 namespace、
Service/Ingress、身份与凭据迁移，不在普通维护 workflow 中直接更名。
