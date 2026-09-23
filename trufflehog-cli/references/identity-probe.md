# LLM 模型 Key 身份溯源探针

对已验证有效（verified）的 LLM 平台凭证，进一步确认 key 归属人身份。

## 适用场景

- "这个 OpenAI key 是谁的"
- "疑似离职员工的个人 key，需要确认归属"
- "判断 key 属于公司账号还是个人账号"

## 前置条件

仅对同时满足以下条件的凭证执行：

1. 连通性验证结果为 **verified**（参见 [credential-verify.md](credential-verify.md)）
2. 凭证类型属于 LLM 平台（OpenAI、Anthropic 等）

不满足条件时跳过，按原 verified 状态处理。

## 各平台探针定义

### OpenAI（完整支持）

身份查询 API：

```bash
export CHECK_TOKEN="<token>"
curl -s -H "Authorization: Bearer $CHECK_TOKEN" \
  https://api.openai.com/v1/me
unset CHECK_TOKEN
```

返回字段：

| 字段 | 说明 |
|---|---|
| `name` | 账号持有人姓名 |
| `email` | 注册邮箱 |
| `phone` | 注册手机号 |
| `email_domain_type` | `corporate` 或 `social` |
| `orgs[].name` | 所属组织名称 |
| `orgs[].role` | 组织内角色 |

- `200`：成功返回身份信息
- `401`：key 已吊销
- `403`：权限不足，无法获取身份信息

### Anthropic（仅限 Admin Key）

仅 `sk-ant-admin-` 前缀的 Admin Key 可查询组织信息：

```bash
export CHECK_TOKEN="<token>"
curl -s -H "x-api-key: $CHECK_TOKEN" \
  -H "anthropic-version: 2023-06-01" \
  https://api.anthropic.com/v1/organizations/me
unset CHECK_TOKEN
```

返回字段：

| 字段 | 说明 |
|---|---|
| `id` | 组织 ID |
| `name` | 组织名称 |

- 普通 `sk-ant-api-` key 不支持此端点，跳过身份探针
- 仅当 key 前缀为 `sk-ant-admin-` 时执行

### DeepSeek（不支持）

API 仅暴露 `/user/balance` 余额端点，无用户身份信息接口。

跳过身份探针，按原 verified 状态处理。

### Google Gemini（不支持）

API key 无关联用户信息接口。

跳过身份探针，按原 verified 状态处理。

### LangSmith（不支持）

仅有 `/api/v1/info` 返回部署信息，无用户身份信息。

跳过身份探针，按原 verified 状态处理。

### Azure OpenAI（不支持）

API key 绑定 Azure 订阅，需 Azure AD 权限查询身份，超出单 key 探针范围。

跳过身份探针，按原 verified 状态处理。

## 归属判定规则

基于 OpenAI `/v1/me` 返回数据，自动分类 key 归属：

| 信号 | 判定为公司账号 | 判定为个人账号 |
|---|---|---|
| `email_domain_type` | `corporate` | `social` |
| `email` 域名 | 公司域名（如 addx.com） | 公共邮箱（gmail/proton/outlook/qq 等） |
| `orgs[].name` | 包含公司标识 | 个人化名称 |
| `phone` 区号 | +86（国内号码） | 非国内号码可作为参考信号 |

分类结果：

- **公司账号**：邮箱为公司域名
- **个人账号**：邮箱为公共邮箱服务
- **无法判定**：平台无身份端点，或返回字段不足

## 安全约束

- **只读操作**：所有探针仅使用 GET 请求，不触发任何写操作或模型调用
- **凭证不落盘**：使用环境变量传递，探测后立即 `unset`，不写入 shell history
- **报告脱敏**：邮箱部分脱敏（如 `c***u@proton.me`），手机号部分脱敏（如 `+44***772`），不展示完整 key
- **最小探测**：优先使用专用身份 API，不通过实际模型调用来试探
- 与 [credential-verify.md](credential-verify.md) 的凭证安全约束保持一致
