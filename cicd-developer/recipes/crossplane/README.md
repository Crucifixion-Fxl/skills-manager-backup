# recipes/crossplane/

同一 owning 仓库可以有多个独立渲染的 Application，按权限职责分别绑定批准的 runtime
或数据面 Project；一个 Application 只绑定一个 Project，每个资源只有一个管理者。
批准的平台 claim 可以留 runtime，A1 密码链与 DB CR 的依赖不能仅按 kind 机械拆散。
存量拆分先审批 ownership 交接、prune/finalizer 保护与回滚；IAM/ProviderConfig/WAF
仍在平台入口，owner 名称不替代云权限约束。完整指导见
[权限事实表](../../references/data/permission-boundaries.yaml)。

这里是 Crossplane 资源、ProviderConfig、IAM、数据库连接链路和入口边界模板。具体应该生成哪组
资源由 workflow 决定；不要根据本目录文件名推断支持范围。

## 所有权与 ProviderConfig

- 应用自己的数据资源 claim/CR、ExternalSecret 和 workload 放**应用仓**；集中 IAM、ProviderConfig、
  WAF/IPSet、NineData provider identity 和共享 SG ingress 放 `crossplane-infra`。
- app-owned 数据资源使用该 app 的 ClusterProviderConfig；相应的 app IAM Role、ProviderConfig 和
  RolePolicy 必须先由同一变更建立。不能把缺 ProviderConfig 当作“先跳过”的前置条件。
- runtime Pod IRSA、CI Job Pod IRSA 和 Crossplane app RolePolicy 是三种不同身份；仅使用对应的
  recipe/workflow，不能互相借用。
- m.upbound.io v2 资源不接受 `spec.deletionPolicy`。需要 orphan 语义时使用不含 `Delete` 的
  `managementPolicies`；prod 的具体组合和保护字段以 cost-tiering/workflow 为准。

## RDS/Aurora 密码链路

RDS 和 Aurora 使用 ESO Password Generator → ExternalSecret → PushSecret 的 A1 链路，且与 DB CR
放在同一应用 overlay，由 sync-wave 保证顺序。分别使用其专用密码 recipe；不得复制 RDS 模板后手改
名称或 Vault 路径。

红线：

1. 不要手工删除生成的密码 Secret。
2. 不要改 generator spec。
3. **禁删 generator 型 ExternalSecret 再重建**；`deletionPolicy: Retain` 不保证重建不会覆盖
   现有 Secret，可能造成 K8s、Vault 和 AWS 密码漂移。
4. namespace prune 前先按 workflow 备份所需 Vault 值；紧急 rotate 走运维带外流程。

## 资源特有合同

- RDS、Aurora、ElastiCache：resource identifier/external-name 必须显式且语义一致。
- NineData：只按 `add-ninedata-datasource.md` 写受支持的海外 DataSource 合同；prod orphan 语义通过
  template slot 填入完整 `managementPolicies` 块，不能在 YAML 中手删字段。
- CloudFront：先 bootstrap OAC/Distribution，再在 Ready 后按 workflow 执行 SourceArn hardening；
  bootstrap 不是完成态。
- WAFv2、S3、GCS、IRSA 和 CI IRSA 都有专用 workflow 与 recipe，不能由数据库模板衍生。

校验入口见 `validators/check_db_resource_contracts.py`、`check_cloudfront_oac.py` 和
`check_repo_boundary.py`；跨仓边界与权限以 `references/data/permission-boundaries.yaml` 为准。
