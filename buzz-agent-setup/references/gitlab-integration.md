# Buzz 拉取 GitLab 事件 → Channel 播报

> **已被取代**：GitLab 事件播报已并入 [gitlab-buzz-sync.md](gitlab-buzz-sync.md) 的全量变更同步（顶层即时与摘要、Issue/MR Thread、events 去重）。本页的网络前提、身份与 events API 字段陷阱仍可参考，旧的 `gitlab-issue-notify.py` 在各频道接入新同步后退役。

> 本页处理普通事件流／摘要播报。若目标是“每个 Issue／MR 一个 Thread、状态变化后在原 Thread 指派角色 Agent”，不要扩展本播报脚本；改读 [gitlab-buzz-sync.md](gitlab-buzz-sync.md)。接入新同步前，先按其中的切换清单把项目从旧 notifier 的 `SOURCES` 删掉，避免重复播报。（旧的 [issue-thread-routing.md](issue-thread-routing.md) 已取代。）

## 网络前提（先读这段，决定架构）

**GitLab（`gitlab.addx.ai`）出网到 buzz relay 的 ALB 不通**（实测 2026-09）：

```
POST /projects/<id>/hooks/<hook>/test/issues_events
→ 422 {"message":"Failed to open TCP connection to 54.169.234.71:443 (execution expired)"}
```

原因：buzz ALB 由 `from-office` 安全组限制来源（office/自动化 cohort，2026-08-05 冻结），GitLab 出网 IP 不在其中。跟进：`infra/buzz-deploy#2`。

首版统一采用 **Buzz 侧拉取**：Buzz schedule 唤醒指定 Agent，Agent 同一 turn 运行 poller 读取 GitLab，再用自己的身份直接写 Channel。GitLab 不调用 Buzz，也不经过 relay webhook。

| 方案 | 说明 |
|------|------|
| **Buzz-side poller（当前采用）** | Buzz schedule 唤醒播报 Agent → poller → GitLab API 增量拉取 → 持久 cursor／event-id 去重 → Agent 用 `buzz messages send` 直写 Channel |
| webhook source adapter（未来） | 仅在网络、鉴权与重放门禁独立验证后替换“取事件”这一段；不得改变身份、去重、Thread 或 ACT 语义，也不得与 poller 同时成为 writer |

> 历史上验证过 GitLab webhook 无法直连受限 relay，且 relay workflow 只展平 payload 顶层字段。即使未来网络打通，适配层仍必须保留；webhook 只可能成为可替换事件源，不能成为 GitLab 直接控制 Buzz Agent 或 executor 的入口。

## 播报用谁的身份发：用 agent 身份直发，别让 relay 代发

`POST /hooks/<workflow_id>` 发出的消息由 relay 系统身份签名，而不是播报 Agent；当前链路不使用它：

```
buzz users get --pubkey <relay 系统身份 pubkey>   →  []      # 无档案、无显示名
```

后果是频道里**看不出是谁发的**：出问题归因不到那个播报服务，也没法 @ 它追问。播报量一大（一个仓约 170 事件/天，见下）这条很快变成排障障碍。

**做法**：给播报单独铸一个 Agent 身份（密钥对 + owner NIP-OA 背书 + 注册档案，见 [runtime-setup.md](runtime-setup.md)），适配器用**它自己的身份** `buzz messages send` 直发，不走 `/hooks/<wf_id>`。频道里显示的就是这个身份，可归因、可 @。relay Workflow 仍可保留给 schedule / message_posted 这类 relay 内生触发。

> relay 代发虽然不会冒用 owner，却仍缺少可见的 Agent 档案。不要把“未冒名”误当作“身份可归因”；GitLab 事件统一由 Buzz-side poller 以专用 Agent 身份直发。

## 用 events API 覆盖全部事件

不要按资源类型逐个轮询（issues/MRs/notes…）。GitLab 的 **project events API** 一个端点覆盖全部活动：

```
GET /projects/<id>/events?after=YYYY-MM-DD&sort=asc&per_page=100
```

返回 issue 开关、MR opened/approved/accepted/closed、push/建删分支、评论（Note）、成员变更等。

### 字段陷阱

- **Note 事件的 `target_iid` 是评论自身的 id**，所属 MR/issue 编号在 `note.noteable_iid`（用错会生成错误链接，踩过）
- `after` 参数只精确到**日期**，需客户端再按 `created_at > cursor` 过滤
- push 事件的详情在 `push_data`（`ref` / `commit_count` / `commit_title`），没有 `target_title`

### 多项目：一个 group token 读不了别的 group（历史兼容说明）

普通多仓 Agent 不应依赖 Group Access Token；它们使用 owner 固定的项目 token map，为每个目标项目选择独立 Project Access Token。下面的 group 行为仅用于理解既有同步配置或排查历史凭据，不能作为新增 Agent 授权方案。

group access token 读所有项目，只在这些项目**同属该 group** 时成立。加入别的 group 下的项目后：

```
GET /api/v4/projects/<别的 group 下的项目 id>/events   → 404
```

是 404 不是 403 —— GitLab 对无权限资源一律回 404，**别据此判断「项目不存在」**。把这个 bot 加进目标项目也不行：

```
POST /projects/<id>/members
→ 400 {"message":{"user_id":["project bots cannot be added to other groups / projects"]}}
```

所以每个跨 group 的项目要**单独签一个 `read_api` 的 project access token**（`read_api` 足够，别给 `api`），适配器的源表支持按源覆盖 token：

