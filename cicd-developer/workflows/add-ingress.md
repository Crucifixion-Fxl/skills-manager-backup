---
name: add-ingress
description: 给现有服务加 K8s Ingress 把流量从外面（公网 / office VPN / 内网）引到 Service。覆盖 4 种 SG 场景，强制走 DNS 解析申请前的暴露面核查。
---

# Workflow：add-ingress

## 目的

给已部署的 Service 加暴露入口。**不是** 给 service-to-service 内部调用——那个走 K8s Service DNS（`<svc>.<ns>.svc.cluster.local`）。

产出：
- K8s Ingress 资源（ingressClassName=alb，Kyverno 自动注入 ALB 配置）
- 更新 cd-requirements.md 的 "网络暴露" 段
- 产 Ops Todo：DNS 解析申请（addx.live 托管在阿里云 DNS，需运维）

## 进入条件

- 应用已部署（K8s Service 存在）
- 用户已说明：暴露场景（c-end 公网 / office VPN / 内网 / feishu webhook）
- 本 workflow 的 `ingress.yaml.tmpl` 是 AWS ALB recipe。`cloud != aws` 时 STOP + Ops Todo，先读取目标 TKE/GKE 已审定 controller/证书/SG/WAF 合同并补专用 recipe。当前 CN 全为 TKE，不能只补 TLS 或保留 ALB annotation 伪装成腾讯 CLB。

## Step 1. 解析需求

[precondition]
  - cd-requirements.md 列了 Ingress 需求

[action]
  - 提取：
    - `$app`              kebab-case
    - `$targets[]`        含需要 Ingress 的 target
    - `$exposure`         scenario，必须是以下之一：
        - `c-end-public`     公网 C 端服务（无来源 IP 限制，**必须** 挂 WAF）
        - `office`            办公室 / VPN 限制（自动注 from-office SG）
        - `internal`          仅 VPC 内可达（scheme=internal）
        - `feishu-webhook`    飞书国内服务器回调（自动注 feishu-webhook SG）
    - `$hostname`          完整 hostname（如 `my-app.addx.live`、`my-app-internal.addx.live`）
    - `$service_port`      Service 端口（不是 container port）
    - `$health_path`       ALB target health check 路径（如 `/health`）

[validate]
  - `$exposure` 是允许的 4 种之一
  - hostname 后缀跟 `$exposure` 一致：
    - c-end-public / office / feishu-webhook → `*.addx.live`
    - internal → `*-internal.addx.live`（仍是一层 `*.addx.live`，可复用各区域现有 wildcard ACM 证书）
  - service `$service_port` 在 K8s `kubectl get svc -n <ns> <app>` 输出里能找到

[output]
  - 内存变量

## Step 2. 暴露面核查（硬红线）

[precondition]
  - Step 1 完成

[action]
  - 按 CLAUDE.md 全局规则 #3 + `references/data/stop-conditions.yaml`：
  - 如果应用属于 **内部工具**（管理后台、可观测面板、中间件 UI、数据库控制台、内部门户等）：
      - 必须有实际附着 SG 的 office/VPN 来源限制和应用层认证；office 限制场景不强制 WAF
      - 核验每个附着 SG 的有效 ingress，不得有任何组放行公网全网；缺少任一保护 → **STOP**
  - 如果应用是 **面向 C 端的公开服务**：
      - 来源 IP 不限定，但 **WAF 必须挂**（`alb.ingress.kubernetes.io/wafv2-acl-arn`）
      - 业务层身份认证（OAuth / JWT）也建议有，但不强制（C 端用户没法预签）
  - 应用层 SSO 是 **业务侧** 实现（如 Casdoor OIDC 鉴权 middleware），workflow 检测不了——需要用户确认
  - 把核查结果写到 cd-requirements.md "网络暴露" 段，让 reviewer 能看到

