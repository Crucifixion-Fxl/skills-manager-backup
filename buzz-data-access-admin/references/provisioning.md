# 管理员执行手册

## 事实来源与操作边界

- 飞书模板已 ACTIVE；固定陈敬敏审批、结束抄送李文斑。approver open_id=`ou_755158d120e03b0c18dd4a9334bc3aad`，executor open_id=`ou_6d7b771147382dd4baaee27ebba8487f`，GitLab executor=`wli1`（216）。ID 是定位信息；每次仍核对本应用的真实用户。
- [DEV/IaC](https://gitlab.addx.ai/DEV/IaC)（92，master）管理 LF Terraform。现有 employees backend key 还使用旧 tech-service 路径，不能根据目录猜 backend。新 agents state 建议与 employees 分离，但 owner 与 backend 未核实前不创建。
- [Athena mutator](https://gitlab.addx.ai/DATA/superset/-/blob/master/superset/extensions/awsathena_extend.py) 用当前用户名构造角色 ARN。[登录钩子](https://gitlab.addx.ai/DATA/superset/-/blob/master/superset/extensions/user_login_extend.py) 可能创建 Role/附加 policy/默认 LF grant；源码不等于当前生产配置，必须核对部署 revision 与配置。
- [dbt 工具](https://gitlab.addx.ai/DATA/dbt/-/tree/master/utils/aws) 有 LF 辅助函数，但不替代 Terraform SSOT。
- 显式列 SELECT、DB DESCRIBE 和 data cells filter 的资源形状依据 [AWS provider v5 文档](https://github.com/hashicorp/terraform-provider-aws/blob/v5.100.0/website/docs/r/lakeformation_permissions.html.markdown) 与 [过滤器文档](https://github.com/hashicorp/terraform-provider-aws/blob/v5.100.0/website/docs/r/lakeformation_data_cells_filter.html.markdown)。`IAMAllowedPrincipals` 或 implicit/admin grant 可能使过滤失效；用 [AWS filtering](https://docs.aws.amazon.com/lake-formation/latest/dg/data-filtering.html) 和真实负向查询检验。生成材料需在目标仓 provider lock 下 validate/plan，不能用 JSON 格式通过替代 provider/部署验证。

## 1. 回读批准与创建执行记录

1. 取得 journal，执行 `status`；管理员核对批准模板、Creator、表单范围、revision、有效期及真实审批人。journal 与当前模板不同、撤回、拒绝、到期、范围不明时停止，给 Creator 明确恢复条件。
2. 结束 CC / 重复事件只触发此回读，不能直接执行。API 缺 scope 或模板不可见时使用同应用本人授权；不换身份重试。李文斑不能使用陈敬敏 profile 冒名执行。
3. 经批准派单授权，管理员运行 `handoff --state <journal> --confirm-request <digest>`，核对固定 GitLab host 与本人身份，在 DEV/IaC（92）按 marker `buzz-data-access-execution:v1:<instance_code>:<request_digest>` 搜索全部 opened/closed 工单。恰好一条复用，多条停止去重；零条先在仓库创建 `buzz-data-access-reservations/<marker的SHA256>` 唯一分支，指向现有默认分支 commit。只有原子创建成功者可创建工单并指派 wli1。此步骤需获准分支写入权限；不修改文件，不自动清理 reservation。分支 POST 和 Issue POST 各自前立即回读飞书并检查有效期。已存在 reservation、未知写结果或失败一律先人工核对工单和 reservation，记录恢复决定，禁止删除 reservation/换 marker 盲目重试。成功回读正文、assignee并存 journal。上线前验证 GitLab 分支唯一性、执行者权限及仓库 CI/webhook 对 reservation 的影响；接口依据 [GitLab Branches API](https://docs.gitlab.com/api/branches/#create-repository-branch)。
4. 执行工单正文包含审批链接/instance_code、Agent、账号、principal、用途/受众、精确数据范围、起止时间、request/template/catalog digest、当前 owner、状态、失败恢复条件。无 secret。Creator 进度回此工单，结束 CC 本身不承接状态。

状态分开记录：draft → approval_pending → approved_waiting_execution → executing → pending_validation → active；拒绝/撤回为 not_authorized，失败为 failed，完成撤权验收才是 revoked。过期先 expired/revocation_required，完成撤权才是 revoked。回调不能跳过 executing/pending_validation。

## 2. 资源与登录钩子预检

使用 Li 本人管理员会话检查当前 Superset 版本和实际 OpenAPI/UI 的用户/角色入口；不要假设上游 Superset 支持固定用户创建 REST 路由，不用 `fab create-admin` 给 Agent 建管理员。

- 按精确用户名查现有用户、Agent owner、角色及所有附加权限。已有同名不同 owner 停止；同 owner 复用，不创建重复用户。账号先 inactive；开通前也不让 Agent 登录触发默认授权。
- 在生产 revision 核对 AWS_ACCOUNT_ID、AWS_PARTITION、AWS_ROLE_PATH、AWS_POLICY_ARN、AWS_LF_EXPRESSIONS。不得打印密钥。核对新用户 default roles、查询身份/STS 缓存与 hooks 行为。
- 查询 IAM get-role、信任、附加/inline policy、LF list-permissions 和 DataLake admins；核对角色不是 employee 岗位或共享 superset role。检查 S3/Athena/Glue/workgroup/result bucket 的最小配套访问以及无直接 S3/其他入口绕过 LF 的路径。
- owner 决定 Role 预建和钩子不再额外 grant 的路径；已有资源 import 前查 state，单 owner 接管。先保证不会获得更宽默认权限，再让专用账号进入可登录状态。不能仅“角色已存在”推断所有 hook 已禁用。
- 给账号绑定经回读验证的最小权限：Gamma、SQL Lab、指定数据库/数据集读取与本业务 Chart/Dashboard 写；禁止修改 Dataset/Database/Connection。Gamma 默认权限随版本变化，必须检查实际 permission views/role union，必要时定制受限角色而不是直接套名。

## 3. 从已批准范围准备 Terraform / MR

管理员通过 DataHub/Glue/LF 检查真实库表列、env/business/pii/layer 和产品边界，形成仅管理员持有的 catalog。普通 request JSON 不允许管理员 catalog 字段；catalog 不能凭申请描述生成。文字描述或 pii/产品范围未能明确时先帮助 Creator 修订并重新审批。

运行 `prepare`，查看 permissions.tf.json 和 receipt.json。显式列授权使用 SELECT，DB 使用 DESCRIBE；有行过滤时仅 grant data_cells_filter 的 SELECT。新的表、列、PII 或产品不能自动扩大。metadata 在 catalog 中精确匹配，脚本不创建 LF tag、更不把没有产品 tag 的全表当隔离。

- 将生成资源放入 IaC owner 明确的新 agents 配置/现有 state 入口；文件按 account_name 稳定命名。不得把临时 bundle/backend/plan 二进制凭据直接提交仓库。
- 续期/缩权替换同账号完整资源集；删除不再批准的资源。不要以“新增一份”叠加旧 grants；地址相同仍要核对已有 state。以申请版本更新 trace 注释/MR，保留原证据。
- 目标仓真实 backend/provider/account/region 下跑 fmt、validate、refresh/plan，附可分享的脱敏摘要、完整 plan 的受控存储链接与 digest。plan 只能包含预期 Agent 改动；原有员工/shared role变化停止。
- 查找带上述 execution marker 的全部 MR；同 branch/version 有 MR 则更新，已 merged 的版本先读 apply/状态后决定新 revision，禁止重复 MR。正文放审批/执行工单、principal、资源集、plan、角色/state owner、到期回收、回滚及剩余验证。
- 用 gitlab-mr 流程独立 review，保留目标仓 review/apply Gate。仅获得开通授权不等于可绕过生产 apply；DEV/IaC 的实际 apply 机制仍由 owner 核实。

## 4. Apply 与 Agent 身份验收

每次有副作用前重新读取飞书，验证 revision/范围/有效期仍适用；长 review 等待不复用旧审批快照。记录 MR、merge SHA、plan digest、apply SHA/job/operator/时间与实际 LF grants。apply 失败保留 inactive 账号/失败状态，先核对资源与 state 再恢复。

在 Agent 专属隔离运行环境用 superset Skill 的 login2 会话契约登录；密码由 Vault/受控渠道注入，不由脚本 argv 或复制个人 shell profile 传递。联网前比较 SUPERSET_USER/SUPERSET_USERNAME 与 SUPERSET_EXPECTED_USER，再查询 me/审计和实际 Athena execution/STS principal。

| 正负向验收 | 成功标准 |
|---|---|
| 批准库表列/产品聚合读取 | 查询实际成功，正确 Agent 身份，结果仅聚合，无明细外发 |
| 未批准库表/列/PII | 服务端明确拒绝；超时、空结果或 UI 隐藏不算拒绝 |
| 共用表 SQL Lab 产品行隔离 | 已知跨产品 seed 原始 SQL 验证过滤，受控 admin 基线证明 seed 实际存在；空表不算通过 |
| 数据写入 | 在 owner 指定无影响测试资源验证写被拒，禁止对真实业务执行破坏性 SQL 来测试 |
| Chart/Dashboard | 能维护本业务测试资产；跨业务资产被拒；清理测试资产 |
| Dataset/Database/Connection | 修改被权限层拒绝；不对真实生产配置试危险修改 |
| 首次登录权限 | hook 前后 grant diff 无额外访问；总 role/grants 与批准一致 |
| 凭据/运行环境 | 0600 env 或 Vault delivery，登录 shell 不重注入个人凭据，无其他 Agent/个人身份回退 |

测试资源或禁止查询无法安全设计时保持 pending_validation，写 owner 与待补证据；不编造 active。安全交付后按 Agent 身份重复关键查询。执行记录最终包含：审批/Creator/Agent/账号/principal、request/template/catalog digest、MR/merge SHA、apply/plan、平台只读回查、正负向结果/审计、凭据交付引用、有效期与回收 owner。不得把手工布尔 JSON 当实时授权或验收证据。

## 5. 到期、撤销、轮换与失败恢复

- 开通时在真实执行系统安排到期提醒/任务给李文斑，记录调度回读。没有可靠调度时保持未开通；此 Skill 无后台守护或自动到期权限。
- 撤销先禁用 Superset 用户/撤除授权，再通过独立 review/apply 删除该 Agent 的 LF grant/filter，处理 IAM trust/policy 的 Agent 专属依赖，不能影响其他用户。回读实际权限；撤销会话并核对 STS TTL/cache/result bucket 等残余，等待必要失效窗口后验证旧凭据也无法读。
- 到期后禁止恢复旧 journal 或旧 approved 记录；续期重新批准，复用账号但换完整授权 revision。需临时恢复时同样新审批。
- 轮换通过 Secret owner 的渠道；禁用旧密码/会话并验证拒绝，新凭据正确身份查询成功。生产访问凭据轮换遵守安全合规（最长90天）；不贴密码到 Issue/聊天。
- 重试先查实际账号、Role、LF/state、MR/apply 和 journal。不能靠删除 journal 重建来绕过去重或扩大权限。失败写 `failed`、失败步骤、实际已创建对象、owner、恢复条件；已产生的多余 grant 走撤销，不能仅留 TODO。
