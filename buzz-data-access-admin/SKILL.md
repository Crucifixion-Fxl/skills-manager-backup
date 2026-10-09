---
name: buzz-data-access-admin
description: 为 Buzz 业务/BI Agent 执行已批准的数据访问开通、验收、续期、轮换和撤销。需要创建专用 Superset 账号、核对 IAM Role、提交 DEV/IaC Lake Formation Terraform MR 或处理飞书数据权限申请时使用。固定陈敬敏审批、李文斑执行；生成材料与真实生效分别留证，不代替生产 review/apply 门禁。
---

# Buzz 数据访问授权

## Description

申请由 Creator 经 [buzz-agent-setup](../../agent-harness/buzz-agent-setup/SKILL.md) 发起；本 Skill 由授权管理员使用。陈敬敏批准范围，李文斑负责实际开通，业务 Agent 不持本 Skill 的管理凭据。事实入口是 [Issue #200](https://gitlab.addx.ai/engineering/skills/-/issues/200)。

随包 [data_access.py](scripts/data_access.py) 提供身份校验、提单、查询和批准范围的 Terraform 材料生成。实际资源操作沿平台现有管理员入口和独立审核执行，读 [执行手册](references/provisioning.md)。脚本返回 `prepared / provisioned=false`；它不执行 apply，也不签发“已开通”证明。

## Rules

1. 从 Creator 的 0600 申请 journal 取得 instance_code；它是定位信息，不能作为授权。每个有副作用的步骤前，用同一应用、真实管理员个人身份回读审批定义和实例，确认当前 APPROVED、未撤回、实际 Creator、陈敬敏在预期节点真实 APPROVED、表单完整内容、版本及起止时刻。聊天、截图、回调及手工 JSON 均不代替回读。
2. 使用已验证的本地 lark-cli runtime/entry/profile；本环境固定 `jchen-personal`、`cli_a940faa4ec381bc4`、`--as user`。其他管理员安装前由其宿主建立同应用的本人 profile 与本地 `identity.json`（profile/expected_open_id，宿主安装路径不同时可填 node_path/cli_entry），每条命令传 `--identity-config` 并核对实时用户；禁止复制陈敬敏登录态或为李文斑冒名。默认配置只代表陈敬敏；当前主机不改为李文斑或其他 Creator 来重试。只有在其本人已批准的宿主配置上使用本人身份。
3. 固定飞书原生模板 `1882E07A-49A1-4885-82E4-2C6129CB37D2`，陈敬敏节点 custom_id=`data_access_approval`。结束抄送李文斑仅是通知，不表示批准或执行成功；当前模板可见范围为这两人，扩大 Creator 可见范围需模板管理员操作并重新验收。
4. 输入业务数据范围必须能映射到真实资源；描述未解析时不生成 grant。管理人先核对 DataHub、Glue、LF tags、产品隔离，再维护本地只读 catalog。catalog 是管理员的已核实资源清单，不是申请者提供的授权。变更范围、用途或续期重新申请。
5. 一 Agent 一 `agent_` 专用 Superset 本地账号，与 `/employee_role/<username>` 的实际映射一致。Gamma＋SQL Lab 是基线；本业务 Chart/Dashboard 可写，Dataset/Database/Connection 只读；不得授 Admin、all_database_access、数据写入、转授、LF 管理或共享 superset/员工岗位权限。
6. 首次登录前解决 IAM 角色创建与默认 LF grant 的 owner：`DATA/superset` 的登录钩子可能自动创建并授权。Terraform 与钩子不能重复拥有资源；更宽默认 grant 未消除时不开通。角色预建、trust、IAM policy、资源 import、state/backend 的方案由数据平台 owner 认领。
7. Terraform 权限 SSOT 为 `DEV/IaC`，US 在 `terraform/a4x/aws/us/tech-service-data/us-east-1/lakeformation/permissions/`；不是 dbt repo。CN/EU 使用对应本区域账号、partition、backend 和 provider；不复制 US 值。脚本有意不生成 backend/IAM/provider，避免猜 state 边界或绑定错误账号。
8. 显式数据库 DESCRIBE＋指定列 SELECT；有产品行隔离时用 LF data cells filter。列/行过滤 grant 不再额外给整表 SELECT/DESCRIBE；管理员核查 implicit/admin/IAMAllowedPrincipals/其他授权。Dataset RLS 不能证明 SQL Lab 原始 SQL 隔离。源码与格式依据见 [手册](references/provisioning.md)。
9. 权限写入须 MR、plan、独立 review、既有生产 apply Gate。合并、apply、生效、验收是四件事。操作失败记录失败步骤、owner 与恢复条件；重试先读已有账号、Role、执行工单、MR 和 state，禁止重复创建。
10. 密码、Cookie、JWT、AWS 临时凭据只在隔离管理员/Agent 环境或受控交付渠道；不进 argv、审批、Issue、MR、prompt、回执。`SUPERSET_EXPECTED_USER`、真实查询身份与启动器/login shell 环境均需验证。生产凭据由运维经 Vault 注入。
11. journal、准备材料和执行工单不是持续真实状态。有效期到达必须禁用账号/撤权/处理会话并验收；本 Skill 不部署常驻到期服务，不承诺自动回收。执行工单设置到期 owner/提醒；未落实回收调度不开通。

## 使用

申请 journal 由 Creator 安全交给管理员，放在本机私有目录。先读 [申请契约](../../agent-harness/buzz-agent-setup/references/data-access.md)，再由管理员维护 catalog；[示例](references/catalog.example.json) 只用于测试，不能作为生产资源证明。

```bash
python3 scripts/data_access.py status --state /private/request-journal.json
python3 scripts/data_access.py prepare --state /private/request-journal.json \
  --catalog /private/verified-catalog.json --out /private/preparation
```

批准后经管理员授权，可创建/复用 DEV/IaC 执行工单并指派 wli1。宿主必须显式注入已获准的 `GITLAB_HOST=gitlab.addx.ai`，glab 使用该主机本人的既有认证；没有 host 时停止，不回退其他 GitLab：

```bash
python3 scripts/data_access.py handoff --state /private/request-journal.json \
  --confirm-request <已获准派单的request_digest>
```

脚本核对 GitLab 本人身份与实时飞书管理员对应，再搜索包含已关闭工单的记录。新建前在 DEV/IaC 创建 `buzz-data-access-reservations/<marker的SHA256>` 分支，GitLab 的分支名唯一性保证跨主机只有一个创建者。该分支只指向既有默认分支 commit，不改文件；需事先获准仓库分支写入权限。没有权限就停止。分支已存在、创建结果未知、审批随后失效或 Issue 写入失败时均停止新建，由管理员核对分支和工单、记录恢复决定；不得删分支或换 marker 自动重试。已有唯一工单可直接回读复用。保留 reservation 作幂等记录，不自动清理；生产 rollout 前需验证 GitLab 分支唯一性和权限。创建后回读正文与 assignee；结果存到 journal。

以上从本 Skill 目录执行。`prepare` 可由陈敬敏预审，实际执行 owner 为李文斑。运行时每次读取真实审批；文件名和 Terraform 地址稳定，同范围续期不新增 grant。已有输出不同则拒绝覆盖，旧批准/已失效授权不能复用。

接着按 [执行手册](references/provisioning.md) 建执行记录、创建/核对专用账号及 IAM Role，提交 Terraform MR、apply、用 Agent 身份验收，再安全交付和回填。必须将实际平台证据与申请 revision 绑定后才能说“已开通”。
