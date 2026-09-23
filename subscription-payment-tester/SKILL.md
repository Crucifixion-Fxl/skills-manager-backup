---
name: subscription-payment-tester
description: 订阅支付测试器（技能家族）。自动化测试Stripe/Airwallex/Apple Pay支付流程。可以单独测试某个渠道，也可以批量测试所有渠道。当需要测试支付流程、验证订阅状态时使用。
---

# subscription-payment-tester

## Description

订阅支付测试器技能家族，提供三个独立的子技能用于测试不同的支付渠道。

**子技能**：
- **stripe-tester** - 测试Stripe支付流程
- **airwallex-tester** - 测试Airwallex支付流程
- **applepay-tester** - 测试Apple Pay支付流程（iOS）
- **googlepay-tester** - 测试Google Pay支付流程（Android）

**使用方式**：
- 单独使用：只测试某个支付渠道
- 批量使用：测试所有支付渠道并生成汇总报告

## Rules

### Rule 1 — 子技能路由

根据用户需求路由到对应的子技能：

| 用户需求 | 路由到 |
|---------|--------|
| 测试Stripe | stripe-tester |
| 测试Airwallex | airwallex-tester |
| 测试Apple Pay | applepay-tester |
| 测试所有渠道 | 批量调用所有子技能 |

### Rule 2 — 批量测试

当用户需要测试所有支付渠道时：

```bash
# 1. 并行调用三个子技能
stripe-tester run --scenario "新购订阅"
airwallex-tester run --scenario "新购订阅"
applepay-tester run --scenario "新购订阅"

# 2. 汇总测试结果
# 3. 生成综合报告
```

### Rule 3 — 测试报告格式

```json
{
  "test_suite": "subscription-payment-tester",
  "channels_tested": ["stripe", "airwallex", "applepay"],
  "total_scenarios": 15,
  "passed": 13,
  "failed": 2,
  "results_by_channel": {
    "stripe": {
      "passed": 5,
      "failed": 0
    },
    "airwallex": {
      "passed": 4,
      "failed": 1
    },
    "applepay": {
      "passed": 4,
      "failed": 1
    }
  }
}
```

## Examples

### Example 1: 只测试Stripe（快捷路径）

```bash
# 直接使用子技能
stripe-tester run --scenario "新购订阅"
```

### Example 2: 批量测试所有渠道

```bash
# 使用主skill
subscription-payment-tester run-all --scenario "新购订阅"
```

**输出**：
```
[INFO] 开始批量测试所有支付渠道

[1/3] Stripe 测试
  ✓ 新购订阅 - 通过
  ✓ 续费订阅 - 通过
  ✓ 取消订阅 - 通过

[2/3] Airwallex 测试
  ✓ 新购订阅 - 通过
  ✗ 续费订阅 - 失败
  ✓ 取消订阅 - 通过

[3/3] Apple Pay 测试
  ✓ 新购订阅 - 通过
  ✓ 续费订阅 - 通过
  ✗ 取消订阅 - 失败

[SUMMARY]
  总计: 9个场景
  通过: 7个 (77.8%)
  失败: 2个 (22.2%)

[REPORT] 详细报告: payment_test_report.json
```

## Sub-Skills

### stripe-tester

测试Stripe支付流程，包括：
- 新购/续费/升级/降级/取消
- 支付失败/超时/重试
- Webhook回调处理
- 终身VIP保护
- 跨渠道取消

详见：[stripe-tester/SKILL.md](stripe-tester/SKILL.md)

### airwallex-tester

测试Airwallex支付流程，包括：
- 新购/续费/取消
- 支付失败处理
- Webhook回调
- 发卡行拒绝异常

详见：[airwallex-tester/SKILL.md](airwallex-tester/SKILL.md)

### applepay-tester

测试Apple Pay支付流程，包括：
- iOS订阅通知
- 续费/取消/退款
- 空值校验
- Server Notification处理

详见：[applepay-tester/SKILL.md](applepay-tester/SKILL.md)

### googlepay-tester

测试Google Pay支付流程，包括：
- Android订阅通知
- 订阅组冲突处理
- Google Play Billing
- Developer Notification处理

详见：[googlepay-tester/SKILL.md](googlepay-tester/SKILL.md)

## Best Practices

1. **优先使用子技能**：如果只需要测试一个渠道，直接使用对应的子技能
2. **批量测试用于回归**：在发布前运行批量测试
3. **关注失败的渠道**：重点修复失败率高的支付渠道

## Integration with CI/CD

```yaml
# .gitlab-ci.yml
test:payment:
  stage: test
  script:
    # 快速测试：只测试Stripe
    - stripe-tester run --scenario "新购订阅"

    # 完整测试：测试所有渠道
    - subscription-payment-tester run-all --output report.json
```

## References

- [Stripe测试指南](stripe-tester/SKILL.md)
- [Airwallex测试指南](airwallex-tester/SKILL.md)
- [Apple Pay测试指南](applepay-tester/SKILL.md)
