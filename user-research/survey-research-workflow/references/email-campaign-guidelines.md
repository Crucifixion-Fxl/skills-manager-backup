# Email Campaign 指南

用于调研邀请邮件 copy 和 Mailchimp campaign 准备。

## 职责边界

- `research-email-writer` 负责 message strategy 和 copy。
- 可选的 `mailchimp-research-campaign` 或等价外部工具负责生成本地计划、创建 draft、写入内容、单独发送 test email、运行 checklist；本家族不捆绑邮件平台执行器。
- Project owner 确认 copy、audience、CTA links；如果要正式 send 或 schedule，需要离开当前脚本能力单独人工处理。

## Cohort Experience Check

写邮件前先填这张表：

| Cohort | Has experienced | Has not experienced | Risky claims to avoid | Best hook |
|---|---|---|---|---|
|  |  |  |  |  |

除非 cohort 定义已经确认，否则不要暗示用户使用过某个功能、套餐、trial 或 support flow。

## Copy 要求

每个 cohort 邮件需要：

- From name
- Reply-to recommendation
- Subject
- Preview text
- Body
- CTA label
- CTA URL
- Plain text fallback
- Merge tags used
- Support or contact line
- Incentive wording（如已确认）

## 调研邮件质量检查

- 邮件解释了为什么找这类用户。
- 填写时长是真实承诺。
- CTA 跳转到正确 cohort 的表单。
- Subject 和 preview 单独看也能理解。
- 邮件不应像销售 campaign，除非有明确意图。
- Copy 不应诱导用户夸赞某个概念。
- 邮件不承诺每个建议都会被实现。
- 未确认激励政策前，不提 incentives。

## Mailchimp Campaign Spec

按 `production-project-artifact-contract.md` 的契约创建 `campaign_spec.json`。

创建 draft 前必须具备：

- Audience ID 或 list ID
- Segment ID、saved segment ID 或 tag strategy
- Campaign name
- From name
- Reply-to
- Subject
- Preview text
- HTML body 和 plain text body
- Test recipient list
- 已批准且已开放回答的目标平台正式填写 URL

## Mailchimp API 可行性

Mailchimp Marketing API 支持 MVP 所需操作：

- 创建 draft campaign。
- 设置 campaign content。
- 发送 test email。
- 获取 campaign send checklist。

MVP 自动化默认只支持 draft、test 和 checklist。Send / schedule 不在当前脚本能力内，需要在 read-back diff 和 checklist review 后单独强确认并人工处理。

## Mailchimp 安全检查

创建 draft 前，如果出现以下情况必须阻断：

- Audience 或 segment 缺失。
- CTA URL 缺失或不是 cohort-specific。
- Reply-to 缺失。
- Test recipients 缺失。
- 引用了 merge tags，但未定义。
- Incentive language 未确认。

Send / schedule 前，如果出现以下情况必须阻断：

- Send checklist 有 blocking issues。
- Read-back diff 与 `campaign_spec.json` 不一致。
- Audience count 未知或异常。
- Owner 未确认 campaign ID、audience、subject、CTA URL 和 send time。

## 发送后指标

如可获取，记录：

- Sent
- Delivered
- Opens and open rate
- Clicks and click rate
- Click-to-open rate
- Unsubscribes
- Bounces
- Survey completions

这些指标供 `follow-up-strategist` 使用。
