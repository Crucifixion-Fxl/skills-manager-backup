# Naturehood frozen repeat-run fixture

Captured for deterministic offline evaluation. This fixture is internally complete and does not claim to describe current production state.

## Trusted target and writer state

- The verified current-event binding points to `applications/naturehood#167`.
- Exactly one note belongs to the current writer, is editable, and begins with the exact first line below.
- The runtime holds the single-writer lease for `applications/naturehood + 167 + v1`.

```markdown
<!-- data-review-evidence-index:v1 -->
## 数据证据索引

范围指纹：`viconature + header-image + production`
最近全量发现：2026-09-15T01:00:00Z · Superset 419/419 · GrowthBook 641/641

已核验：
- Superset · `665` · [Header image production](https://superset-us.addx.live/superset/dashboard/vico-nature-paywall-header-image-production/) · production
- GrowthBook · `exp_iilm21mtr59thn` · [Header image experiment](https://us-ab-management.addx.live/experiment/exp_iilm21mtr59thn) · production · running

最近数据结论：
- 付费转化率 4.4%（88/2000），对比 4.0%（72/1800），+0.4pp；95% CI 跨 0，尚不能判断改善。
```

## Fresh bounded read

The cached Dashboard and Experiment IDs still resolve to the same product, environment and status. A fresh bounded aggregate read returns exactly 88/2000 versus 72/1800 under the same UTC+8 seven-day windows and `user_id` deduplication. The normalized report body is byte-for-byte equivalent to the existing note after excluding run timestamps. The correct write action is `no-op` and the external write count is zero.
