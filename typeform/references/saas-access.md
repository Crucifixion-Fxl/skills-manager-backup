# Typeform 实例与认证

唯一owner：`typeform`。声明入口：`https://api.typeform.com`。provider保留delegated，只委派本契约，不表示统一runtime adapter或MCP已接入；部署版本/实际subject/权限均pending。本次未登录、读凭据或调用业务API。

## 认证申请、消费与撤销

direct路径：现有survey executor从宿主TYPEFORM_ACCESS_TOKEN消费Bearer PAT，TYPEFORM_API_BASE允许官方US/EU API base（以原脚本allowlist为准），默认api.typeform.com。官方Personal tokens中按最小scopes创建、regenerate、delete；这些操作须明确授权。当前subject、region、表单归属、计划权限、期限及撤销效果pending。throughAudience路径：Audience project key由Audience平台代管Typeform身份，严格沿Audience owner；禁止切到本地Typeform token或仅拆form_id直接调用。

## 身份与能力路由

direct优先官方MCP，当前canonical文档为https://api.typeform.com/mcp的Streamable HTTP+OAuth；旧pr_1748 beta PAT文档不作为现行MCP契约。MCP可包含发布、contacts、automation与response analytics副作用，先读当前tools/scopes与实际account_id。现有PAT REST执行器继续按官方Create/Responses/Webhooks路由；获权GET /me可核对direct subject，但读取已有form还须form/workspace归属。创建/发布/改删表单、测试提交、答卷删除及webhook/分发均保留研究业务独立gate，不为权限验证造新form。

访问恢复只调用`web-access`。认证/版本漂移交由`platform-onboarding`更新本owner；source-only发现不得升级为线上验收。业务gate见[原消费者契约](../../user-research/survey-research-workflow/references/typeform-execution-contract.md)；写必须明确目标/payload、最小权限、独立业务确认/发布门禁及写后回读，结果未知先对账。

## 官方或私有source入口

- [https://www.typeform.com/developers/get-started/personal-access-token/](https://www.typeform.com/developers/get-started/personal-access-token/)
- [https://www.typeform.com/developers/get-started/hands-on/](https://www.typeform.com/developers/get-started/hands-on/)
- [https://www.typeform.com/developers/create/](https://www.typeform.com/developers/create/)
- [https://www.typeform.com/developers/responses/](https://www.typeform.com/developers/responses/)
- [https://www.typeform.com/developers/webhooks/](https://www.typeform.com/developers/webhooks/)
- [https://www.typeform.com/developers/mcp/](https://www.typeform.com/developers/mcp/)
