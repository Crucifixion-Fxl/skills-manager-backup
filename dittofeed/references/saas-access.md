# Dittofeed AddX fork 实例与认证

## 当前生命周期：已下线

AddX Dittofeed 平台已由用户确认下线。下方域名与认证契约仅保留为历史追溯；不再访问该实例、申请凭据或执行运行态验收。下线记为不适用，不算验收通过。恢复上线后须重新核对实例、部署版本及认证契约。

实际声明入口：`https://ditto.addx.live`。部署版本：`unknown`；本次未登录或调用业务API，runtime pending。源码和环境一致性见[source-discovery.json](source-discovery.json)。

## 认证申请、消费与撤销

本次研究fork prod overlay `AUTH_MODE=single-tenant`：网页secure session Cookie；Admin `/api/admin`由adminAuth校验`Authorization: Bearer <Admin API key>`和目标workspace（workspaceId/项目slug按源码类型选取）。本人在 `/dashboard/settings#admin-api-key`创建key并由宿主注入`DITTOFEED_API_KEY`；管理key的删除/轮换走该workspace当前版本settings契约，不猜REST路径。公开写key（PublicWriteKey）、内部INTERNAL_API_KEY和网页Cookie独立，不能用历史X-API-Key替代Admin Bearer，也不能复用内部服务key。

## API、身份与资源探针

fork全API入口为router.ts注册的controllers和buildApp.ts Fastify Swagger；`/documentation`及`/documentation/json`为部署OpenAPI发现入口，源码types和controller schema补齐AddX私有路由。对照dashboard菜单、workspace role、journey approval状态、发送副作用与回读。官方Admin API先查官方ref，AddX fork差异以研究commit为准。默认仅研究/只读；发送broadcast、写events、journey发布、创建key均为副作用写且需用户明确授权。只有能解析的只读用户/workspace探针并获得对应权限后才运行，不虚构通用 `/me`。

版本探测先读部署镜像/ArgoCD应用已授权只读元数据（不取Secret），核对研究commit，必要时再以获权只读API响应核验。Ingress可达性不等于身份或权限有效。认证/版本变化使原证据失效；明确的source-only研究不能升级线上验收。

## 官方reference

- [https://docs.dittofeed.com/guide/accessing-admin-api](https://docs.dittofeed.com/guide/accessing-admin-api)
- [https://docs.dittofeed.com/contributing/updating-api-docs](https://docs.dittofeed.com/contributing/updating-api-docs)

研究源码`adminAuth.ts`的debug分支包含`actualKey/apiKeys`字段；运行带真实key的探针前需确认该分支禁用或完成字段redaction，避免凭据写日志。该源码发现不代表已运行或已泄漏。