[validate]
  - 内部工具未通过核查 → STOP
  - C 端公开服务：WAF ARN 已知（运维提供）
  - 如果 WAF ARN 不存在，需要先走 `crossplane-infra` 仓库的 WAFv2/IAM/ProviderConfig MR；本 workflow **不能** 在应用仓库里生成 WAFv2 / IAM / ProviderConfig YAML

[output]
  - `$waf_arn`（如适用，c-end-public 必填）

## Step 3. 写 Ingress（每 target 一次）

[precondition]
  - Step 2 通过

[action]
  - 用 `recipes/k8s/ingress.yaml.tmpl`，填槽：
      `{{app}}=$app`、`{{namespace}}=$target.namespace`、
      `{{hostname}}=$hostname`、`{{service_port}}=$service_port`、`{{health_path}}=$health_path`、
      `{{sg_label}}=`（按 `$exposure` 填或留空）、`{{scheme}}=`（internal 时填 `internal`，否则留空 / internet-facing）
  - 按 `$exposure` 调整：
      - `c-end-public`：不加 `ingress.addx.io/sg` label；annotation 加 `alb.ingress.kubernetes.io/wafv2-acl-arn: $waf_arn`
      - `office` / `internal` / `feishu-webhook`：加 `ingress.addx.io/sg: <scenario>` label
      - `internal`：annotation 加 `alb.ingress.kubernetes.io/scheme: internal`
  - 不得把本 ALB recipe 写入 TKE；非 AWS 目标在进入条件处已停止
  - 写到 `k8s/overlays/{$target.env_keyword}/ingress.yaml`
  - 加到 kustomization resources 列表
  - **应用还要对外暴露 gRPC（HTTP/2，如官方 SDK 直连）时**，按下面做，不要和 HTTP 端口挤在同一个 host 上按路径分流：
      - **独立 host**：在主 host 的服务名后加 `-grpc`，如主入口 `my-app-staging-us.addx.live` → gRPC `my-app-grpc-staging-us.addx.live`，暴露场景、SG label 与主入口相同；客户端连 `https://<grpc host>:443`
      - **同一个 Ingress 加第二条 host rule**，共用一个 ALB 和 `*.addx.live` 通配证书；不另开 ALB
      - **gRPC 后端用独立 Service**（如 `my-app-grpc`，只暴露 gRPC 端口），把协议注解写在这个 Service 上，只对它的 target group 生效，不影响 HTTP 后端：
        `alb.ingress.kubernetes.io/backend-protocol-version: GRPC`
      - 健康检查：模板在 Ingress 上设了 `healthcheck-path: {{health_path}}`（HTTP 路径），它会套到同一 Ingress 下所有 target group，所以 gRPC Service 上**必须显式覆盖**（Service 注解优先于 Ingress）：
        `alb.ingress.kubernetes.io/healthcheck-path: /AWS.ALB/healthcheck` + `alb.ingress.kubernetes.io/success-codes: '12'`
        （AWS 对 GRPC 的约定：调这个不存在的方法，后端回 12 = UNIMPLEMENTED 即算存活，应用不用实现健康检查方法）；应用有 gRPC 健康方法时也可改成 `/<package>.<Service>/<Method>` + `success-codes: '0'`
      - ALB 只在 HTTPS 监听器上支持 gRPC（以 AWS 文档为准）：落地时核实 Kyverno 给该 ALB 注入的 listener 是 HTTPS 443；`feishu-webhook` 的独立 ALB 不承载 gRPC
      - 为什么不按路径分流：gRPC 路径是 `/<package>.<Service>/<Method>`，要列全后端的所有 gRPC 服务名，上游新增服务就会漏路由；按 host 分没有这个问题
      - 首个按此落地的是 infra/ai-data-platform 的 Hub（`addx-rerun-hub`，2026-09）；真实集群验证后如有出入，回来修订本节

[validate]
  - 文件存在；YAML 可解析；`kustomize build` 成功
  - 公网 c-end → wafv2-acl-arn 已填
  - internal → scheme=internal 已加
  - 目标 `cloud=aws`，无 TKE/GKE 混用 ALB recipe

