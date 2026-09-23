# 产物索引

| 产物 | 路径 | 状态 | Owner | 最后更新 | 备注 |
|---|---|---|---|---|---|
| 项目上下文 | `project-context.md` | draft |  |  |  |
| Workflow 状态 | `workflow-state.json` | draft |  |  |  |
| 决策记录 | `decision-log.md` | draft |  |  |  |
| 假设记录 | `assumption-log.md` | draft |  |  |  |

## Status 枚举

- `missing`
- `draft`
- `needs_review`
- `approved`
- `superseded`
- `blocked`
- `final`

## 维护规则

- 每新增、修改或废弃一个项目产物，都要更新本索引。
- `Status` 使用上面的固定英文枚举，便于自动化读取。
- `备注` 记录阻塞项、gate 状态或替代文件。
