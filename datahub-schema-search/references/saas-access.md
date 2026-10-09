# DataHub Schema Search 实例与认证

唯一owner：`datahub-schema-search`。声明入口：`https://datahub-schema-search.addx.live`。provider保留delegated，只委派本契约，不表示统一runtime adapter或MCP已接入；部署版本/实际subject/权限均pending。本次未登录、读凭据或调用业务API。

## 认证申请、消费与撤销

既有Skill声明独立服务入口与/docs，但未声明认证机制；不宣称匿名、不套DATAHUB_TOKEN或DataHub SSO。宿主变量、subject、实际认证/角色、申请/撤销及生命周期均pending。只有部署/source认证契约核对后才能连接验证。

## 身份与能力路由

这是DataHub私有搜索服务，不是DataHub GraphQL/API的同一路径。服务自身/docs为发现入口（本次未在线打开），既有Skill仅证明search/tables/databases和POST sync路由声明。先定位service版本、认证中间件与资源权限，再按其Swagger/源码选择获权读取。POST sync刷新索引是写操作，需明确范围与授权、完成后回读；不为测试可达性自动触发。DataHub底层访问继续调用datahub owner，公开DataHub docs不替代此私有API契约。

访问恢复只调用`web-access`。认证/版本漂移交由`platform-onboarding`更新本owner；source-only发现不得升级为线上验收。业务gate见[原消费者契约](../SKILL.md)；写必须明确目标/payload、最小权限、独立业务确认/发布门禁及写后回读，结果未知先对账。

## 官方或私有source入口

- AddX私有service来源见[source-discovery.json](source-discovery.json)；没有已核实的独立公开Skill/MCP/API，不套上游产品。