[output]
  - k8s/overlays/{$target.env_keyword}/ingress.yaml

## Step 4. 全量 validator

[precondition]
  - Step 3 完成

[action]
  - 如果 `k8s/` 在 MR 目标分支上没有历史 validator debt，运行严格门禁：
    `bash "$skill_root/validators/validate.sh" k8s/`
  - 如果完整目录已有与本次 Ingress 无关的历史 debt：先提交候选改动、确保工作树干净，并以 MR 目标的可信 remote-tracking branch 作为基线运行：
    `python3 "$skill_root/validators/validate_delta.py" --expected-origin https://gitlab.addx.ai/<group>/<repository>.git --base-ref origin/<mr-target-branch> k8s/`
    该命令验证 local origin 后 fresh fetch exact remote target；fresh target must be an ancestor of the candidate，否则 exit 2 并 rebase/rebuild/rerun。它对固定 target/base 与 candidate 运行完整严格套件并机械计算 scope 内 candidate write set；每个 carried debt path/object 必须 outside the candidate write set，overlapping debt must be fixed 并 exit 1。不能传本地 branch/SHA，也不能按 app/file/rule 做 ignore。记录 exact remote target/candidate SHA；MR HEAD 或 target SHA 变化必须重跑。

[validate]
  - 严格门禁或显式的 delta 门禁退出 0
  - delta 模式要求 candidate-only policy finding 与 candidate write set 上的 carried debt 都为 0；base/ref/祖先关系/依赖/输出不可用必须 exit 2 并 STOP

[output]
  - validator 日志

## Step 5. 出 summary + Ops Todo

[precondition]
  - Step 4 通过

[action]
  - Ops Todo 必填：
      - DNS 解析申请：`$hostname` → 对应 ALB DNS name（ArgoCD sync 后 ALB Controller 生成）
        - addx.live 在 **阿里云 DNS**（不是 Route53）
        - 申请前运维需核查 Ingress 是否裸奔（本 workflow 已在 Step 2 拦下，这里只需操作 DNS）
      - 如 c-end-public：WAF 已挂，确认 wafv2 ACL 在目标 region 存在
        - 若还没有 WAF ARN：Ops Todo 指向 crossplane-infra MR；应用仓库 MR 保持只包含 Ingress，等待 ARN 回填
  - 用 `recipes/docs/ops-todo-table.md`
  - 用 `recipes/docs/summary-card.md`

[validate]
  - summary 已打印

[output]
  - 最终用户消息

## 出口

用户拿到：
- `k8s/overlays/{env_keyword}/ingress.yaml`（每 target）
- Ops Todo：DNS 解析申请

ArgoCD sync 后：
- ALB Controller 看到 Ingress 自动建 ALB
- `kubectl get ingress -n <ns> <app>` 显示 ALB DNS name
- DNS 申请合并后 → hostname 可达
- 公网 c-end：WAF 已挂；内部工具：来源 IP 已限定

**常见踩坑**：
- ALB 一直 pending（无 DNS name）→ ALB Controller 没装 / IAM 缺权限 / 子网未标 `kubernetes.io/role/elb` tag
- 公网 c-end 用户访问 5xx → 跳 `troubleshooting/pod-runtime-crash.md`（健康检查失败 = ALB 摘后端）
- office SG 没生效（office 同事访问超时）→ 看 ALB 的 SG 列表里有没有 from-office，没有 = Kyverno mutation 没注入（label 写错？）
- gRPC 客户端连不上 / target unhealthy → 看 gRPC target group 的协议版本是不是 `GRPC`（注解要写在 gRPC 的 Service 上）、健康检查是不是被 Ingress 级 `healthcheck-path` 覆盖成了 HTTP 路径、客户端是否走 443 TLS
- TKE Ingress 报 `spec.tls is required` → 核验该目标 controller 的专用 Ingress/证书合同；本文 AWS ALB recipe 不适用，不能回 Step 3 补 TLS 后套用。没有已核验的 TKE recipe 则 STOP + Ops Todo。
