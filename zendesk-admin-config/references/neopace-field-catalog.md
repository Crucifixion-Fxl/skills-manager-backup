# NeoPace Zendesk field catalog decisions

Source: [Feishu 字段配置](https://a4x-paas.feishu.cn/wiki/LKTWwrvdliaea0k84sdcsTKqnye?sheet=JBeMAV), reviewed 2026-08-20.

## Adopted decisions

- Use one form named `NeoPace Customer Support` unless Customer Care explicitly approves another form.
- Use Zendesk native Priority and native CSAT; do not create duplicate custom fields.
- Do not create a custom Owner Agent field. Assignment belongs to Zendesk Assignee/Group.
- Create agent-only integration fields without making them editable in the customer portal.
- Preserve existing/system form fields. The tool only appends managed custom fields.
- Keep all delete operations out of automation v1.

## Decisions still required

| Decision | Owner | Current safe behavior |
|---|---|---|
| Formal support Group and member list | Customer Care | Group management disabled |
| Contributor Agent controlled values | Customer Care | Field disabled |
| Automatic tags and exact trigger conditions/order | Customer Care | Trigger disabled |
| GitLab Work Item Title owner/generation rule | Customer Care + Golf Backend | Field disabled |
| Device Info / Query Info source and write path | Golf Backend | Fields may exist agent-only; integration is not claimed complete |

The candidate tag vocabulary is `brand_neopace`, `source_email`, `source_webform`, `pre_sales`, `order_fulfillment`, `post_sales`, `kickstarter`, `dtc`, `amazon`, `urgent`, and `incident`. Tags are not global Zendesk resources: they become meaningful only when a ticket field, channel, app, or trigger writes them. Do not create a routing trigger until ownership and collision checks are approved.

## UAT after apply

1. Create a sandbox ticket through the customer form.
2. Confirm NeoPace Topic is required and the selected option creates only the intended option tag; the Zendesk system Topic field must remain unchanged.
3. Confirm agent-only integration fields are hidden from customers.
4. Confirm native Priority and CSAT remain unchanged.
5. Confirm existing system fields and unrelated forms/triggers were not removed or reordered.
6. Confirm assignment and tag automation only after their disabled decisions are separately approved.
