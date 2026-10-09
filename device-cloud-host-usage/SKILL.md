---
name: device-cloud-host-usage
description: 帮公司同事安装、使用和诊断 Device Cloud Host 上位机，并把安装阻塞、设备或插件不支持、Bug、文档问题整理为可追踪的 GitLab 反馈。用户提到上位机 Host、devt-host guide、DevShell、设备找不到或插件操作失败时使用。
---

# Device Cloud Host 使用与反馈

## Description

面向所有安装 AddX Plugin 的同事，提供一个上位机使用入口。安装事实以官方 guide 和实际运行结果为准；页面复制、AI 猜测和 Hook 线索不证明安装成功。

## Rules

### 1. 找到用户当前卡住的阶段

先看 [Host 安装指引](https://device-cloud.builder.addx.live/host-product/install)；该页提供 PROMPT/CLI 两种入口，CLI 命令为：

```bash
npx --yes --registry=https://nexus-sg.addx.live/repository/devt-npm/ devt-host@latest guide
```

完整阅读 guide 后按其当前版本指导安装、启动和检查。不要把 `guide` 输出本身当作安装完成；不要臆造安装包参数、端口或本地路径。已有 Host 时先核对实际版本与状态，再决定是否复用。

按顺序确认：guide 能否运行 → Host 安装/启动 → 身份及 DevShell 就绪 → 目标插件准备 → 设备发现 → 能力匹配 → 首次真实操作。每一步记 `成功 / 明确阻塞及错误码 / 用户取消 / 未验证`，不要把没有下一步记录推断为阻塞。无设备、仪器型号不支持或缺少 action 是反馈类型，不应伪装成程序异常。

### 2. 诊断与证据

- 优先读取用户提供的脱敏报错、Host/Skill/插件版本、设备类型、目标任务、最近一次操作及时间。只读检查环境和健康状态；需要重启、升级或修改配置时说明影响并遵循用户授权。
- 有权限时按关联 ID 查 A4X Logger 的日常日志、Sentry CN 的非预期异常。ReportPortal 只用于有测试用例上下文的执行证据；普通开发问题不去 RP 寻找或制造记录。
- 预期失败记录阶段、结果和规范错误码；只有未预期程序异常或可行动的系统故障才关联 Sentry。诊断结论区分已证实、推测与待验证，给出下一步操作。
- 不收集或上传 GitLab token、完整环境变量、完整命令输出、个人邮箱、设备 SN、原始路径。若用户愿意参与使用分析，可说明本地 `glab` 数字身份将用于受控匿名化；拒绝或缺失身份不阻断使用。

### 3. 反馈到 Issue

用户反馈 Bug、安装阻塞、设备/插件不支持、缺少 action 或文档问题时，按 [`gitlab-issue-sop` 的 Host 反馈规范](../../collaboration/gitlab-issue-sop/references/device-cloud-host-feedback.md)执行。先按故障归属选 Host 或插件仓库，再跨相关 Issue 查重并给出草稿；归属未明时以 [DEVT/device-cloud-host](https://gitlab.addx.ai/DEVT/device-cloud-host) 接收并关联后续归属仓库的 Issue。草稿包含类别、任务目标、预期/实际结果、复现步骤、Host/本 Skill/插件版本、设备类型、脱敏错误码与证据链接。

保留用户脱敏原话，并与 AI 的技术判断分栏；用户不愿公开原话时只写匿名概述、受限来源留在授权范围内。用户确认草稿且当前会话有写授权后再提交并回读 Issue URL。用户不愿自己提单但愿意记录反馈时，交开发代提；明确拒绝公开或提交时尊重其选择。无法访问 GitLab 或无写授权时只交付可复制草稿和待办，不声称已提交。

### 4. 收尾

用一小段话给用户：当前阶段和证据、下一步可执行操作、已创建或待提交 Issue、仍未知的项目。安装和诊断过程中的埋点由官方 CLI/Host 实现；本 Skill 不用临时 HTTP 请求伪造成功事件，也不上传 prompt、stdout/stderr 或凭据。
