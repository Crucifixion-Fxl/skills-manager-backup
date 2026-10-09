# CustomerCare / SmartPopup 接入与认证

Owner：`customer-care`。入口：https://customer-care-admin-staging.addx.live。

认证/业务契约：[现有reference](api-reference.md)；认证发现见 [auth-discovery.json](auth-discovery.json)，源码与部署一致性见 [source-discovery.json](source-discovery.json)。

Skill记录旧staging匿名；研究源码SmartPopup SSO由ADMIN_SSO_ENABLED控制，false/unset为no-op；Warranty独立飞书身份/角色gate常开。部署SHA、flag和SpiceDB迁移待核验；prod地址推测不得采用。

默认先核验环境、部署版本、subject和资源权限。source-verified不等于runtime-verified；本次未登录、读取凭据或调用业务API。写授权、发布/审批、回读和结果未知门禁沿用owner Skill。需要刷新时只研究受影响认证/源码契约，不复制公开业务API。
