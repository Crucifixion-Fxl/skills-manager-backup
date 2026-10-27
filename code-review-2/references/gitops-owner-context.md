# StreamPark 固定外仓身份与源码闭包

本契约对应 Task3 补充包v5，不授予模型自行启用能力的权限。
中央模板默认关闭，只有可信 fixed-source 契约、本独立契约、中央实现及工具枚举
全部匹配并由中央对唯一消费者启用，才可使用。当前缺少任一项则保留 incomplete。
作者快照不是权威；本地 PASS、MR 描述和模型自述不代替真实固定 Git 证据。

## 唯一消费者与依赖

- 父项目1146（DEV/argocd-apps），MR2810，原
  `aws-390709477306-us-staging/streampark-us-staging-access.yaml`。
  必须匹配原 Application、platform-control-plane、源 repo/path、目的 namespace
  和手动/prune/delete 设置。父 base/head 由可信 CI 元数据和实际 Git 树绑定；
  正常 rebase 不把 MR 正文或参数变成依赖授权。
- 子项目419（DEV/k8s），仅
  `297ddbc797c9ddb611d24874894cfc6edaa2c1fb` 和
  `171736f6bb1ddb49ba721438372e413f4d95ba66`。
- 闭包只有 `clusters/aws-390709477306-us-staging/streampark-access/` 下的
  identity.yaml、kustomization.yaml、registry-pull.yaml、renew.py、renewal.yaml。
  身份使用原严格投影的完整11个对象；其他四文件只给实际 Git 对象、摘要和两端相等证明。
  必须校验固定 Kustomization 的三资源及 ConfigMap 脚本引用和中央批准 blob，
  不能仅相信作者 manifest 自报列表/hash。

## 请求

先用 Read 读取 `source-context.json`，核对 root 项目、parent base/head、
source `streampark-access` 的419/两个固定 commit、binding 与 capability。
工具枚举必须有 `gitops-owner`，不能只看到原 `gitops-identity` 或 generic file 就使用。

```json
{"operation":"gitops-owner","source":"streampark-access","revision":"head","path":"clusters/aws-390709477306-us-staging/streampark-access/identity.yaml"}
```

revision=base|head；如需原身份只把 revision 改为 base。
不允许 query、window、offset、symbol、额外路径或任意 source。
新 source 的 file/tree/search、config 和旧投影操作均被拒绝，不继承通用源码权限。

## 证据核验与边界

成功结果中的 `parent_head_sha` 必须等于当前 meta head；`commit_sha` 是所选固定
依赖 revision。核对完整 `documents`、`closure`、各 `git_blob_oid`、字节数与 `sha256`，
确认四共享文件两端相同、闭包已验证，差异仅是原 Role 的 apps/deployments/get。
对 source-events.jsonl 的调用回执核对 source/project/revision 和结果；日志不包含
documents 或四共享文件原文。必要字段、对象或 source 不可用时保持 incomplete，
不将 UNKNOWN 当不存在，不重复请求来碰运气。

沿用同一预算账本：最多16个文件版本、32个唯一请求窗口和8 MiB总源码，
沿用现有去重/缓存语义；结果最多64 KiB。保持整份隐私检查、regular Git blob
和固定 commit 证明。不输出原始脚本/YAML/Secret，不执行脚本，不渲染或跟随外部引用。
源码闭包与投影证明不等于实际 Kustomize 输出，也不等于实时集群权限。

合并和原门禁完成后仍须完整18对象 repo-server 渲染/server dry-run、原手动 owner
Role-only 同步、实际 mounted-SA 正负权限，以及原两个 Application 的 TaskManager、
NAT/IRSA/checkpoint/savepoint/恢复验收。不得提前回填运行通过或开发交接。
撤回时由中央受控流程关闭本项协商并恢复策略，不让模型更改环境或集群授权。
