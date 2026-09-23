---
name: cross-account-db-timeout
description: 跨 AWS 账号 / 跨 VPC 访问 RDS / Redis / DocumentDB / Aurora timeout。按 VPC peering、双向路由、目标 SG 入站三层排查；禁止把数据库改 publiclyAccessible。
---

# Troubleshooting：cross-account-db-timeout

## 症状

应用从账号 X / VPC X 访问账号 Y / VPC Y 的 RDS、Aurora、Redis、DocumentDB 超时：

- JDBC `CommunicationsException: Communications link failure`
- `java.net.ConnectException: Connection timed out`
- Redis / Mongo / SDK 连接 timeout，message 很少

这类症状只说明 TCP 不通；按三层排查，不要先改应用配置。

## Step 1. 确认 VPC peering active

[action]
  - 运维用 AWS CLI 查源/目标账号 peering：
    ```bash
    aws ec2 describe-vpc-peering-connections --region <region> \
      --filters "Name=accepter-vpc-info.owner-id,Values=<target-account>" \
                "Name=requester-vpc-info.owner-id,Values=<source-account>" \
      --query 'VpcPeeringConnections[].[VpcPeeringConnectionId,Status.Code]'
    ```

[decision]
  - 没有 peering 或不是 `active` → Ops Todo：建立 / 修复 VPC peering，STOP
  - active → Step 2

## Step 2. 确认双向路由

[action]
  - 运维确认源 VPC route table 有目标 VPC CIDR → peering。
  - 运维确认目标 VPC route table 有源 VPC CIDR → peering。
  - 不要只看主 CIDR。RDS ENI 可能落在扩展 CIDR；先查目标资源真实 ENI IP / subnet，再反查 route table。

[validate]
  - 源到目标、目标回源两边都有 route。
  - route 指向同一个 peering connection。

[decision]
  - 任一方向缺 route → Ops Todo：补 route，并记录到平台 manual infra change 文档，STOP
  - 双向都有 → Step 3

## Step 3. 确认目标 SG 入站

[action]
  - 找目标 RDS / Redis / DocumentDB 当前挂载的 SG。
  - 检查是否放通源 VPC CIDR + 正确端口：
    - MySQL / Aurora MySQL: 3306
    - PostgreSQL / Aurora PostgreSQL: 5432
    - Redis / ElastiCache: 6379
    - DocumentDB / MongoDB: 27017
  - 正确修法是在现有 SG 上加独立 `SecurityGroupIngressRule`，不要改 RDS 的 `vpcSecurityGroupIds`。

[validate]
  - CIDR 收敛到源 VPC CIDR（通常 /16），不要 `10.0.0.0/8`。
  - MR 描述列出共享 SG 当前覆盖的数据库 / 缓存资源，说明 blast radius。

[decision]
  - SG 缺规则 → Ops Todo：给目标 SG 加最小 CIDR ingress rule，STOP
  - SG 已放通 → Step 4

## Step 4. 从源端验证 TCP

[action]
  - 运维从源集群 pod 测 TCP：
    ```bash
    kubectl --context <source-cluster> -n <namespace> exec <pod> -- python3 -c '
    import socket
    s = socket.socket(); s.settimeout(5)
    s.connect(("<endpoint>", <port>))
    print("OK", s.getpeername())
    '
    ```

[decision]
  - 返回 `OK` → 网络已通，回到应用层检查用户名、密码、TLS、数据库白名单等。
  - 仍 timeout → 回到 Step 1-3，通常是扩展 CIDR / 子网 route table 漏查。

## 红线

- 不要把内部 RDS / Redis 改 `publiclyAccessible: true`。
- 不要直接编辑 Crossplane 管理的 `vpcSecurityGroupIds`。
- 不要用 `10.0.0.0/8` 一把梭放通。
- 不要复制别的账号 `providerConfigRef.name: ec2`；目标账号未必有同名 ProviderConfig。

## 输出

最终给用户：
- 卡在哪一层：peering / route / SG / app layer
- 需要运维改什么
- 源 CIDR、目标 SG、端口、peering id、route table id
- 验证命令和预期结果
