# Device Cloud Host 推广使用反馈

推广反馈入口仓库：[DEVT/device-cloud-host](https://gitlab.addx.ai/DEVT/device-cloud-host)，总任务 [#367](https://gitlab.addx.ai/DEVT/device-cloud-host/-/issues/367)。按已证实的故障归属选择 Host 或对应插件仓库；无法定位时由 Host 接收并在定位后关联归属仓库的 Issue。独立可复现问题应查重后建独立 Issue，不逐条追加到总任务。普通用户入口由 `device-cloud-host-usage` Skill 负责。

## 分类与标签

类别写入 Issue 正文，固定为 `install_blocker`、`unsupported_device`、`unsupported_plugin`、`missing_action`、`bug`、`docs_usability`，一个主类别，可列次要现象。分类便于日报查询，不为这些类别创建永久 label。

按 [Label 规则的唯一 SSOT](label-system.md) 查询项目及祖先 Group 的现有标签后，才选择规范值：

| 反馈类别 | 适用 type | 说明 |
|---|---|---|
| `bug` | `type::bug` | 已承诺行为偏离预期 |
| `install_blocker` | `type::bug` 或 `type::maintenance` | 可复现产品缺陷取 bug；环境/说明改进取 maintenance |
| `unsupported_device`、`unsupported_plugin`、`missing_action` | `type::feature` | 新设备、插件或能力诉求；确认已有承诺后才改判 bug |
| `docs_usability` | `type::maintenance` 或 `type::feature` | 文档修复取 maintenance；产品交互能力取 feature |

优先级由实际影响与 Owner 分诊，不能因推广期自动贴 P0。`priority::p0` 当前若不可用，停止该 label 写入并报 Group 治理缺口，不用 `P0` / `priority::P0` 代替。`status::*`、`area/*` 同理只选已存在的规范标签。一个 Issue 至少有明确 assignee；达到 ready 时必须有 milestone。

## 创建前与提交

1. 查 Host 与相关插件仓库的 open/closed Issue；同一根因优先补充已有 Issue。查明归属后在归属仓库提交，无法定位时先由 Host 接收并在正文标记归属待确认。查询失败标记为“查重未完成”，不能断言没有重复。
2. 保留用户原声的脱敏短句、反馈日期和来源链接或受限来源标识；AI 判断、复现结果和建议另写。用户明确不愿公开原话时，公开正文只放匿名概述。
3. 给用户展示完整草稿。用户愿意提交且当前会话有授权时提交并回读；不愿亲自提交但愿意记录时，由开发代提并标记提交者与原反馈人角色。用户拒绝公开或提交时不代替其做决定。
4. 关联相同根因的 Sentry、A4X 和埋点查询链接时，只用可访问的脱敏证据；不复制完整日志或凭据。

## 草稿模板

```markdown
## 反馈类别与任务目标
- 类别：<install_blocker / unsupported_device / unsupported_plugin / missing_action / bug / docs_usability>
- 用户想完成：<原任务，不写 AI 猜测的动机>

## 用户原声与来源
> <脱敏原话；用户不允许公开时改为“匿名概述”，受限来源另存>
- 反馈时间：<带时区>
- 来源：<可访问链接或“受限，联系反馈 Owner”>
- 提交方式：<本人 / 开发代提>

## 预期与实际结果
- 预期：<可验证的目标>
- 实际：<观察到的行为和阶段>
- 复现步骤：<最短步骤；未知写“待补”>

## 环境与证据
- Host 版本：<版本或未知>；Skill 版本：<插件 Git revision 或未知>；插件名称/版本：<或不适用>
- 设备类型：<型号类别；不含 SN>；系统：<必要时>
- 脱敏错误码：<或无>；关联 ID：<或无>
- 证据链接：<Sentry / A4X / 埋点 / RP 用例链接，按权限可见；无则写“待补”>

## 诊断与跟进
- 已证实：<证据支持的事实>
- 待验证：<假设，不能冒充原声>
- Owner / 答复 / 下次回看：<明确人和动作>
```
