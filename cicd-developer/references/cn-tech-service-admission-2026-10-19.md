# CN tech-service 目录准入核验（2026-10-19）

本回执记录只读核验时点，不是 ResourceSpace 已上线或全部自助能力就绪的证明。
用户授权：核验剩余集群准入条件，条件满足后更新技能仓并运行准入脚本。

## 身份与 GitOps

- 腾讯云 STS 主账号 `100052802231`，区域 `ap-beijing`；TKE
  `cls-o0gwzdpz` / `cn-tech-service` 为 Running。云端实例 `ins-3k2pjxm3`
  与本机 context `cn-tencent-tech-service` 的 Node providerID 对应。
- `root-apps` 的 source 为 `DEV/argocd-apps` 的
  `tencent-100052802231-cn-tech-service`，revision
  `87ab08f6cd6f17f10213a5ae6e3c473b86c83c49`，Synced/Healthy。
- ArgoCD、Casdoor、Harbor、gitlab-runner-amd64、external-secrets 均
  Synced/Healthy，观察到 k8s source revision
  `089a6a4228181aea6188dfedb01d69c557043a30`。
- `app-runtime` generation 2 已允许 `infra/resourcespace-deploy.git` 来源及
  `prod-resourcespace` destination。该 Project 不是任意仓库/命名空间的通用授权；
  实际 Application 仍逐项核对 source、destination 和资源 kind，尤其不能假定
  ServiceAccount、Role、Job 或平台 Database claim 已获准。本次未扩大白名单。

## 平台入口

- 用户于 2026-10-19 明确确认：`argocd-cn-tech-service-tke.addx.live` 与
  `argocd-cn-tech-service.addx.live` 为 `cn-tke-tech-service` 下同一 ArgoCD 实例的双入口，
  旧 `cn-eks-tech-service` 已下线。此事实与本回执已有的 live Ingress、primary URL / additionalUrls
  记录一致；不是本次新执行的集群下线或 DNS 切换。不能按域名后缀区分集群或推断迁移状态。
- `argocd-cn-tech-service-tke.addx.live/api/version`：TLS 校验通过，HTTP 200，
  ArgoCD `v3.4.5`；无 `-tke` 域为 live additionalUrls。
- `/auth/login` 返回 303 到 `casdoor-cn-tech-service.addx.live/login/oauth/authorize`。
  live `argocd-cm` 的 issuer 与该域 discovery 的 `issuer` 一致，discovery HTTP 200。
  `casdoor-cn-tech-service-tke.addx.live` 是兼容入口，discovery 仍声明无 `-tke` issuer。
  这些证据不代表本轮完成了人类交互式 SSO 登录。
- VM `/select/0/prometheus/api/v1/query?query=up` 和 VMAlert `/-/healthy`
  均 TLS 校验通过、HTTP 200；不代表应用日志、告警通知或 SLO 验收完成。
- Builder Vault `/v1/sys/health` HTTP 200、initialized=true、sealed=false；
  目标集群存在对应 Ingress。此入口不改变 Ops 密钥域。
- `vault-backend` Ready=True，server=`https://vault-cn.addx.live`，KV v2 `secret`，
  JWT mount=`jwt-tke-cn-tech-service-2231`、role=`external-secrets`、
  audience=`vault`、SA=`external-secrets/external-secrets`。未读取业务密码。
- 平台入口公网 EIP `62.234.5.152` / `eip-4g1ihow9` 绑定
  `lb-qnrqungl` / `10.2.0.6`，与目标 VPC `vpc-3xp4xoe8` 对应。
  CLB 的 `SecureGroups` 为 `sg-f23hhii9`（beijing-office-all）及
  `sg-j0m1vt4p`（default）；入站规则分别 27 / 46 条，含深杭办公网允许项，
  未见 ACCEPT `0.0.0.0/0` / `::/0`，没有 AddressTemplate 引用。
  这核验了平台入口的配置边界，未从非白名单网络执行反向访问测试。

## Runner 与 Harbor

