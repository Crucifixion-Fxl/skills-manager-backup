# 常见凭证类型速查

当没有 `DetectorName` 时，用于快速初判。

TruffleHog 内置 1,000+ 检测器（`DetectorName`），覆盖 Git 平台、云服务、SaaS、数据库、支付、监控等主流服务。完整列表：
[proto/detectors.proto (v3.94.0)](https://github.com/trufflesecurity/trufflehog/blob/v3.94.0/proto/detectors.proto)

| 模式 | 可能类型 | DetectorName | 只读验证建议 |
|---|---|---|---|
| `glpat-` | GitLab PAT | `GitlabV2` | `GET /api/v4/personal_access_tokens/self` |
| `ghp_`, `gho_`, `ghs_`, `github_pat_` | GitHub Token | `GitHub` | `GET https://api.github.com/user` |
| `AKIA` + 20 chars | AWS Access Key ID | `AWS` | `aws sts get-caller-identity` |
| `ASIA` + 20 chars | AWS STS 临时 Key ID | `AWSSessionKey` | `aws sts get-caller-identity` |
| `xoxb-`, `xoxp-`, `xoxa-` | Slack Token | `Slack` | `POST https://slack.com/api/auth.test` |
| `sk-` | OpenAI API Key | `OpenAI` | `GET /v1/models`；余额探针见 [balance-probe.md](balance-probe.md)；身份探针见 [identity-probe.md](identity-probe.md) |
| `sk-`（deepseek 域） | DeepSeek API Key | `DeepSeek` | `GET /user/balance`；余额探针见 [balance-probe.md](balance-probe.md) |
| `sk-ant-` | Anthropic API Key | `Anthropic` | 按平台最小只读探针验证 |
| `AIza` | Google Gemini API Key | `GoogleGeminiAI` | `GET /v1beta/models`；有限支持，见 [balance-probe.md](balance-probe.md) |
| `lsv2_pt_` | LangSmith API Key | `LangSmith` | `GET /api/v1/sessions`；非计费型，余额探针不适用 |
| Azure endpoint + key | Azure OpenAI Key | `AzureOpenAI` | `GET /openai/models?api-version=...`；余额需 Azure 订阅权限 |
| `sk_live_`, `rk_live_` | Stripe Key | `Stripe` | `GET /v1/balance` |
| `SG.` | SendGrid API Key | `SendGrid` | `GET /v3/scopes` |
| `-----BEGIN ... PRIVATE KEY-----` | 私钥材料 | `PrivateKey` | 不做“使用型验证”，按高危处理 |

## 规则

- 有扫描元数据时，优先用扫描元数据
- 没把握时，不做盲目跨平台暴力探测
- 用“最小只读探针”回答是否有效
- 凭证不得落盘，不写 shell history
