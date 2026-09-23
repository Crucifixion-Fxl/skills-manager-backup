---
name: ninedata-datasource-issues
description: NineData DataSource Ready=False / SQL 任务报 Connect failed / AccessKey must not be null / COMMERCIAL_INSUFFICIENT_QUOTA / accessAlias not registered 等 NineData 自助注册的失败模式。
---

# Playbook：NineData DataSource 失败

## 症状

按 `workflows/add-ninedata-datasource.md` 配好后：
- `kubectl get datasource` 显示 `Ready=False` 或 `SYNCED=False` 长时间不变
- 数据源 Ready=True 但 NineData WebUI 跑 SQL 查询报错
- WebUI 跑 SQL 变更任务（特别是含数据备份的）报错
- ArgoCD sync 失败，事件含 `COMMERCIAL_INSUFFICIENT_QUOTA`

## 诊断 + 修复对照表

### 信号 1：DataSource Ready=False, reason=Creating，长时间不变

```
kubectl describe datasource <name>
```

看 condition message，按下面 message 分支判：

| message 含 | 模式 |
|---|---|
| `COMMERCIAL_INSUFFICIENT_QUOTA` | **模式 1：50 slot 配额满** |
| `cloudProfile.accessAlias "xxx" is not registered` | **模式 2：accessAlias 未在 ProviderConfig Secret 下发** |
| `meta DB credentials missing on ProviderConfig Secret` | **模式 3：ProviderConfig 缺 meta_db_* keys** |
| `PERMISSION_DENY module:DataSource` | **模式 4：provider 服务账号缺 permission 表行** |
| 网络 timeout / connection refused | **模式 5：provider Pod 出口 IP 错（不在 NineData 白名单）** |

### 信号 2：DataSource Ready=True，WebUI 跑 SQL 查询报错

DataSource 自身建出来了，但 SQL 任务执行器跑不通：

| 报错 | 模式 |
|---|---|
| `Connect failed! Connect failed! username is required`（**没有** `Init source connector failed` 前缀） | **模式 6：cloudProfile 三字段缺失** |
| `Init source connector failed, ... Connect failed! username is required` + `AccessKey must not be null` | **模式 7：accessAlias 缺失（备份任务挂）** |
| `AccessKey must not be null`（**没有** `Init source connector failed` 前缀；调用栈 `io.minio.MinioClient`） | **模式 8：regionId 被 OpenAPI 静默改写** |

⚠️ 模式 7 和模式 8 文案一样（`AccessKey must not be null`），但调用栈不同——**先扒 jzcloud-console pod 日志看完整调用栈**再判模式。

## 各模式修法

### 模式 1：50 slot 配额满

NineData 自建 ENTERPRISE license 整 org 共享 50 slot。`COMMERCIAL_INSUFFICIENT_QUOTA` = 接近或到上限。

**特别坑**：失败的 create 也会留 orphan record（NineData 是"先落库再校验"）。

修法：
1. 找运维 DB DELETE 清掉本次失败留的 orphan record（必做，否则下次 retry 也会撞同样的"已存在"或继续占 slot）
2. 找运维评估当前 active 数据源 + 清掉真正废弃的（review fleet 哪些 app 已经下线但 DataSource 没删）
3. 实在不够 → 找 NineData 商务谈扩配额

预防：**提交本 workflow 前** 跟运维确认剩余 slot，剩余 < 3 不建议新增。

### 模式 2：accessAlias 未在 ProviderConfig Secret 下发

`cloudProfile.accessAlias` 写了 `aws-overseas` 但 ProviderConfig 的 `cloud_access_aliases` map 里没这条。

修法：产 Ops Todo "找运维核查目标集群 ProviderConfig Secret `cloud_access_aliases` map，确认含 `aws-overseas`"。这是平台级配置不是业务侧。

### 模式 3：ProviderConfig 缺 meta_db_* keys

provider v0.1.3+ 要求 ProviderConfig 的 Secret 里有 5 个 `meta_db_*` keys（NineData meta MySQL 直连凭据，用来 patch cloudProfile / accessAlias / regionId 等 OpenAPI 不暴露的字段）。

不该业务侧管。产 Ops Todo "运维核查目标集群 ProviderConfig Secret meta_db_* keys 是否齐全"。

### 模式 4：PERMISSION_DENY module:DataSource

provider 用的 NineData 服务账号缺 `permission` 表行。

不该业务侧管。产 Ops Todo "运维在 NineData WebUI 给目标集群服务账号补 DataSource 模块权限"。

### 模式 5：provider Pod 出口 IP 异常

provider-ninedata Pod 应该跑在 `crossplane-providers` NodePool（私网子网，NAT 出口已加 NineData 白名单）。Pod 出口 IP 不对 → NineData 拒连。