```python
# (project_id, 项目 URL, workflow/频道标识, [该源专用 token 的 env 变量名])
SOURCES = [
    (463,  ".../DEVT/device-cloud-host",  "WF_HOST"),                   # 省略第 4 项 → 用默认 token
    (1175, ".../applications/naturehood", "WF_NH", "NH_READ_TOKEN"),    # 该源单独的 read_api token
]
```

源表里**只写 env 变量名，不写 token 值**——这张表是要提交进仓的。

### 事件量级参考

`DEVT/device-cloud-host` 一个仓约 **170 事件/天**（一次 MR 合并会产生「批准 → 合并 → push to main → 删除分支」4 条）。全量播报很吵，建议：

- 单轮截断上限（如 15 条）防洪水
- 按 `action_name` 白名单过滤，只留 `opened,closed,accepted,commented on`

## 适配器实现要点

```
Buzz schedule → 播报 Agent → GitLab events API（增量拉取）
                     ↓
            cursor／event-id 去重
                     ↓
      专用 Agent `buzz messages send` → Channel
```

- **单实例锁**：`fcntl.flock(LOCK_EX|LOCK_NB)`。schedule 补跑或手动运行与定时器重叠时会并发读到同一游标 → **重复播报**（实测发生过）
- **游标 + 事件 id 去重**双保险，`seen_ids` 保留最近若干条
- **字段名避开保留字**：payload 里别用 `author`/`text`/`timestamp`，用 `by`/`title` 等
- Buzz schedule 只唤醒播报 Agent；Agent 在同一 turn 运行 poller。其 0600 env 只含 GitLab token 与该 Agent 的 Buzz 凭据，不配置 inbound webhook secret

### 🔴 cursor 只能推进到「已成功发送」的位置

最容易写错、而且**错了完全不报错**的一处。常见写法是读到就记最大时间戳、发完无条件落盘：

```python
for ev in events:
    batch.append(ev); max_ts = max(max_ts, ev['created_at'])   # ← 读到就算
for ev in batch:
    if send(ev): seen.add(ev['id'])                            # ← 只有成功才记 seen
json.dump({'last_seen': max_ts, 'seen_ids': [...]}, f)         # ← cursor 无条件推进
```

`seen_ids` 只记成功的，cursor 却把失败的一起跨过去了。下一轮 `created_at > cursor` 的过滤直接跳过它们——**发送失败的事件永久丢失，且日志里没有任何异常**。`seen_ids` 的去重在这里救不了场：它只在 cursor 没推进、事件被重读时才起作用。实测：一次 `NameError` 让 2 条事件发送失败，cursor 照常推进，那 2 条就没了，手动回退 cursor 才补回来。

正确写法 —— cursor 由**发送结果**决定，且**停在第一条失败之前**：

```python
# events API 用 sort=asc，batch 本身就是时间升序
max_ts = cursor
for ev in batch:
    if not send(ev):
        break                      # 这条和它之后的都不推进，下轮重读
    max_ts = ev['created_at']
```

⚠️ 只取「成功发送中的最大时间戳」（`max(ts for ok, ts in results if ok)`）**不够**：失败的是 A、它之后的 B 成功了的话，cursor 仍会越过 A，A 照样丢。必须在第一条失败处停住。

同理，单轮截断（上面的防洪水上限）也只能把 cursor 停在**实际处理过**的最后一条上，不能停在读回来的最后一条上。

代价是失败点之后已发成功的事件下轮会被重读 —— 这正是 `seen_ids` 要挡的：**cursor 保证不丢，`seen_ids` 保证不重**，两者缺一不可。

## 降噪：聚合放适配器，**不要让 agent 总结**

频道里事件一多就会有人问「能不能让 agent 把这些总结一下」。**不要。**

| | 脚本侧聚合 | 让 agent 总结 |
|---|---|---|
| 结果 | 确定性，同样输入永远同样输出 | 每次措辞不同，**会改写事实**（把 3 条 push 说成「若干次提交」） |
| 延迟 | 与轮询同步 | 多一个 LLM turn |
| 成本 | 0 | 每轮一次推理 |
| 副作用 | 无 | agent 会**把播报当成需要回应的对话**，在频道里跟自己聊起来 |

聚合规则本身很简单，按 `(项目, 事件类型)` 合并，**只有真正需要逐条看的才不合并**：

```python
INSTANT = {"issue 新建", "issue 关闭", "issue 重开", "MR 新建", "MR 合并", "MR 关闭"}
# 其余（push / 评论 / 各种 update）按 (项目, 类型) 合并成一条，带计数和最近几个标题
```

实测一轮 11 条事件 → 5 条消息，频道可读性完全不同，而且**没有任何一条事实被改写**。

> 总结这件事该留给「有人明确要一份日报」的时候——那是一个**定时唤醒 Agent 的 Workflow**
> （见 [runtime-setup.md](runtime-setup.md)），输入是 GitLab 的真实数据，不是频道里已经播报过的消息。
> 让 agent 去总结播报流，等于让它基于二手信息再加工一次。

## Canonical 落点

- 每个业务／业务平台 Channel 配一个 Buzz schedule，只唤醒该 Channel 的 `<business>-desk`。
- Desk 在同一 turn 调用 `skills/buzz-agent-setup/scripts/issue_thread_router.py` 与该业务的独立配置；不部署第二个 systemd timer、第二个进程 writer 或 `-issue`／router Agent。
- 旧的 `gitlab-issue-notify.py + gitlab-buzz-notify.timer` 只是早期通知流实验，不是当前 Issue→Thread 自动化的部署方法，不得按它新建实例。
