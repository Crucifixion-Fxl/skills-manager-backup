# 授权层：谁的 approve 算数

配“开发 Agent + dev executor + 平台管理员”职责分离时的 GitLab 授权细节。通用规则在 [act-authorization.md](act-authorization.md)，本文保留 CE 平台证据与 MR 专属门禁。

> 本页只说明 GitLab CE approve／merge 的平台特例。merge 必须包装成 `ACT-MERGE-<id>`，并遵守 [通用 ACT 协议](act-authorization.md) 的同 Channel／同 Thread、平台管理员 pubkey、精确 payload、过期、一次性 ledger 与拒绝矩阵；MR IID 与 commit SHA 仍是 canonical payload 的必填字段，但批准命令只绑定 ACT ID＋完整 payload digest，不再使用旧的 MR IID＋SHA 命令特例。

## 一、GitLab CE 的 approve 到底是什么（实测证据）

**结论先行：CE 的 approve 只是一个信息性标记，平台既不校验谁批的、也不会因为没批就拦住合并。**

实测于本实例，2026-09：

```bash
curl -s --header "PRIVATE-TOKEN: $TOKEN" "$GITLAB/api/v4/version"
# {"version":"18.0.0","revision":"...","enterprise":false}
#                                        ^^^^^^^^^^^^^^^^ CE，不是 EE

curl -s -w "HTTP %{http_code}\n" --header "PRIVATE-TOKEN: $TOKEN" \
     "$GITLAB/api/v4/projects/<id>/approval_rules"
# HTTP 404  {"error":"404 Not Found"}          ← approval rules 是 Premium 功能

curl -s -w "HTTP %{http_code}\n" --header "PRIVATE-TOKEN: $TOKEN" \
     "$GITLAB/api/v4/projects/<id>/approvals"
# HTTP 404  {"error":"404 Not Found"}          ← 连项目级 approvals 配置都没有

curl -s --header "PRIVATE-TOKEN: $DEVELOPER_BOT_TOKEN" \
     "$GITLAB/api/v4/projects/<id>/merge_requests/<iid>/approvals"
# user_can_approve: True | approved_by: []     ← Developer(30) 的 bot token 就能批
```

由此三条，都反直觉：

1. **Developer(30) 就能点 Approve** —— approve 不是一个权限档位。大仓里通常几十号人都是 Developer。
2. **没有 `approvals_required`** —— 没人批也照样能合。
3. **平台不校验谁批的**，也不阻止作者批自己的 MR。

### 推论：白名单是唯一的闸，不是「额外一道闸」

- **白名单绝不能写成「Developer 以上都算」。** 开 MR 的那个 agent 自己的 bot token 通常正好是
  Developer(30)，它能给自己开的 MR 点 Approve —— 名单一放宽它就自批自合，职责分离当场作废。
  名单只能是**逐个写死的身份**。
- **白名单成员应当在平台上本来就有目标分支的 merge 权限**（`main` 一般是 Maintainer(40)+）。
  否则等于用 agent 的授权层，把平台不肯给的合并权凭空发给了一个 Developer。

### 推论：approve 不会因新推送失效

CE 既然完全不管 approve，也就**不会**在分支被推了新 commit 之后让旧 approve 失效。
「先拿到批准、再往分支推代码」是这套流程里最实际的一条绕过路径，**只能由隔离 ACT broker 在执行时比
approve 时间与最新 commit 时间**来堵。平台帮不上忙。

### 频道 Canvas 里要写成三个边界

「谁能批准」「谁负责执行」和「谁能紧急 break-glass」是三个集合，混写会制造常规旁路：

| 边界 | 谁在里面 | 用途 |
|---|---|---|
| **谁的 approve 算数** | `-dev-executor` 的 `target_platform=gitlab` 管理员 allowlist（Nostr pubkey＋GitLab 用户名）中逐个写死的身份 | 审查精确 `ACT-MERGE` 后批准；批准者不执行 |
| **谁负责执行** | 对应业务的单一 `<business>-dev-executor` ＋隔离 GitLab ACT broker | executor 只提交同 Thread 已获批 `act_id`；broker 校验 HEAD／digest／时效／ledger 后 merge |
| **谁能 break-glass** | 平台上对目标分支有 Maintainer(40)+ 的指定应急人员 | 只限紧急恢复；不得作为日常人工合并旁路，事后必须补 ACT、原因、平台 receipt 与验证证据 |

三者都要显式标注「**平台上能手工 merge ≠ ACT approve 有效，也不是日常绕过 executor 的许可**」。用户若找了不在 allowlist 的人点 GitLab Approve，`-dev-executor` 必须拒绝；被显式问到某个 MR 时，应说明卡在哪条闸，但不能泄露 allowlist。非紧急场景即使 Maintainer 技术上能点 Merge，也必须回到 propose → approve → executor 主路径。

## 二、`/approve` 的合法发起人：完整落地

### 为什么不能采信显示名

团队 Channel 通常按 [runtime-setup.md](runtime-setup.md) 设成 `respond_to=anyone`，于是任何人都能发言、都能把昵称改成
任何人、都能 @ 任何人；而 harness 递给 agent 的是**显示名**。`/approve` 覆盖的又恰恰是最狠的
动作（删 skill、改 CI 配置、改仓级约束、force push）。

对照 `sre-agent`：它把审批身份钉死在 `FEISHU_APPROVER_OPEN_ID`，不认「谁在群里说了话」。
Buzz 里的等价物是消息的 Nostr `pubkey` 字段 —— 它由发送者私钥签名，是一条消息里唯一伪造不了的东西。

### 步骤

1. env 里给一个逗号分隔的 hex 列表，例如 `<AGENT>_APPROVER_PUBKEYS=<hex1>,<hex2>`。
   **写 pubkey，不写名字。**
