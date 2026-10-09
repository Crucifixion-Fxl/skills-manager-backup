# ArgoCD Fleet Scanner 实例与认证

唯一owner：`argocd-fleet-scanner`。声明入口：`https://argocd-fleet.addx.live`。provider保留delegated，只委派本契约，不表示统一runtime adapter或MCP已接入；部署版本/实际subject/权限均pending。本次未登录、读凭据或调用业务API。

## 认证申请、消费与撤销

仓库声明真实在线scanner入口，sg-devops；其SSO、API/token契约与身份/权限尚未定位，credentialEnv不声明。不要把ArgoCD API token或本地kubectl身份套到scanner，不在未核对契约时自动探测、登录或重用凭据。申请/撤销/生命周期和服务版本均pending。

## 身份与能力路由

这是AddX私有在线scanner，不是公开ArgoCD产品API的别名；service源码https://gitlab.addx.ai/DEV/argocd-fleet-scanner已由部署记录定位，commit和API路由待研究，不猜endpoint或假定支持官方ArgoCD MCP。优先版本匹配的service自身OpenAPI/路由源码。argocd-fleet-scan的本地kubectl fallback仍按其K8s context/RBAC门禁；ArgoCD单实例访问由argocd owner管理。线上scanner的读取/配置/手动扫描副作用需source发现后按本owner授权、回读，不以本地扫描成功证明在线认证。

访问恢复只调用`web-access`。认证/版本漂移交由`platform-onboarding`更新本owner；source-only发现不得升级为线上验收。业务gate见[原消费者契约](../../argocd-fleet-scan/SKILL.md)；写必须明确目标/payload、最小权限、独立业务确认/发布门禁及写后回读，结果未知先对账。

## 官方或私有source入口

- AddX私有service来源见[source-discovery.json](source-discovery.json)；没有已核实的独立公开Skill/MCP/API，不套上游产品。
