# cicd.addx.live: native REST and OpenCLI maintenance

优先采用可维护的 native REST 契约；业务模型、角色、状态、读写副作用、回读入口先读取 [platform-api-catalog.md](platform-api-catalog.md) 与 [source-api-catalog.json](source-api-catalog.json)。凭据来源、身份探测与能力边界采用 [saas-access.md](saas-access.md)，不得另造登录方案。候选 base URL 为 `https://cicd.addx.live/api`，只有部署实例确认后才能将源码相对路径用于实际请求；CICD JSON 清单路径已经包含 /api，不能重复拼接 /api。Console 的代理 /api 前缀与后端路径须按现有 auth profile 和实例来源匹配。

本交付只进行了固定源码和本地模拟核验：没有登录、业务请求、创建记录、部署、审批或发布。未新增站点 OpenCLI runtime command；route catalog 不等于全站 command registry，不能将这份文档当作 executable adapter。

只有 REST 不完整或没有可维护的 API 入口时，才进入 OpenCLI sitemap/adapter 维护。复用已安装 upstream [opencli-adapter-author](../../../agent-harness/web-access/runtime/node_modules/@jackwener/opencli/skills/opencli-adapter-author/SKILL.md)，不复制其教程；依赖安装后由该目录提供实际 Skill，未安装先按 web-access runtime 的锁定依赖准备。浏览器站点入口维护使用同 upstream 的 `opencli-browser-sitemap` / `opencli-sitemap-author`，遵守 web-access 的隔离浏览器与用户认证要求。

维护记录最少保存：owner 与源码/前端 SHA、策略和为何 native REST 不足、角色和资源权限证据、页面路由/semantic selector、实际请求模型及响应解码、目标前后状态、写入副作用、同资源回读、typed refusal、adapter fixture 和浏览器 verify 回执。证据必须关联同一候选版本和身份；失配、页面禁用、角色不符、部署未知或缺回读时 fail closed。无源码或合法 UI 证据的写入不得编造 command。

写能力候选必须经本地模拟契约覆盖拒绝分支（角色/资源/状态/挂起/目标变化）、单次写入与回读；本仓库 `skills/delivery/cicd-platform/tests/test_platform_api_catalog.py` 包含静态路由提取和权限/状态安全模拟，明确不是生产服务器运行证明。后续 adapter 还须实际接口/schema 与 UI 验收，不得引用模拟证明线上支持。开源组件的官方 API/CLI 使用对应 owner Skill，不纳入平台站点 adapter 重写。

恢复条件：服务 owner 提供实际部署 SHA/前端 SHA 与角色 grant，完成读回模型与接口契约对照；用户授权目标业务动作后，按该 owner 的状态和发布门禁运行验收。
