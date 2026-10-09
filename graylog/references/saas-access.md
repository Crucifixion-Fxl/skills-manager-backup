# Graylog 实例与认证

实际声明入口：`https://graylog-staging-us.addx.live`。部署版本：`declared-7.0-runtime-unknown`；本次未登录或调用业务API，runtime pending。源码和环境一致性见[source-discovery.json](source-discovery.json)。

## 认证申请、消费与撤销

Graylog官方REST access token采用HTTP Basic，username为`GRAYLOG_TOKEN`内容，password为字面`token`；不是Bearer。用户在 System → Users and Teams 为本人创建限时token，权限继承用户role/stream；撤销在Token管理删除。Session token采用Basic `<session>:session`，与access token不同，默认不自行创建。AGENTS历史`internal.example.com`和Bearer描述均不作为实例事实。宿主消费时在内存组装Authorization，不打印header/Base64或放到argv；不读配置Secret中的root密码。

## API、身份与资源探针

源码声明Graylog 7.0，尚未核验实际镜像；先在官方文档版本选择器选择部署对应7.0，再路由部署的 `/api` API browser与官方REST文档，不复制公开API。只读`GET /api/system`核验版本/节点，`GET /api/users/{已核验用户名}`核验subject及role，再对目标stream做最小可见性探针；无role权限时停止，不改用root。非GET必须满足写授权且带`X-Requested-By`，变更stream/input/pipeline、删除数据等需回读/恢复计划。

版本探测先读部署镜像/ArgoCD应用已授权只读元数据（不取Secret），核对研究commit，必要时再以获权只读API响应核验。Ingress可达性不等于身份或权限有效。认证/版本变化使原证据失效；明确的source-only研究不能升级线上验收。

## 官方reference

- [https://go2docs.graylog.org/current/setting_up_graylog/rest_api_access_tokens.htm](https://go2docs.graylog.org/current/setting_up_graylog/rest_api_access_tokens.htm)
- [https://go2docs.graylog.org/current/setting_up_graylog/rest_api.html](https://go2docs.graylog.org/current/setting_up_graylog/rest_api.html)
- [https://go2docs.graylog.org/current/setting_up_graylog/rest_api_use_cases.htm](https://go2docs.graylog.org/current/setting_up_graylog/rest_api_use_cases.htm)
