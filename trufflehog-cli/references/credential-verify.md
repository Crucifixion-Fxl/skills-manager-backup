# 凭证验证

用于“已发现疑似凭证后”的类型识别与有效性确认。

## 适用场景

- “这个 token 现在还可用吗”
- “轮转后旧凭证是否失效”
- “这串疑似密钥是什么类型”

## 识别顺序

按成本从低到高：

1. 先用已有扫描结果里的 `DetectorName`
2. 再用 [credential-types.md](credential-types.md) 做前缀/格式识别
3. 需要人工交互时用 `trufflehog analyze`
4. 最后做目标平台只读 API 验证

## TruffleHog Analyze

仅在有人可交互操作终端时使用：

```bash
trufflehog analyze --no-update
trufflehog analyze github --no-update
```

不要假设 AI Shell 可以驱动交互式 TUI。

## 可脚本化只读验证

### GitHub PAT

```bash
export CHECK_TOKEN="<token>"
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $CHECK_TOKEN" \
  https://api.github.com/user
unset CHECK_TOKEN
```

### GitLab PAT

```bash
export CHECK_TOKEN="<token>"
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "PRIVATE-TOKEN: $CHECK_TOKEN" \
  https://gitlab.addx.ai/api/v4/personal_access_tokens/self
unset CHECK_TOKEN
```

仅当 token 具备 API scope 时使用该接口。

### AWS Access Key

```bash
AWS_ACCESS_KEY_ID="<key_id>" AWS_SECRET_ACCESS_KEY="<secret>" \
  aws sts get-caller-identity
```

## 结果判定

- `2xx`：通常可用（active）
- `401`：通常失效/吊销（inactive）
- `403`：视平台语义，通常需要补充判断
- 网络错误：`unknown`

报告中仅展示掩码前缀、状态、证据和处置建议。

## LLM 模型 Key 深度检查（可选后续步骤）

对于 LLM 模型类凭证（OpenAI、DeepSeek、Gemini 等），
连通性验证只能确认 key 是否有效，无法判断财务风险和归属。

- 余额与用量分级：[balance-probe.md](balance-probe.md)
- 身份溯源（key 是谁的）：[identity-probe.md](identity-probe.md)

对于模型类 key，建议三步连续执行：有效性 → 余额 → 身份。
