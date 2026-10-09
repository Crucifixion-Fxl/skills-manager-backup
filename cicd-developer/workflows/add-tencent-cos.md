---
name: add-tencent-cos
description: TKE 应用通过 Pod Identity、CAM 角色和 STS 临时凭据访问腾讯云 COS；平台准备资源后接入应用身份并验证。
---

# Workflow：add-tencent-cos

## 范围和依据

适用于已有 GitOps overlay 的 TKE 应用访问同账号 COS。新服务先走 new-service。
这是一条平台交付前置条件下的应用接入流程，不是 COS / CAM 的全自动资源创建器。
只写应用仓的目标 overlay ServiceAccount、工作负载 SA 引用及部署文档；SDK 代码适配
作为有明确验收条件的应用开发待办，不在本 workflow 自动生成。平台资源由运维通过
受管 GitOps 路径交付；本流程不写未知 Crossplane CRD，不直接修改云端或 live 集群。
跨账号、TKE 访问 AWS S3、CI Job 身份不在本流程范围，产 Ops Todo 评估，不套 AWS IRSA。

官方依据（执行时检查当前版本）：
- [TKE Pod OIDC / CAM](https://cloud.tencent.com/document/product/457/137141)
- [COS 临时凭据](https://cloud.tencent.com/document/product/436/14048)
- [COS 临时凭据 SDK 使用](https://cloud.tencent.com/document/product/436/68283)

业务 Pod 使用应用专属 ServiceAccount 和 CAM 角色，不复用 Crossplane 管理身份。
平台控制器的定制凭据链不能直接套用于业务客户端；每次接入都核验目标集群和 SDK。

## Step 1. 记录目标和资源契约

[precondition]
- 已读 `references/data/stop-conditions.yaml`。
- 应用已有 `docs/deployment/cd-requirements.md` 和目标 overlay。

[action]
- 从 `references/data/clusters.yaml` 和 `references/data/env-keywords.yaml` 解析
  cluster、cloud、account_id、namespace、env_keyword；cloud 必须为 tencent。
- 向资源 owner 核对 bucket 完整名称（含 APPID 后缀）、实际腾讯云地域、HTTPS endpoint、
  bucket 所属账号、允许的对象 prefix 和具体 read/write/list/delete/multipart 需求。
  不把业务短码 cn 当作腾讯云地域，不从 bucket 名推断 UIN。
- 记录应用语言、COS 或 S3 客户端依赖版本、预签名上传/下载和浏览器 CORS 需求。
- 不同权限的工作负载分别列 SA、CAM role、动作、prefix。

[validate]
- 每个目标和 owner 明确；同账号已核实；缺字段则 STOP 并列缺失输入。

[output]
- cd-requirements.md 中的 COS 接入表；尚未写应用身份配置。

## Step 2. 平台交付和只读核验

[precondition]
- Step 1 完成；使用目标集群的明确 context，不能依赖当前默认 context。

[action]
- 平台交付私有 COS 桶、权限策略和应用专属 CAM role，记录资源 owner / GitOps MR。
  IAM / ProviderConfig 归 `crossplane-infra`；业务资源沿平台核准的受管路径交付，
  不假设已有 COS claim 或 provider schema，不使用冻结 Terraform 仓。
- 检查集群已开启 OIDC、CAM 登记的 issuer / provider ID / audience 与集群一致，
  pod-identity-webhook Deployment 可用且 webhook 选择范围包含目标 Pod。
- 平台角色信任必须使用 `name/sts:AssumeRoleWithWebIdentity`、精确 federated provider，
  并以 `oidc:aud` 及 `oidc:sub=system:serviceaccount:<namespace>:<sa>` 限定身份。
  禁止通配 namespace / SA。用实时角色策略确认，不能只凭 ARN 名称推断权限。
- 权限仅允许实际需要的 COS actions 和指定 bucket/prefix；分别检查 bucket 级与对象级
  resource / condition。按 COS CAM 文档确认 list 与 multipart 的授权粒度，
  不把 AWS S3 action / ARN 照搬成 COS 策略，不授权 `*` 全资源或全操作。
- 验证 Pod 到 STS 和 COS 的 DNS / TLS / 网络路径；外网出口遵守固定 NAT 策略。
  私有桶不得为了验证而改为公开；浏览器 CORS 只按实际 origin/method/header 配置。
- 平台 handoff 必含 bucket/account/region/endpoint/prefix/actions、role ARN、
  OIDC provider/issuer/audience、精确 namespace/SA、策略核验结果和负责人。
  所有 JWT、临时密钥、Session Token 和预签名 URL 均不可输出到日志或文档。

[validate]
- handoff 和 live 只读证据均完整；否则 STOP 应用配置写入，按
  `recipes/docs/ops-todo-table.md` 列资源、原因、输入和验收条件。
- 不能把缺平台资源自动降级为长期 AK/SK；例外须平台明确评估，且不得编造 COS Vault 路径。

[output]
- 可审阅的脱敏平台 handoff，或具体 Ops Todo；开桶和 CAM 配置仍归平台。

## Step 3. 应用 ServiceAccount 与工作负载绑定

[precondition]
- Step 2 通过；role ARN、audience、namespace/SA 来自已核验 handoff。

[action]
- 使用 `recipes/k8s/serviceaccount-tke.yaml.tmpl`，填 app、namespace、role_arn、audience，
  写目标 `k8s/overlays/<env_keyword>/serviceaccount.yaml`，纳入 kustomization resources。
  如果已有 SA，保留其他必要字段，只合并这两条 annotation；不覆盖已有不同的身份绑定，
  遇到冲突 STOP 并记录需要迁移的身份。
- 在目标 overlay 为现有 Rollout / Deployment / StatefulSet 的 Pod template 添加
  `spec.template.spec.serviceAccountName: <sa>`；使用 Kustomize patch，不改其他云共享 base。
  不新建工作负载或给其他目标写腾讯云 annotation。
- token 文件由 webhook 注入；不手工创建长期 SA token Secret，不把 JWT 写入 Git/Vault。

[validate]
- `kustomize build k8s/overlays/<env_keyword>` 成功，输出中 SA 身份、namespace、
  role ARN、audience 及工作负载 serviceAccountName 与 handoff 精确相等。
- `bash "$skill_root/validators/validate.sh" k8s/` 退出 0；其 PASS 不证明 CAM 权限或 SDK 刷新正常。
- 检查其他 overlay 的渲染未因此次变更改变身份。

[output]
- 仅目标 overlay 的身份配置和绑定变更；通过 Git MR 交付，不直接 apply。

## Step 4. 应用凭据适配契约

Python 原生 COS 的接入示例见 `references/tencent-cos-python.md`；
该版本预签名需显式传 `Params={"x-cos-security-token": token}`，只在 client 配置 Token 不够。

[precondition]
- 已确定实际 SDK 和版本；仅 SA annotation 不足以证明客户端接入成功。

[action]
- 将以下要求写入 cd-requirements.md 的应用开发待办，由应用 owner 实现和验收：
  1. 使用当前腾讯 SDK 支持的 Web Identity credential provider，读取 webhook 注入的
     `TKE_ROLE_ARN` / `TKE_WEB_IDENTITY_TOKEN_FILE`，及该版本需要的 provider ID / region。
     核对注入结果和 SDK 文档，不假设默认凭据链一定识别。
  2. 向腾讯 STS 换取 `TmpSecretId`、`TmpSecretKey`、`Token` 和 expiration；
     COS 客户端必须同时使用三项凭据。凭据只保存在进程内存，不写静态配置。
  3. 在过期前刷新 STS 凭据，并重新读取轮换后的投影 token 文件；刷新应并发合并、
     有限退避，失败不得继续使用已过期凭据或静默回退节点/长期密钥。
  4. AWS SDK / S3 兼容客户端不能自动视作支持 TKE OIDC。需要经该语言/版本验证的
     credential adapter，并核验 COS endpoint、签名和 Session Token 传递方式；
     不把腾讯 Web Identity 请求发到 AWS STS。未验证则保持待办，不宣称兼容。
  5. 预签名操作需携带正确 token；URL 有效期不得超过临时凭据剩余有效期，留出时钟余量。
     使用原生 COS 和 S3 兼容链路时分别做实测；若用浏览器直传，再验证 CORS。

[validate]
- 记录 SDK 版本、适配实现引用、刷新和预签名测试结果。未完成则明确标记“应用适配待完成”，
  允许提交配置 MR 供审阅，但阻止发布/宣称接入完成。

[output]
- SDK 接入要求和应用 owner 的验收项；不输出未经版本核实的通用 SDK 代码。

## Step 5. 发布后验收与回退

[precondition]
- 平台资源已交付，应用适配完成并通过评审；按既有发布流程合并和 reconcile。
- 本 workflow 不代替生产发布授权；staging 与 production 分别交付和验证。

[action]
- 从新建业务 Pod 核验 SA、注入环境变量名称和投影 token 路径/可读性，绝不打印 token 内容。
  SA annotation 改变不会自动更新已存在 Pod，需在批准的发布中重新创建 Pod。
- 用实际应用客户端在批准的测试 prefix 做所需的上传/读取/删除或 multipart 操作。
  只测已授权动作，测试对象由授权 cleanup 或生命周期清理，不为清理额外扩大业务权限。
- 负向验证越权 prefix / 未授权操作被拒绝；另用未授权 SA 验证无法扮演该角色。
- 覆盖投影 token 轮换及 STS 凭据刷新后继续成功；预签名在有效期内可用、过期不可用。
  不向 MR 上传 URL 或凭据；记录 app SHA、目标、时间、脱敏结果与证据位置。
- 回退应用 MR 到原身份和应用版本；不要默认删除桶或数据。紧急撤权由平台修改角色权限，
  仅移除 annotation 不会立即撤销已签发的临时凭据。

[validate]
- 正向、越权拒绝、错误 SA 拒绝、刷新、适用的预签名/CORS 检查全部通过才称接入完成。
- 任何未执行项明确标记 pending，不拿 Crossplane Provider 的成功替代业务客户端验收。

[output]
- `docs/deployment/cicd.md` 记录身份流、验收与回退；输出 Ops Todo 和实际完成范围。
