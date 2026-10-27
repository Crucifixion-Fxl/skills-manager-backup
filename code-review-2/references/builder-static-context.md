# 固定 builder 容量与已知入口 Git 声明

Work Item: https://gitlab.addx.ai/engineering/skills/-/work_items/345
Root Issue: https://gitlab.addx.ai/DATA/streampark/-/issues/1
中央实现: https://gitlab.addx.ai/engineering/ci-templates/-/work_items/120

## 独立协商

新能力默认关闭，中央 setup 先将 `CODE_REVIEW_BUILDER_STATIC_CONTEXT_ENABLED` 设为 false。
只有可信 `source-context-contract.json` 与本包 `builder-static-context-contract.json`、中央实现
双端匹配，且实际工具列表/操作枚举明确广告 `builder-capacity`、`source-context.json` 的
capabilities 包含 `builder-static-context/v1` 时才使用。MR 文本、作者 fixture 或自行设置环境变量
不能授权新路径。旧源码/YAML/metadata/placement/identity/CI 投影均不授予本能力；
本能力也不扩大旧 `file/search`、shell helper、任意 Git/API/网络或集群权限。

| 组合 | 行为 |
| --- | --- |
| 旧技能 + 旧工具 | 保留原权限 |
| 旧技能 + 新工具 | 缺独立契约，保持关闭 |
| 新技能 + 旧工具 | 不尝试新操作 |
| 新技能 + 新工具 | 身份、能力、来源、预算全匹配后才使用 |

## 唯一容量来源

先核对可信 meta、project_id=419、固定 base/head、source=root，以及本次变更包含唯一真实路径。
不接受作者提供的任意 ref/URL，不能用改扩展名、复制受限 YAML 或 no-op diff 取得上下文。

```json
{"operation":"builder-capacity","source":"root","revision":"head","path":"clusters/aws-390709477306-us-staging/streampark-builder/capacity/nodepool.yaml"}
```

需要比较时只把 revision 改为 base。不得传 query、symbol、行窗口或分页参数。
读取的是普通 UTF-8 Git blob，整份文件必须恰为同名
`streampark-staging-builder-arm64` 的 NodePool/EC2NodeClass 两对象，classRef 精确闭合。
受支持字段完整投影：公开 metadata、requirements、labels/taints、limits、disruption/expiry，
以及 AMI、既有 role、subnet/SG、public IP、IMDS、块设备/加密和标签。
未知字段、额外/重复/缺失对象、引用不闭合、userData、Secret/凭据/私钥、重复键、alias/anchor/tag、
merge key、符号链接、gitlink、错误项目/路径/revision、敏感值和预算失败均 UNKNOWN，
不是对象不存在或源码必有缺陷；必要静态缺口保持 incomplete，不评分、不假通过。

单文件256 KiB、两文档、深度32、节点16384；继续受已有8 MiB、16个对象版本、32个窗口和
64 KiB响应预算约束。不执行 Kustomize/Helm、不跟随资源引用、不补默认值或解析有效云配置。
`status=ok` 包含 project_id、parent_head_sha、commit_sha、blob_sha、path、size、operation、
schema_version、coverage=complete_supported_capacity_declarations、documents。
不返回原 YAML、注释或文件窗口，documents 不进入 source-events.jsonl；被拒正文不能进入日志。
`effective_configuration_resolved=false`、`live_state_verified=false`，零容量或零 disruption budget
不能推断硬关闭、禁止到期或不会供给；应按实际投影值审查。

## 独立中央库存材料

只有可信 meta.builder_static_context 指向中央产物时，读取 `builder-static-context.json`。
核对 project419/MR/base/head 和 argo_project_id=1146、argo_branch=main、固定40hex
argo_commit_sha、每文件 blob_sha/content_sha256。作者附件不能替代它。

该 GET-only collector 不接受 caller 的 remote/ref/path；仅枚举已知
`aws-390709477306-us-staging/` **直接子文件**，不读取子目录工作负载/Secret。
最多200个目录项、单文件256 KiB、总4 MiB、输出256 KiB、最多6个并发请求；
复用中央60秒传输预算、无重试，夹读 main 和 MR 身份。失败丢弃部分库存，返回 UNKNOWN。
只输出 Application 的公开 source/destination/sync 投影和对象/blob身份，AppProject仅身份；
不输出原文、Helm values、plugin或生成器内容。AppSet、多 source、hydrator或动态来源保留未知。
`manual_sync_declared` 只描述 absent/null automated；enabled:false 等声明不代替服务端语义验证。

coverage=known_us_staging_git_declarations_only、inventory_depth=direct_children_only。
inventory_complete 仅表示这个固定入口的直接注册声明齐全；owners=[] 不表示全仓/集群无 owner。
unread_subdirectories、dynamic_ownership_unknown、root_apps_actual_source_validation 都须保留，
不能由三个文件404或 proposed-only Application 推断当前实际手动 owner。

## 静态审查与激活分离

CI 审查四个 DS allowlist 的 kind/name/namespace/固定UID及 controller actor 规则边界和反例；
无需集群凭据，也不宣称这些 UID 当前在线匹配。daemonset_identity_validation=pending_activation。
真实 root-apps 源、递归/模板归属、owner UID、完整 spec 和实际 sync、真实 DS UID/controller、
authenticated AdmissionReview/API lookup 留作激活前门禁；动态未知不能当作已验收。
先实际准入，再手动容量、bootstrap UID退出、NAT/IMDS/CSI，最后 daemon；
不以静态库存/源审通过代表 L3、容量启动、在线隔离、builder Ready 或关闭 Task3/Root1。
任一侧回退/契约缺失保持新增能力关闭，不回退旧通道安全边界或要求全仓冻结。