- [Runner 4942](https://gitlab.addx.ai/admin/runners/4942)：实例级，online，paused=false，
  locked=false，access_level=not_protected，run_untagged=false。服务端 tags 包含
  `cn-tech-amd64`、`cn-tech`、`kubernetes-cn-tech-service`、
  `sonar-scanner-cn-tech-service`、`harbor-02231-cn-tech-service`；catalog 仅提供架构明确的构建 tag。
- [作业 2120558](https://gitlab.addx.ai/DEVT/reportportal-plugin/-/jobs/2120558)
  成功，tag=`cn-tech-amd64`、Runner=4942；运行中的作业 2120557、2120567
  的 Kubernetes Pod 标签/Job ID 与此 Runner 对应，确实位于目标 TKE。
- ResourceSpace 项目 2110 的 `shared_runners_enabled=true`。当前 Developer 身份读取
  项目 Runner 列表返回 403；已通过实例 Runner 类型、未锁定状态、项目共享开关及其他
  实际作业核对基础调度条件，未触发 ResourceSpace 验证流水线，因此该项目首个作业的
  实际调度/构建/推送仍需后续发布验收。
- live Runner ConfigMap 的 `HARBOR_REGISTRY` 为
  `harbor-02231-cn-tech-service.addx.live`；manager、helper 和实际 Python 作业镜像均
  从该 registry 运行。项目镜像目的路径及 push 权限仍单独核验。
- 从目标 TKE 作业容器核验：私网域解析 `10.2.0.6`，公网 `-pub` 域解析
  `62.234.5.152`，二者 `/v2/` 均 TLS 校验通过、HTTP 401，要求 Bearer 认证。
  **realm 随请求域名分别为各自 `/service/token`**；不能照搬 staging 的 split DNS 合同。
  live Harbor core `EXT_ENDPOINT` 为私网域。该 `-pub` 域在本轮 Pod 中解析公网，
  不得描述成“集群内也是私网”。本机无私网路由，私网超时不作为集群内不可用证据。
- 使用现有 Runner 凭据在进程内核验受限公网 token 服务：HTTP 200；不输出/落盘任何凭据。
  pull secret 有私网精确 hostname auth key，没有 `-pub` auth key；新增公网消费者须另验配置。
- ResourceSpace 原 Task 指定的
  `base/ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55`
  在此 registry 的该路径认证后返回 404。**该精确镜像未通过，不能静默替换 digest，也不能
  用目录准入 PASS 放行依赖它的构建。** 本回执不宣称整个 registry 不存在该 digest。
- 同一路径的 `base/ubuntu:22.04` 认证读取 HTTP 200，manifest digest 为
  `sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02`，
  内容 SHA256 与返回 digest 一致。这是现有镜像可读取的证据，不是替换原指定 digest 的授权。

## 准入含义与未开放能力

上述证据解除“平台入口待切换、没有已核验 Runner、业务 AppProject 未交付”的历史目录阻塞。
`allowed` / `verified` 只允许进入按目标核验的工作流，不替代应用上线、权限审批或资源类型准入。

- ResourceSpace 的 Application/源目录/分支、ServiceAccount、ExternalSecret 消费契约、
  精确镜像、Ingress/SG 和实际业务上传/预览/授权下载仍须审核验收。
- **业务 CLB 的独立缺口**：本轮云端读取 `lb-84k2azmp`（10.2.0.17）的
  `SecureGroups=[]`，与 Task #2 旧回执“已绑定办公网安全组”不一致。
  此项不影响上方平台 CLB 的核验，但业务上线前必须重新核对 EIP/后端来源限制并补齐
  对应 GitOps/云端交付；仅空数组不能直接断言该业务入口已向任意公网开放。
  本轮未修改安全组、CLB 或应用 Ingress。
- 共享 MySQL 自动建库 Composition、TKE OIDC/COS CSI、Sentry onboarding、日志映射、
  迁移机器/带宽等能力没有因本次改目录而开放；遵循各自 workflow 和平台 handoff。
- ResourceSpace 已交付共享库和 Vault 托管 CAM 长期凭据的事实以
  [Task #2 平台回执](https://gitlab.addx.ai/infra/resourcespace-deploy/-/work_items/2#note_736047)
  为准，不能改称 Composition 或 OIDC 已交付；Task #2 保持未完成。
- 新的仓库、destination 或 kind 必须走自己的 GitOps 授权变更；不复用 default Project 绕过白名单。

复核命令：`python3 validators/check_deployment_target.py --env-keyword prod-cn-restricted-admin`。
该脚本只检查本地 catalog，不连接集群；运行态证据为本回执的独立前提。

## 本地变更验证

变更基于 `engineering/skills` main
`148ddf6834b5d9a26d6a8a974aecb20da593d394`，分支
`fix/cn-tech-service-admission-20261019`。验证使用临时 Python 3.14 环境和
PyYAML 6.0.3；没有修改检查脚本或已安装的 plugin cache。

- `check_deployment_target.py --env-keyword prod-cn-restricted-admin`：exit 0，
  `PASS: catalog admits cn-tke-tech-service; live readiness is not verified`。
- 同一脚本 `--cluster cn-tke-tech-service`：相同 PASS。
- `test_cn_tencent_routing.py` 与 `test_cluster_env_and_vault_facts_are_consistent`：
  共 19 passed，保留 blocked 和 pending-cutover 负向门禁测试。
- `scripts/validate.py --skill` 分别检查 cicd-developer、harbor、k8s-ops：
  均 0 errors / 0 warnings。
- `scripts/build_docs.py --indexes-only`、`--check` 和 `scripts/check_docs.py` 通过；
  生成目录新增此回执引用，并在 Skill 提交后同步作者/贡献者统计。`git diff --check` 通过。

该回执及本地 PASS 不代表变更已推送、合并或分发。远端 main 与已安装缓存仍须通过
正常发布流程更新，其他使用者不能将本地结果当作其安装版本的结果。

### CI 回归契约修正

Job 2125097 暴露旧测试仍将 `prod-cn-restricted-admin` 固定视为 blocked。
修正后，该关键字与 `prod-cn-tke-2231` 一样进入当前 CN 路由验证矩阵；日志入口
分别验证实际准入结果，以及显式模拟 blocked / pending-cutover 时仍拒绝。
未修改 validator、关键字或集群映射，日志实际交付仍未验证。
两个相关测试文件本地及独立审查复跑均为 68 passed；不是完整 CI 已通过的声明。
