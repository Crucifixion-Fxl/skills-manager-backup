# <组件正式名称> 视觉 QA 与交付记录

> 版本：v0.1  
> 状态：扫描中 / 差异待确认 / 最终待验收  
> 对应 PRD：<approved version>  
> 对应技术设计：<approved version>  
> Draft theme：<name/id>  
> Preview URL：<url without secret in document>

## 1. 基线与环境

| 项目 | 内容 |
|---|---|
| Figma Desktop | <file/node-id/frame size/read time> |
| Figma Mobile | <file/node-id/frame size/read time> |
| 设计是否漂移 | 否/是，影响 <...> |
| Store/Theme | <store + draft name/id> |
| 代码基线 | <branch/worktree/commit if any> |
| 浏览器 | <name/version> |
| 字体与媒体 | 已加载/阻塞 |
| 内容配置 | <preset/settings/blocks> |
| 扫描时间 | <time> |

Preview URL 如含 session、token 或 preview secret，只在当前安全交付通道发送，不写入可提交文档。

## 2. 覆盖矩阵

| QA ID | viewport/状态 | Figma 基准 | 内容/配置 | 功能 | 视觉 | 性能 | 证据 ID | 结论 |
|---|---|---|---|---|---|---|---|---|
| QA-01 | Desktop <WxH> | SRC-01 | 默认 | Pass | Pass/Fail | <...> | E-D-01 | <...> |
| QA-02 | Mobile <WxH> | SRC-02 | 默认 | Pass | Pass/Fail | <...> | E-M-01 | <...> |
| QA-03 | breakpoint-1 | PRD R-RESP-01 | 默认 | <...> | <...> | - | E-R-01 | <...> |
| QA-04 | breakpoint | PRD R-RESP-01 | 长内容 | <...> | <...> | - | E-R-02 | <...> |
| QA-05 | breakpoint+1 | PRD R-RESP-01 | 最大 blocks | <...> | <...> | - | E-R-03 | <...> |
| QA-06 | Theme Editor | TECH T-EDITOR-01 | 增删改排/重载 | <...> | <...> | - | E-E-01 | <...> |
| QA-07 | 超宽屏 | PRD 7.1 | 默认背景图 | <...> | <...> | <...> | E-A-01 | <...> |
| QA-08 | 矮屏/横屏 | PRD 7.1 | 默认背景图 | <...> | <...> | <...> | E-A-02 | <...> |
| QA-09 | 平板竖屏 | PRD 7.1 | 默认背景图 | <...> | <...> | <...> | E-A-03 | <...> |
| QA-10 | 高长宽比手机 | PRD 7.1 | 移动图/桌面图回退 | <...> | <...> | <...> | E-A-04 | <...> |
| QA-11 | 运营异常比例素材 | TECH T-ASSET-01 | 非默认比例/缺图 | <...> | <...> | <...> | E-A-05 | <...> |
| QA-12 | 运营配置说明 | TECH T-OPS-DOC-01 | 全部 Theme Editor 字段 | <...> | <...> | - | E-OPS-01 | <...> |

根据组件特性补齐 hover、focus、active、disabled、展开/关闭、无媒体、长文案和最小/最大 block。

## 3. 视觉检查清单

| 维度 | Desktop | Mobile | 中间/边界宽度 | 备注 |
|---|---|---|---|---|
| 信息结构与顺序 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 容器与尺寸 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 间距与对齐 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 字体 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 颜色/圆角/阴影 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 图片/视频裁切 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 背景主体焦点 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 文字安全区/对比度 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 图片等比与露底 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 层级/overflow | Pass/Fail | Pass/Fail | Pass/Fail | <...> |
| 交互状态 | Pass/Fail | Pass/Fail | Pass/Fail | <...> |

## 4. 差异清单

只要存在未决项，状态必须是 `VISUAL_DECISION_PENDING`，不得输出“已完成”。

| 差异 ID | 严重级别 | 需求 ID | viewport | Figma 期望 | Draft 实际 | Figma 截图 | 实现截图 | 建议方案 | 影响 | 用户决定 |
|---|---|---|---|---|---|---|---|---|---|---|
| VQA-001 | Blocker/Major/Minor/Accepted-candidate | <...> | <...> | <...> | <...> | <path> | <path> | 修复/接受/改需求 | <...> | 待确认 |

用户决定类型：

| 决定 | 后续 |
|---|---|
| 修复 | 回到开发，只改批准项；重测该项和必要回归 |
| 接受差异 | 写入 PRD、技术设计、变更记录的批准偏差 |
| 修改需求 | 回退 PRD；重新评估技术设计和已有实现 |

## 5. 证据清单

| 证据 ID | 类型 | viewport/条件 | 文件/链接 | 说明 |
|---|---|---|---|---|
| E-FIGMA-D | Figma Desktop | <WxH> | <absolute path> | 最终设计 |
| E-DRAFT-D | Draft Desktop | <WxH> | <absolute path> | 同 viewport 实现 |
| E-FIGMA-M | Figma Mobile | <WxH> | <absolute path> | 最终设计 |
| E-DRAFT-M | Draft Mobile | <WxH> | <absolute path> | 同 viewport 实现 |
| E-DIFF-001 | 对比图 | <WxH> | <absolute path> | 仅差异评审使用 |
| E-PERF-01 | 性能 | <condition> | <report> | Lab/体积/Field 类型 |
| E-OPS-01 | 运营配置说明 | 字段逐项核对 | <document path/link> | 常用直接展示、进阶默认收起 |

## 6. 功能、配置与性能结果

| 类别 | 方法 | 结果 | 证据 | 结论边界 |
|---|---|---|---|---|
| Liquid/schema/static | <...> | <...> | <...> | 仅代码/静态 |
| Theme Editor | <...> | <...> | <...> | 仅 Draft 配置 |
| 浏览器功能 | <...> | <...> | <...> | 仅指定 URL/设备 |
| 性能 Lab | <same-condition median> | <...> | <...> | 不代表 Field |
| Field/RUM | <if available> | <...> | <...> | 聚合窗口和范围 |

不得用上传成功代替浏览器功能结论，不得用单次 Lighthouse 分数代替组件性能结论。

## 7. 已批准偏差

| 差异 ID | 批准人/日期 | 批准内容 | PRD/技术设计同步位置 | 剩余风险 |
|---|---|---|---|---|
| <id> | <...> | <...> | <...> | <...> |

## 8. 最终交付包

| 项目 | 内容 |
|---|---|
| 组件名称 | <...> |
| 页面位置 | <...> |
| PRD / 技术设计 | <versions> |
| Draft theme | <name/id> |
| Preview URL | <secure delivery> |
| 运营配置说明 | <document path/link + version> |
| Desktop 主证据 | E-FIGMA-D + E-DRAFT-D |
| Mobile 主证据 | E-FIGMA-M + E-DRAFT-M |
| 视觉结论 | 无未决差异 / 已批准偏差 <IDs> |
| 验证结论 | <...> |
| 未验证项 | <...> |
| Git 状态 | 未 commit / 未 push / <actual> |
| Live 状态 | 未发布 |
| 剩余风险 | <...> |

## 9. 下一门禁

差异存在时，请逐项回复 `VQA-xxx：修复 / 接受差异 / 修改需求`。

无未决差异时，请明确回复“最终验收通过”。通过后 Agent 只能询问是否授权 commit、push 和创建 MR，不得自动执行。
