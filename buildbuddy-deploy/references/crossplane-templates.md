# Crossplane 资源模板

> 同步来源：DEV/crossplane-infra/aws-<account-id>-<cluster>/buildbuddy-waf-*.yaml

所有 Crossplane 资源放在 `crossplane-infra` 仓库的 `aws-<account-id>-<cluster>/` 目录下。当前 WAF 名称和 ARN 见 [facts.md](facts.md)。

> **不要放在应用仓库**，应用集群没有 Crossplane CRD，ArgoCD 会同步失败。

## WAF WebACL（内网只读）

```yaml
# buildbuddy-waf-readonly.yaml
apiVersion: wafv2.aws.m.upbound.io/v1beta1
kind: WebACL
metadata:
  name: buildbuddy-readonly-<cluster>
spec:
  forProvider:
    name: buildbuddy-readonly-<cluster>
    description: "Block write operations on internal gRPC endpoint"
    region: <region>
    scope: REGIONAL
    defaultAction:
      - allow: [{}]
    rule:
      - name: block-bytestream-write
        priority: 1
        action:
          - block: [{}]
        statement:
          - byteMatchStatement:
              - fieldToMatch:
                  - uriPath: [{}]
                positionalConstraint: CONTAINS
                searchString: "ByteStream/Write"
                textTransformation:
                  - priority: 0
                    type: NONE
        visibilityConfig:
          - cloudwatchMetricsEnabled: true
            metricName: block-bytestream-write
            sampledRequestsEnabled: true
      - name: block-actioncache-update
        priority: 2
        action:
          - block: [{}]
        statement:
          - byteMatchStatement:
              - fieldToMatch:
                  - uriPath: [{}]
                positionalConstraint: CONTAINS
                searchString: "UpdateActionResult"
                textTransformation:
                  - priority: 0
                    type: NONE
        visibilityConfig:
          - cloudwatchMetricsEnabled: true
            metricName: block-actioncache-update
            sampledRequestsEnabled: true
      - name: block-cas-batchupdate
        priority: 3
        action:
          - block: [{}]
        statement:
          - byteMatchStatement:
              - fieldToMatch:
                  - uriPath: [{}]
                positionalConstraint: CONTAINS
                searchString: "BatchUpdateBlobs"
                textTransformation:
                  - priority: 0
                    type: NONE
        visibilityConfig:
          - cloudwatchMetricsEnabled: true
            metricName: block-cas-batchupdate
            sampledRequestsEnabled: true
      # NOTE: do NOT block FindMissingBlobs - it's a read operation
      # (existence check before upload), not a write operation
  providerConfigRef:
    name: default
    kind: ClusterProviderConfig
```

## WAF WebACL（CI 入口门禁）

> **注意：** 此 WAF 仅检查 `x-buildbuddy-api-key` header 是否存在且非空，不校验 Key 的值。它的作用是入口门禁（阻止无 header 的裸请求）。onprem 版没有应用层 API Key 鉴权能力（auth/API 配置是 Enterprise 专属功能），因此当前安全边界是"网络层安全组 + WAF header 门禁"，不是真正的 API Key 鉴权。如需值级别校验，需升级到 Enterprise 版。

```yaml
# buildbuddy-waf-ci-auth.yaml
apiVersion: wafv2.aws.m.upbound.io/v1beta1
kind: WebACL
metadata:
  name: buildbuddy-ci-auth-<cluster>
spec:
  forProvider:
    name: buildbuddy-ci-auth-<cluster>
    description: "Require API key header on CI gRPC endpoint"
    region: <region>
    scope: REGIONAL
    defaultAction:
      - block: [{}]  # default deny
    rule:
      - name: allow-with-api-key
        priority: 1
        action:
          - allow: [{}]
        statement:
          - sizeConstraintStatement:
              - fieldToMatch:
                  - singleHeader:
                      name: x-buildbuddy-api-key
                comparisonOperator: GT
                size: 0
                textTransformation:
                  - priority: 0
                    type: NONE
        visibilityConfig:
          - cloudwatchMetricsEnabled: true
            metricName: allow-with-api-key
            sampledRequestsEnabled: true
  providerConfigRef:
    name: default
    kind: ClusterProviderConfig
```
