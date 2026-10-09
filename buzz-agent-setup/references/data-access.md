# Buzz Agent 数据访问申请

需要 Superset/Lake Formation（特别是 BI）的 Agent 首先走此流程。Creator 为真实申请人；陈敬敏批准，李文斑执行。批准与开通分开；未验收不向 Agent 提供可用状态或借用个人账号。

## 采访、解析与摘要

一次收集以下业务字段；Creator 由 CLI 的本人登录态读取，不能在 request JSON 指定或改填别人的名字。所有内容禁止密码/Token/Cookie。模板名 `Buzz Agent 数据访问申请`，approval_code=`1882E07A-49A1-4885-82E4-2C6129CB37D2`。

| JSON 字段 | 输入 |
|---|---|
| request_id | 稳定申请标识；新用途/范围/续期创建新版本和 journal |
| agent_id / agent_name_type | Agent ID、名称和类型 |
| business_owner | 业务负责人 |
| business_line_issue | 业务线与受控业务 Issue 链接 |
| background | 业务背景、分析问题与使用场景 |
| query_frequency | 查询频率 |
| audience_destination | 结果受众与允许输出目的地 |
| region_environment | 例如 us-east-1/prod；不猜其他区域账号 |
| data_scope | 下面结构化库表列/产品行范围，或需管理员解析的文字描述 |
| pii_reason | 敏感性/PII 需求及理由；无需求明确填写无 |
| validity | start/end，含时区的 ISO 8601 时间；不支持永久 |
| account_name | 一 Agent 一 `agent_` 前缀专用账号，非个人/共享/Admin |

`data_scope` 结构：region、environment、business、pii、layer、resources。每个 resource 的 database/table 为明确名称，columns 为明确列数组，row_filter 是管理员核实的 LF PartiQL 表达式；null 仅适用于允许全行的独立表。示例见 [data-access-request.example.json](data-access-request.example.json)，所有 fixture 资源均为测试数据，不能直接用于真实开通。

Creator 无法确认资源时可用文字描述提单，管理员将其标为待补充；明确库表/产品/PII 范围后重新提交并审批，不能由管理员把模糊文字自动变成已批准 grant。metadata 不是实际隔离证明；每项需管理员资源清单核对。平台无 Golf/Naturehood business tag 时不推断其产品访问。

## 预览、确认、提单、回读

在宿主已验证的同应用本人 lark-cli profile 中执行。当前开发机默认 `jchen-personal`、App `cli_a940faa4ec381bc4`、陈敬敏。其他 Creator 要先由其宿主配置同应用的本人 profile，建立只含 `profile`、`expected_open_id` 的本地配置文件，并对每条命令传 `--identity-config /private/identity.json`；宿主安装路径不同可额外填 `node_path`、`cli_entry`（安装包真实绝对路径，不比较版本/hash）。应用 ID 始终固定；配置文件不是业务 request 的一部分。当前主机继续仅使用已批准的陈敬敏 profile；禁止复制个人登录态、冒名或自动切 app/bot。模板当前仅陈敬敏/李文斑可见；其他 Creator 需要管理员扩大发起可见范围并验收后才能用。

从本 Skill 目录执行，request/journal 放在本机已存在的私有目录，journal 0600：

```bash
python3 references/scripts/buzz_data_access.py preview \
  --request /private/request.json --state /private/request-journal.json
# 展示全字段和 request_digest，Creator 已明确授权此精确摘要后再执行：
python3 references/scripts/buzz_data_access.py submit \
  --request /private/request.json --state /private/request-journal.json \
  --confirm-request <明确获准的request_digest>
python3 references/scripts/buzz_data_access.py status --state /private/request-journal.json
```

脚本重新读真实模板，按 custom_id 解析当前 widget id，不写死控件 ID。原生提单 `--as user`、固定 app/profile；它不传 node_approver_list。审批人固定，用户不能换人或 Agent 自动代批。

返回保存 request/template digest、完整提交表单、uuid、instance_code。提单前写 UUID journal，完成后先存 instance_code 再回读；重试沿用同一文件和 UUID。明确的本地缺 scope/参数预检失败后可原请求重试；网络响应丢失时脚本不再盲目 create，先按模板、Creator、提交时间和完整表单读取真实已发起实例（最多40条/45秒），恰好一个匹配才恢复 instance_code；零个/多个/分页不完整停止并交 owner 核对。也可显式执行 `recover --state /private/request-journal.json`，禁止删除 journal 来重建。不同请求/模板 revision 拒绝复用 journal；重复提交回读已有实例，拒绝/撤回的旧申请不重建。缺 scope/模板不可见/身份冲突停止，给出错误类别、owner 与恢复条件，不借个人/admin 账号。

`APPROVED` 只输出 approved_waiting_execution；它不表示已开通。按 instance_code 到 [buzz-data-access-admin](../../../security/buzz-data-access-admin/SKILL.md) 创建或复用唯一执行工单并交给李文斑。结束 CC、重复通知或用户贴来的 approved JSON 只用于定位真实审批，不能派 grant。Creator 从关联工单查询 executing/pending_validation/active/failed/revoked；审批撤回、过期或扩权先停并走新审批/回收。

## 使用边界

CLI 只完成申请、查审批及材料准备，账号/Role、MR/apply/验收/回收由授权 Skill 执行并留真实证据；不启动常驻服务。当前本机 user 还缺 `approval:approval:read`，真实本人提单与权限验收需授权及测试资源，离线测试不替代 live 结果。

上线还需：扩大实际 Creator 的模板可见范围；验证本人 user 的 `approval:approval:read`、`approval:instance:read/write`；选择可安全测试的业务范围；由李文斑核对生产 Superset hooks/IaC state/apply owner，并完成一次批准、开通、正负向验收、撤销的真实闭环。
