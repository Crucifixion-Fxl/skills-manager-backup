# Linked Items — relates_to 用法（GitLab CE）

## CE vs Premium 限制

| link_type | CE | Premium |
|-----------|-----|---------|
| `relates_to` | ✅ | ✅ |
| `blocks` | ❌ 400 Bad Request | ✅ |
| `is_blocked_by` | ❌ 400 Bad Request | ✅ |

本 skill 只用 `relates_to`。阻塞关系通过**两个约定**表达，不依赖 `blocks` link_type。

## 阻塞关系的表达（CE 方案）

### 约定 1：在 issue 描述里写依赖段

```markdown
## 依赖

- 阻塞于 #15（埋点未注册，等 data team 审核）
- 依赖 #17 完成后才能开始
- 等运维部署 Prometheus ServiceMonitor（issue 见 infra/#42）
```

### 约定 2：给被阻塞的 issue 贴 `flag/blocked`

读 board 时立刻可见。保留 issue 原本的 `status::*`；依赖解除后只移除 flag。

### 约定 3：同时建立 `relates_to` link

即使不是 blocks，也要用 relates_to 把阻塞对和被阻塞对关联起来。GitLab UI 的 "Linked items" 面板会显示。

## 加 link 的 glab 命令

```bash
glab api -X POST "projects/:id/issues/19/links" \
  -f target_project_id=1327 \
  -f target_issue_iid=20 \
  -f link_type="relates_to"
```

`:id` 会被 glab 替换为当前仓库 id。跨项目时把 `:id` 换成对方项目的数字 id 或路径（如 `projects/engineering%2Fskills/...`）。

**跨项目关联：** `target_project_id` 换成对方项目 id 即可。GitLab 会自动去重，重复 POST 同样的 link 返回 409。

## Deployment Task 的 related fallback（Work Item API）

只有 parent-child capability preflight 明确失败时，才把 Deployment Task 降级为独立 Task
work item + `RELATED` link。普通 issue 的 REST link 示例不能证明 Task 关联成功；Task 路径必须
使用 Work Item GraphQL，并回读 `WorkItemWidgetLinkedItems`。

### 1. 解析 Requirement 和 Task type 的 global ID

不要猜全局 ID，也不要假设不同 GitLab 实例的 Task type ID 相同。先按 namespace 和 Requirement
IID 查询 `WorkItemID` 与 `workItemTypeId`：

```graphql
query DeploymentTaskInputs($path: ID!, $requirementIid: String!) {
  namespace(fullPath: $path) {
    workItem(iid: $requirementIid) { id iid }
    workItemTypes(name: TASK) { nodes { id name } }
  }
}
```

只有查询恰好得到一个 Requirement（形如 `gid://gitlab/WorkItem/<id>`）和一个名为 `Task` 的
type 才继续。空值、歧义或 schema 不支持均停止写入并产出 Ops Todo。

### 2. 创建并关联 standalone Task

优先在一次 `workItemCreate` 中用 `linkedItemsWidget` 建立关系，避免创建成功但关联失败：

```graphql
mutation CreateRelatedDeploymentTask(
  $path: ID!
  $title: String!
  $taskType: WorkItemsTypeID!
  $requirement: WorkItemID!
) {
  workItemCreate(input: {
    namespacePath: $path
    title: $title
    workItemTypeId: $taskType
    linkedItemsWidget: { workItemsIds: [$requirement], linkType: RELATED }
  }) {
    workItem { id iid webUrl workItemType { name } }
    errors
  }
}
```

若 Task 已由另一个已授权步骤创建，则用它的真实 `WorkItemID` 补关联：

```graphql
mutation LinkDeploymentTask($task: WorkItemID!, $requirement: WorkItemID!) {
  workItemAddLinkedItems(input: {
    id: $task
    workItemsIds: [$requirement]
    linkType: RELATED
  }) {
    workItem { id }
    errors
  }
}
```

HTTP 200 不代表成功；mutation 的 `errors` 必须是空数组。若 schema 没有
`workItemCreate`/`linkedItemsWidget` 或 `workItemAddLinkedItems`，不得静默退回普通 Issue 或把
`relates_to` 写成文本后声称完成；停止并产出需要 GitLab 管理员或人工 UI 操作的 Ops Todo。
写入契约使用 mutation input enum `RELATED`；不要把该枚举当成回读值。

### 3. 回读关系

用创建/关联响应中的 Task `WorkItemID` 回读，不用标题搜索：

```graphql
query VerifyDeploymentTaskLink($task: WorkItemID!) {
  workItem(id: $task) {
    id
    workItemType { name }
    widgets {
      ... on WorkItemWidgetLinkedItems {
        linkedItems {
          edges {
            node { linkType workItem { id iid webUrl } }
          }
        }
      }
    }
  }
}
```

验收必须同时证明：返回的 work item type 是 `Task`、Task ID 等于创建结果、linked items 中存在
精确 Requirement `WorkItemID`，且 `linkType` 是 output string `"relates_to"`。输出字段是字符串，
不得拿它与写入时的 enum `RELATED` 做同值比较。把这份 readback 与降级原因写进 Deployment
Task；只有 URL 或 mutation 成功消息不算完成。

## 反模式

### 只写 relates_to link，不写描述依赖段

信息丢失：link 只表达 "相关" 不表达 "阻塞方向"。谁阻塞谁只在描述里说得清。

### 用 `link_type: blocks` 在 CE

返回 400。别复制 Premium 文档的示例。

### 阻塞原因只在评论

评论会被新评论淹没。依赖关系在描述专门段，进度变化在 comment。两套通道互补。

### 阻塞对不贴 `flag/blocked`

board 视图上看不出来 — 一个 in-progress 的 issue 如果其实在等依赖，只有点进去才知道。label 要和描述保持一致。

## 典型场景

### 场景 1：设计文档拆多个实现 issue

设计 issue #10 → 实现 issue #11, #12, #13。

```bash
# #11, #12, #13 都与 #10 建立 relates_to
PID=$(glab api projects/:id | jq -r '.id')
for impl in 11 12 13; do
  glab api -X POST "projects/:id/issues/$impl/links" \
    -f target_project_id="$PID" \
    -f target_issue_iid=10 \
    -f link_type="relates_to"
done
```

#11-#13 的描述里都要有：

```markdown
## 背景
[...] 本 issue 是 #10 设计的实现子项之一。
```

### 场景 2：data 依赖链

`area/observability` 的 #19 依赖 `area/data` 的 #20 先产出 dwm 表。

1. 建 relates_to link
2. #19 描述加 "阻塞于 #20"
3. #19 保留当前 status，另贴 `flag/blocked`
4. #20 close 时，在 #19 comment 里通知 + 移除 `flag/blocked`

### 场景 3：跨项目

customer-care 的 #19 依赖 dbt 仓库 dbt/!2988 MR。MR 不是 issue，建不了 link，但可以：

- 在 #19 描述里写 "依赖 dbt/!2988 合并"
- MR 合并时在 #19 comment 里附 MR 链接

跨项目 issue 间才用 `target_project_id` 跨 link。
