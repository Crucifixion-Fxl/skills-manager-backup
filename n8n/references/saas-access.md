# n8n 实例与认证

实际声明入口：`https://n8n.addx.live`。部署版本：`unknown`；本次未登录或调用业务API，runtime pending。源码和环境一致性见[source-discovery.json](source-discovery.json)。

## 认证申请、消费与撤销

官方public API使用用户在 Settings → n8n API 创建的API key，header `X-N8N-API-KEY`，通过宿主注入`N8N_API_KEY`。Enterprise scope和SSO取决于实际edition；部署values只证明UI开启和editor URL，不证明Enterprise、SSO或API已启用，不能套用Casdoor。Key申请/权限和撤销由本人在Settings管理；非Enterprise key能力也需按版本核验，不宣称scope受限。

## API、身份与资源探针

版本匹配后路由官方public API `/api/v1`、OpenAPI与官方Skill；以受权GET `/api/v1/workflows?limit=1`做小范围资源探针，但该请求仅证明当前key可见资源，不能证明人名或管理员角色。subject须从当前用户设置/角色与Key申请记录核对。工作流激活、执行、创建/改删及凭据配置有真实副作用，需明确授权和回读。不要读取workflow credentials或API key明文。

版本探测先读部署镜像/ArgoCD应用已授权只读元数据（不取Secret），核对研究commit，必要时再以获权只读API响应核验。Ingress可达性不等于身份或权限有效。认证/版本变化使原证据失效；明确的source-only研究不能升级线上验收。

## 官方reference

- [https://github.com/n8n-io/n8n-docs/blob/main/docs/connect/n8n-api/authentication.md](https://github.com/n8n-io/n8n-docs/blob/main/docs/connect/n8n-api/authentication.md)
- [https://github.com/n8n-io/n8n/blob/master/packages/cli/src/public-api/v1/openapi.yml](https://github.com/n8n-io/n8n/blob/master/packages/cli/src/public-api/v1/openapi.yml)
- [https://github.com/n8n-io/skills](https://github.com/n8n-io/skills)
