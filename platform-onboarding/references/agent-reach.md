# Agent Reach 可选集成

目标项目是 [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach)，不是 PyPI 的同名项目。它负责安装、诊断与选择上游工具，读取/搜索由上游工具执行。官方 Skill 与安装说明从实际版本读取，不复制一份渠道教程。

运行 `platform-onboard agent-reach-doctor` 使用上游真实 `doctor --json`，只输出渠道状态、上游 backend 名称；原始 message、stderr 和配置值不会进入模型输出。`AGENT_REACH_BIN` 可指定受控本机安装的绝对可执行路径。NOT_INSTALLED 是依赖状态，不是平台认证失败；DIAGNOSED 也不等于任务验收。

按目标平台选择已安装的官方 CLI、OpenCLI 或其他 backend。同一 OpenCLI/browser profile 沿用，不另建 daemon 或改用户默认 profile。当前任务涉及登录/人工验证时使用 [web-access](../../web-access/SKILL.md)，并遵循上游工具实际允许的凭据消费方式。需要手动 Cookie 导出的工具不自动复制浏览器凭据，优先适用的原浏览器路径。

内部/私有/认证 URL 与内容留在本机工具。公共网页、RSS、YouTube、公开 GitHub 等适用时可使用公共 Reader；不将私有页面交给第三方 Reader。先做目标任务实测，记录渠道、版本、backend、环境、认证和结果，source/doctor 状态不升格成业务读写验收。

本机集成在隔离环境安装项目代码与必要依赖；不运行 --system 去重写全局 Skills、MCP、浏览器或凭据配置。需要增加目标渠道工具时按已授权任务安装该渠道，不批量安装所有渠道。
