---
name: graylog
description: 维护AddX Graylog的真实实例、部署源码、身份和权限验证及官方API入口；用于查认证方式、申请或撤销个人凭据、定位当前版本API或排查接入失败。所有运行态与业务调用遵守环境和用户授权。
---

## Description

Graylog实际接入由[saas-access.md](references/saas-access.md)持有，机器认证事实见[auth-profile.json](references/auth-profile.json)，源码和全API发现见[source-discovery.json](references/source-discovery.json)。日常认证复用`web-access`；首次或部署/认证变化用`platform-onboarding`更新本owner。优先官方Skill/CLI/API，本Skill只补AddX部署事实与私有差异。

## Rules

1. 先核对目标环境、部署SHA/版本、subject和最小资源权限；source-verified不等于runtime-verified。未知部署不得沿用其它平台认证或退回占位域名。
2. 凭据仅从宿主环境变量`GRAYLOG_TOKEN`消费；不读取Secret、vault、session/token缓存值，不在argv、日志、仓库或聊天输出凭据；用户交互创建/撤销不由扫描自动触发。
3. 默认源码研究和无副作用小范围验证。业务写必须有本次明确授权、完整目标/payload和资源scope，审批/发布/生产门禁独立适用；写后回读，结果未知先对账不重放。
4. 刷新仅覆盖受影响认证/部署/API，不复制完整官方公开API。无权限停止并说明owner和恢复条件，不换身份。

## Steps

1. 读取实例和认证reference，判断source-only还是runtime evidence；缺失环境/版本/权限时按source-discovery具体入口研究。
2. 路由部署版本对应官方reference或fork OpenAPI；先核对资源和subject，再执行已授权操作。
3. 记录候选版本、环境、数据范围、执行时间/退出码与未验收能力；凭据内容不入回执。

### 空白页与版本一致性排查

登录页持续空白时，先查浏览器控制台及实际资源请求，不把 HTTP 200 当作前端资源正常。JavaScript 返回 HTML/MIME 错误或 ChunkLoadError 时，可做一次正常刷新，核对当前页面引用的资源路径与返回 Content-Type；不要通过关闭 MIME 检查、伪造会话或改部署绕过。公开版本接口只用于诊断，不代表已登录。

如果不同请求出现版本、资源哈希或 MIME 类型变化，分别保存时间与本机/远端结果，标记部署响应一致性待查；不能把单次版本作为稳定部署版本，不能未核实就断言负载均衡或节点配置是根因。登录界面无法加载属于技术阻塞，不反复要求用户输入密码。修复配置/部署需独立授权，当前只读验收不执行。

技术阻塞过后重新核验实际界面：登录表单恢复时记录本次 ready，将历史 MIME 问题保留为历史证据，不继续要求用户等待修复。界面可登录不证明所有节点或认证已恢复；按人工接管规则置前、有界检测，再核验服务器认可的当前主体。按用户名查询 profile 不能替代 current-caller 证据。已有 access token 继续按原生 Basic token 契约消费；网页登录产生的 Session 可作为明确标注的回退并与原生 Token 并存，不把它冒充 API access token或为验收自动创建新 Token。

核验所部署版本的 SessionValidationResponse 与实际 handler，不只依赖 Swagger 状态码注释。现有 Basic Session 模式要求服务器返回有效会话及匹配的当前用户名，再读业务；正常 Token 没有浏览器 Session 时的返回语义单独处理。按版本支持情况请求 `X-Graylog-No-Session-Extension: true`，避免默认延长会话；Cookie 设置/删除等认证状态维护与业务写入分开记录，不输出 session_id 或 Set-Cookie。系统概况可能需要独立节点权限，403 不自动否定已核验身份；流列表优先 paginated 的小页，输出 ID/标题/禁用状态，授权总数与顶层或全局统计分开，不读取日志正文、规则或完整配置。