2. 提示词要求 agent 收到 `/approve` 后回读原始事件、逐字比对 `pubkey` 字段，
   **不采信 harness 递来的显示名、@ 提及、或消息正文里的自称**：

   ```bash
   /home/jchen/.local/opt/buzz-0.5.23/usr/bin/buzz messages get --channel <CH> --limit 20
   # 返回的每条消息都带 pubkey 字段；禁止使用 ~/.local/bin/buzz owner wrapper
   ```

3. 比对不上就当没看见：回帖说明「`/approve` 需要来自授权身份，当前这条不是」并指出提案仍在等待。
   **拒绝话术里不要列出白名单有谁** —— 那等于告诉对方该冒充谁。

### 配套五条，缺一条就留下一条绕过路径

| 约束 | 防的是 |
|---|---|
| 一个提案编号（`ACT-MERGE-<id>`）**只能用一次**；执行完回帖「已执行 + 实际做了什么」，同号第二次出现按重放拒绝 | 翻旧帖重放一条历史 `/approve` |
| `/approve` 必须在**同一频道、同一 thread** 内 | 拿别的频道 / DM / 别的 thread 里的批准来顶 |
| 提案编号**写进 MR 描述** | 下游（`-dev-executor` 或审计人）只看得到 diff，看不出走没走审批；不留痕就无法回查 |
| **`/approve ACT-MERGE-<id> <payload_sha256>` 必须是整条消息的全部**（锚定全串匹配，不是在正文里搜） | **引用即授权** —— 见下 |
| digest 必须由含 `merge_request_iid` 与完整 `commit_sha` 的 canonical payload 计算，且批准有时效（如 24h） | 批到一个自己没看过的版本；分支后续被改 |

`ACT-MERGE` 的 canonical payload 至少包含：`version`、`act_id`、`function=dev`、`target_platform=gitlab`、`project_id`、`merge_request_iid`、完整 `commit_sha`、`merge_method`、`delete_source_branch`、`current_state`、`expected_state`、`expires_at`。按 UTF-8 JSON、键排序、无多余空白序列化，再计算完整 64 位小写 `payload_sha256`。任何字段变化都必须生成新 digest 并重新批准。

#### 「引用即授权」：真实风险不是伪造，是诱导

pubkey 伪造不了，所以攻击面不在签名，在**语义**。旧实现用 `re.search` 在整条正文里找
`/approve`，于是**引用一条 `/approve` 等同于发出一条 `/approve`**：

- 真实踩过：另一个 Agent 的唤醒消息里为了说明用法**引用了** `/approve ACT-MERGE-deadbeef <digest>`，
  被审批脚本当成一条真批准。发消息的人完全没有批准的意思。
- 更难防的版本是诱导：让白名单里的人「帮忙催一下 / 转述一下」，
  他就在**没看过那个 SHA** 的情况下完成了授权，而且事后看聊天记录一切正常。

```python
# ✅ 全串匹配，允许 @<agent> 前缀，其余一律不认
pat = re.compile(
    r"^\s*(?:@<agent>\s+)?/approve\s+"
    r"(ACT-MERGE-[0-9a-fA-F]{8,64})\s+([0-9a-f]{64})\s*$"
)
hit = pat.match((m.get("content") or "").strip().strip("*").strip())
```

> `.strip("*")` 是因为有人会把命令加粗成 `**/approve ...**`；除此之外**不要再放宽**。
> 每放宽一次，就多一种「不知情地完成授权」的路径。

#### 为什么 IID／SHA 留在 payload，而批准命令改绑 digest

批的是**这个 MR 的这个 commit 与这一组精确 merge 参数**。executor 只提交 ACT ID；隔离 broker 自行读取提案，重算 canonical payload digest，校验 `merge_request_iid` 指向目标 MR、`commit_sha` 等于当前 HEAD，再与批准消息中的 digest 比对。分支、merge method、删除源分支选项或任何前置状态一变，旧批准都失效。这既保留 IID／SHA 门禁，也让 GitLab merge 与其他 ACT 使用同一授权语法。

审批脚本的退出码要分三档，**「读频道失败」绝不能当成「没批准」**：

```
0 = 找到合法批准（stdout 打 JSON 凭据，贴进 MR 评论留痕）
1 = 没找到 / 不合法
2 = 环境或网络错误 —— 不是拒绝，调用方必须区别对待
```

比对 pubkey、ACT ID、canonical payload digest、IID 与当前 commit SHA 是确定性动作，**交给脚本不交给 LLM**：LLM 既容易看错，也容易被频道里的话术绕过。

### GitLab native Approve 不是执行授权

唯一能让 `-dev-executor` 提交 merge `act_id`、再由隔离 broker 执行的授权，是同一 Buzz Channel、同一 root Thread 中，来自 `target_platform=gitlab` 管理员 pubkey allowlist 的完整 ACT approval。GitLab UI／API 的 native Approve 只作为 code review 与平台证据；策略可以把它设为额外 gate，并核对其 GitLab 用户名，但它永远不能替代 Buzz ACT approval，也不能单独 trigger executor。两套身份映射应由同一平台管理员维护并显式审计漂移，不能把任一侧的“更宽”解释为授权放宽。

## 当前落地状态

本页定义的是必须实现的确定性协议与拒绝条件；当前 `buzz-agent-setup` 候选只提供文档契约测试，尚未包含可部署的 ACT parser、一次性 ledger 与 GitLab merge adapter。它们在真实正负向 E2E 完成前，所有 executor 必须保持禁用；不能把“文案校验通过”写成 ACT 门禁已经上线或 L3/L4 已通过。
