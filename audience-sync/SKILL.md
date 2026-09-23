---
name: audience-sync
description: 通过生产 Audience Platform 验证 Project key 权限并完成圈人、预览、物化与精确运行同步；当用户要求查看 key 权限、筛选人群或同步人群时使用。
---

# Audience Sync

## Description

使用 `https://audience-workflow-api-prod-us.addx.live` 和安全注入的 Project Personal key。
先阅读[宿主配置](references/host-configuration.md)，选择原生工具或随仓 Python 客户端的启动方式，
再验证每把已提供 key 对应的 Project 和允许操作。多把已提供 key 可以直接进行只读验证，
无需再次请求许可；选择与用户目标 Project 和操作匹配的已验证 key。

圈人计划保存筛选条件；圈定人群是按条件得到的成员集合。预览只返回聚合证据，
物化成功才表示对应圈定人群已物化。

## Rules

- **缺少或无效 key：**只返回这个引导网址：
  https://micro-app-platform-us.addx.live/audience-sync-us。
  服务不可用、操作被拒绝、Project 不匹配，或另一把已提供 key 验证成功时，
  不应据此推断缺少 key。
- **查找现有圈定人群：**使用 `list_project_audience_assets` 查找所选所有者可见的原生圈定人群和圈人计划资产。
  持续使用 `next_cursor` 翻页直到 null，包括中间页为空的情况。
  对圈人计划，将返回的 `aqp_` 资产 ID 传给 `get_audience_query`。
- **查看同步任务：**使用 `list_project_syncs` 和 `get_project_sync` 读取所选 Project 下各所有者的聚合同步任务。
  读取权限不代表可以采用其他所有者的圈人计划或重试同步。
- **圈选人群：**读取在线 `get_query_capabilities`，按[圈人条件](references/audience-filter.md)和
  [画像筛选](references/audience-profile-selection.md)转换用户要求，然后验证并创建不可变的圈人计划。
  聚合条件只能选择该 Project 已公布的边与 `aggregate_id`；主表范围由 Platform 固定，
  子表缺少 tenant/bundle 或需求涉及已支持聚合，不等于必须先改 DATA 模型。
- **预览与物化：**获取该计划的fresh preview attestation 和人数，再提交物化并轮询其精确请求 ID。
- **同步：**使用确切成功的物化请求、run 和人数，以及同一 Project 公布的 Brevo Folder 目标及其 revision。
  将用户确认保留为 JSON `confirmed=true`；依据精确同步回读报告成功。
- **检查或恢复：**读取现有计划或精确请求，参见[错误与恢复](references/errors-and-recovery.md)。
  只有检查当前能力和语义后才可复用返回的结构化 `criteria`；
  [过期计划需要重新验证并创建新计划](references/project-query.md#read-and-rebuild-historical-criteria)。
- **报告完成：**物化成功或同步回读达到终态后，按[完成回复](references/project-query.md#completion-replies)
  附上可用且 binding 匹配的链接。保留实际状态；链接不证明成功，也不授权额外工作。

继续任务时使用现有有效的计划与请求证据。请求 schema 和在线能力开关决定可用操作。
[平台 API](references/platform-api.md)列出操作；[Project 圈人查询](references/project-query.md)解释执行证据；
[同步确认](references/confirmations.md)定义同步输入。

默认回复圈人计划名称、实际筛选字段/操作符/值、数据快照分区、分阶段人数和原生状态；
同步还分别报告已知的 Folder 目标名称、后端返回的实际 Brevo List 名称及新增、移除、跳过人数。
缺失信息保持未知，
详细口径与示例见[完成回复](references/project-query.md#completion-replies)。
计划/请求/run ID、preview attestation 和其他精确执行证据在内部保留，不默认展示。
仅当 Platform 返回的结果网址与已验证响应的 binding 匹配时才展示，不得自行构造网址。
收件人数据与服务商凭据留在 Platform 内。
本 Skill 管理圈定人群的成员关系，不发送营销活动。

## Examples

### Good

用户：“验证我提供的几把 key，然后按已确认的条件完成这次不超过 5 人的同步。”
先分别只读验证并汇总权限，选择匹配 Project 的 key；沿用覆盖本次范围的用户授权，
预览后绑定精确圈人计划、成功物化 run、人数及目标 revision，回读成功后报告聚合结果。

### Bad

离线格式通过就称 key 已认证；在原生工具宿主中寻找未分发的 Python 脚本；
把排队状态当成功，或为同一次含糊的同步结果换 key 再提交。