排查（运维）：
```
kubectl -n crossplane-system get pod -l app=provider-ninedata -o jsonpath='{.items[0].spec.nodeName}'
kubectl get node <node> -o jsonpath='{.metadata.labels}'
# 期望 node-group=crossplane-providers
```

不对 → 运维调度问题。

### 模式 6：cloudProfile 三字段缺失

NineData OpenAPI `/datasource/create` **不暴露** `env` / `instanceType` / `cloudInstanceType`，但 SQL 任务执行器严重依赖它们。不写 → 默认 `env=IDC` → 执行器按"自建机房 SSH 跳板机"分支序列化 → username/password 被扔掉 → SQL 任务 pod 报 `Connect failed! username is required`。

⚠️ **WebUI 查询误以为 OK**（走同进程 JDBC，不经过这层序列化）。需要跑 SQL **任务**（不是 WebUI 查询）才能发现。

修法：补全 CR 的 `cloudProfile`：
```yaml
cloudProfile:
  env: AWS
  instanceType: Aurora           # AWS RDS 全部用 Aurora
  cloudInstanceType: URL         # host 是 DNS endpoint
  accessAlias: aws-overseas
```

`recipes/crossplane/ninedata-datasource.yaml.tmpl` 默认就有这 4 字段；用模板就不会踩。

### 模式 7：accessAlias 缺失（备份任务挂）

NineData 在两种场景走"先备份再执行"路径：
- DML 变更任务影响行数较大（实测过 37,987 行触发）
- 用户在变更任务表单勾"数据备份"

备份路径的 binlog connector 要直接调云厂商 RDS API（如 `DownloadDBLogFilePortion`），需要数据源行的 `access_id` 字段绑定到 `meta.access` 表里 vendor=aws 的云账号。**没绑就报**：

```
数据备份: Init source connector failed, error message:Connect failed! Connect failed! username is required
执行 SQL: AccessKey must not be null
```

修法：CR `cloudProfile` 加 `accessAlias: aws-overseas`。provider v0.1.4+ 自动 UPDATE `access_id`。如果加了仍报 `accessAlias "xxx" is not registered` → 跳模式 2。

### 模式 8：regionId 被 OpenAPI 静默改写

`regionId: ninedata-cn-hangzhou` 是 NineData 自建 region 表唯一一行。但 OpenAPI 有 bug：传 `ninedata-cn-hangzhou` 进去落库后被改成 `aliyun-cn-hangzhou`。SQL 任务（特别是大查询、change task + 备份、导出）要去 `meta.base_config` 拿 MinIO endpoint / accessKey / secretKey，这张表用 region 末段（`cn-hangzhou`）做 key；改写过的值匹配不到，返回 `{}`，构造 MinIO client 直接 NPE：

```
java.lang.NullPointerException: AccessKey must not be null
  at io.minio.MinioClient$Builder.credentials(MinioClient.java:...)
  at MinioStorageClient.init line 47
```

修法：
1. 检查 `meta.datasource` 表里 `region_id` 字段，应该是 `ninedata-cn-hangzhou`，被改成 `aliyun-cn-hangzhou` 就是中招了
2. 升 provider-ninedata 到 v0.1.6+——v0.1.6 在 metadb.go patch 时同时把 `region_id` UPDATE 回 spec 值 + Observe 阶段比对 drift（在线 E2E 已验证会自动拉回）
3. CR 模板**不要改** regionId 值；recipes/crossplane/ninedata-datasource.yaml.tmpl 默认就是对的

## 为什么不走 <替代方案>

- **改 CR 的 `deletionPolicy: Orphan` 兼容老版本** —— v0.1.2+ CRD **不接受** `deletionPolicy` 字段，会被 API 拒。要 Orphan 语义改 `managementPolicies: [Observe, Create, Update, LateInitialize]`（不含 Delete）
- **绕过 cloudProfile 直接登 NineData WebUI 配** —— 改完不在 git；下次 ArgoCD sync 走 Observe 又会被 controller 拉回 `env=IDC`。**必须从 CR `cloudProfile` 写**
- **WebUI 手工建 DataSource 不走 Crossplane** —— 失去 GitOps；NineData admin 手工配的也容易漂移；且不影响 50 slot 配额（一样占）

## 参考

- `workflows/add-ninedata-datasource.md`（接入流程）
- `recipes/crossplane/ninedata-datasource.yaml.tmpl`（CR 模板 + 6 个 gotchas 注释）
- provider-ninedata：[DEV/provider-ninedata](https://gitlab.addx.ai/DEV/provider-ninedata)（版本 v0.1.6 起 region drift 自动 patch）
- v1 cicd-developer/references/ninedata-datasource.md（更多历史细节）
