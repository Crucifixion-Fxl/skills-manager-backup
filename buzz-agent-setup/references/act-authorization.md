# 通用 ACT 提案／批准／执行协议

任何会改变受保护目标或线上状态的动作都先成为精确 ACT。MR merge、GrowthBook 实验／flag、K8s／ArgoCD、生产配置、共享环境操作使用同一协议；GitLab CE approve 的额外限制见 [approval-authz.md](approval-authz.md)。

## 唯一允许的主路径

1. **Propose**：注册过的非 executor Agent 在当前 Thread 提出 `ACT-<id>`。
2. **Approve**：`target_platform` 对应的平台管理员以 allowlist 中的 Nostr pubkey，在同一 Channel、同一 Thread 发出完整批准命令。
3. **Execute**：对应职能 executor Agent 只提交 `act_id`；与 LLM 进程隔离的确定性 ACT action adapter／credential broker 独立回读并验证授权，只重放获批的精确 payload，写一次性 ledger，再把结果和验证证据回写 Git。

唯一书面例外：[repo-maintainer-agent.md](repo-maintainer-agent.md) 定义的 repo maintainer agent——只有该 repo 的 GitLab Maintainer 能点名它，点名即授权，不走 ACT；其身份校验、`head_sha` 绑定和一次性执行不放宽，且不适用于其它 executor。

人不能绕过 proposer 直接命令 executor；executor 不能调查、提案、自批，也不接受 schedule、普通 webhook、GitLab approve 事件或 DM 直接触发。Superset dashboard／chart 与 Dagster allowlist run 是业务 `-bi` 的受限直连例外，不进入 ACT；边界见 [fchac-model.md](fchac-model.md)。

## 强制执行边界：LLM 不持高影响写 token

仅在 prompt 中写“只能经 ACT”不是权限门禁。同一 Unix UID 下，0600 文件仍可被其他 Agent 读取；把平台写 token 注入 executor LLM，也意味着被注入的模型能绕过 parser 直接调用平台。

因此平台写 token 只能由独立 OS principal／容器中的确定性 action adapter／credential broker 持有。executor Agent 只传 `act_id`；broker 自行回读 Buzz 原始事件与平台当前状态，完成本页全部校验、原子占用 ledger 后执行 canonical payload。broker API 不接受任意命令、URL、替换 payload或“已经批准”标志。角色 Agent、Desk 与 executor LLM 都没有原始写 token，也不能读取 broker 的 env／secret／进程空间。

独立 ACT parser、ledger、平台 action adapter 与跨 UID／直连平台负向 E2E 未完成前，executor 必须保持禁用。单纯拆 Agent 名、分 env 文件、`env -i` 或同 UID 的 chmod 0600 都不算完成。

## ACT 最小数据契约

每份提案至少包含 `act_id`、`proposer_pubkey`、`function`、`target_platform`、一个精确 `target_resource`、一个精确 `payload`、`current_state`、`expected_state`、`impact`、`verify`、`rollback`、`expires_at` 和规范化 `payload_sha256`。

一个 ACT 只允许一个平台、一个职能、一个动作。跨平台或包含多个独立动作时拆成多份，不能用模糊的“按上述方案处理”打包授权。提案正文是批准对象；批准消息不能夹带新动作。

## 身份与上下文校验

- 按 `target_platform → platform-admin pubkey allowlist` 查批准者。只认原始事件 `pubkey`，不认显示名、自称、@ 提及或转述。
- 批准者必须在目标平台本来就拥有该管理权限；ACT 不能借 executor 把平台没有授予的权限转交给普通成员。
- Proposer 必须在注册 roster 中且 `kind != executor`；executor 不得成为 Issue 自动路由 target。
- Approval 必须与 Proposal **同 Channel、同 root Thread**，时间晚于提案、早于 `expires_at`，且 payload digest 没变。
- 批准命令必须整条锚定匹配。推荐 `/approve ACT-<id> <payload_sha256>`；可允许显式 `@executor` 前缀与整条 Markdown 加粗，但禁止正文搜索、引用、转述或后缀说明。

## 一次性 ledger

隔离 broker 在执行前以 `(executor_function, act_id)` 查 ledger；已存在即拒绝重放。executor LLM 不能读写 ledger，也不能把校验结果作为布尔值传入 broker。执行顺序：

1. 校验 proposal、approval、当前平台状态与 payload digest。
2. 以原子写或事务占用 `act_id`，记录 `in_progress`、proposal／approval event id、平台、资源和 digest。
3. broker 只调用 `target_platform` 对应 token；LLM 进程永远拿不到 token。
4. 记录平台 receipt、验证证据与 `succeeded`／`failed`；失败不能删除 ledger 条目。
5. 在原 Thread 回帖，并把 ACT、receipt、验证／回滚证据写回 Issue／MR 或版本化审计仓。

Channel 丢失时，最多损失协作速度，不能丢执行状态。ledger 与 Git 回执必须足以重建已批准和已执行事实；不要只把状态存在滚动消息或 LLM memory。

## 安全拒绝矩阵

| 输入／状态 | 必须拒绝的原因 |
|---|---|
| proposer 未注册或是 executor | 职责分离失败 |
| `function` 与接收 executor 不匹配 | 错职能，例如 Git merge 送到 BI executor |
| `target_platform` 无唯一 token 或管理员映射 | 无法确定权限域 |
| 批准者不在该平台 allowlist | 跨平台批准越权 |
| 批准者只改成白名单显示名 | 显示名可伪造 |
| Proposal／Approval 不同 Channel 或 root Thread | 跨上下文搬运授权 |
| Approval 早于 Proposal、已过期或 payload 改变 | 未批准当前动作 |
| 命令只是在引用／说明中包含 `/approve` | 引用即授权攻击 |
| `act_id` 已在 ledger 中 | 重放 |
| 资源当前状态不等于 `current_state` | 前置条件漂移，必须重新提案 |
| payload 含 wildcard、多个平台／动作或未声明参数 | 授权范围不精确 |
| 缺回滚；不可逆但未明确说明 | 恢复路径缺失 |
| schedule／webhook／人类直接命令 executor | 绕过 proposer 与平台管理员批准 |
| 角色 Agent／executor LLM 能读取写 token或绕过 broker 直连平台 | 授权门禁不在强制边界上 |

任何读取原始事件、ledger 或平台状态失败都属于**校验错误**，不能降级成“未批准”后继续，也不能猜测放行。错误码应区分：`0` 允许执行、`1` 确定拒绝、`2` 环境／网络／读取失败。

## Canvas 与负向验收

Canvas 的“谁能 trigger／approve executor”按平台写 pubkey／平台用户名；另列“谁能 break-glass 人工执行”，并明确人工高权限不自动等于 ACT approve 有效。

至少验证：非 allowlist pubkey、伪造显示名、跨 Thread、过期、digest 变化、职能不匹配、平台不匹配、重复 `act_id`、引用 `/approve`、平台状态漂移、普通 webhook 触发，均被 broker 拒绝；角色 Agent／executor LLM 读取 broker secret、读取 broker `/proc` 或直连平台也必须失败。有效执行后能从 Git 证据与 ledger 重建全过程。
