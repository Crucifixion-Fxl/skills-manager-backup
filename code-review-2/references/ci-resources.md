# 受限 CI 资源声明

仅用于回答本次变更的具体资源预算问题，不作为读取 CI 全文的入口。
Root Issue: https://gitlab.addx.ai/DATA/troubleshooting/-/issues/13
Work Item: https://gitlab.addx.ai/engineering/skills/-/issues/312

本次是契约准备；可信模板实现由 ci-templates#85 配套交付。合并此 Skill 不代表
运行时已启用，必须按下文实际协商与合并后的 CI 回执判断。

## 独立协商

可信模板、`source-context-contract.json`、`ci-resources-contract.json` 必须共同协商。
先核对 meta 与 `source-context.json` 的项目、base/head；capabilities 必须同时包含
`fixed-source-context/v1` 和 `ci-resources/v1`，实际工具必须暴露 `ci-resources`。
旧模板+新Skill、新模板+旧Skill均不授权；diff-context 和 K8s 权限不替代该契约。
缺能力、缺字段、未知版本或 `CAPABILITY_UNAVAILABLE` 时，不换用 shell/API/Git/
网络、原始 YAML 或任意路径补取。缺模块失败不允许退回全文。

```json
{"operation":"ci-resources","source":"root","revision":"head","path":".gitlab-ci/jobs/sonarqube-scan.yml"}
```

唯一参数为 operation/source/revision/path；无 query、symbol、窗口或分页参数。
仅 root、固定 `revision=base|head`；路径须属于当前完整 MR 文件清单，且是
`.gitlab-ci.yml`、`.gitlab-ci.yaml` 或 `.gitlab-ci/jobs/` 下文件名仅包含
字母、数字、下划线、短横线的直接 `.yml/.yaml` 子文件。不跟随 include、gitlink、
符号链接；不执行配置、构建或扫描；不扩大原 file/search/config 的权限。

## 可以回答的问题

结果 `coverage=static_ci_resource_declarations_only`，包含独立的 global_resources
与 jobs；Job 名和镜像只返回 SHA256 摘要，附固定版本和 blob 身份。
仅以下变量返回 `{status:literal,mib:整数}`：

- KUBERNETES_MEMORY_REQUEST / KUBERNETES_MEMORY_LIMIT：正整数 Mi/Gi，转换为 MiB。
- SONAR_SCANNER_JAVA_OPTS：单一 `-Xmx` 正整数 m/M/g/G；不接受其他 JVM 参数。

整数范围1至1048576 MiB。不存在为 absent；动态、表达式、其他格式或越界为 unknown。
不返回脚本、变量其他值、Job 名、注释、镜像明文或任意 YAML 标量。
仅支持单文档，manifest限制为 `max_ci_resource_documents=1`；`spec:inputs` 等多文档
返回 unknown/CI_RESOURCES_UNSUPPORTED_DOCUMENTS。这只是工具格式不支持，不证明
CI非法或安全性；不改写原CI来迎合工具，不换路径/helper绕过。必要证据仍不足时
保持incomplete，非必要取证只记录限制。
支持范围内的整份输入256KiB、深度32、节点16384、Job128；输出64KiB；沿用16版本/8MiB、
32个不同请求窗口预算；重复同一窗口不增加计数。解析异常、重复键、tag、alias/anchor、merge均 fail closed，
不回传 parser 错误文本。不返回 raw 窗口，审计日志不保存投影正文。

## 不可以推导的结论

`effective_configuration_resolved` 始终 false；这些是静态声明，不能作为实际生效值。
include、extends/inherit、default、rules 的存在保留为 unknown_reasons；根级旧式
image/services/cache/before_script/after_script不当作Job，其内容隐藏并标记
legacy_global_defaults_unresolved。平台变量
优先级、Scanner CLI 版本、真实 Pod 预算均不能由投影证明。global 与 job 局部值
不自动合并。缺必要运行证据仍 incomplete，不以“有投影”强制评分或放行。

先审完整 diff，再仅对有因果关系的文件取证；没有资源问题无需枚举所有 Job。
本契约不改变准入、超时、allow_failure 或 Quality Gate；不证明旧 watchdog 根因
已解决，也不替代新 Sonar Job 的4Gi/完整分析验收。
