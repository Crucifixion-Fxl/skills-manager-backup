# `library_cloud_promo` 疲劳度测试、观察和交接

## 10. 测试

### 10.1 单元和集成测试

engagement 仓库已有这些测试可参考：

| 文件 | 覆盖点 |
|---|---|
| `backend/internal/domain/fatigue/operator_test.go` | `dismissEscalating` 7/15/30/永久策略 |
| `backend/internal/logic/touchpoint/dismiss_fatigue_test.go` | dismiss 后写冷却 / 永久 |
| `backend/tests/l2/dismiss_fatigue_l2_test.go` | dismiss → 冷却内 evaluate 隐藏 → 过期再现 → 第 4 次永久 |
| `backend/tests/l3/library_cloud_promo_e2e_test.go` | experience variant、CMS slug、deep link、variantKey drift |

### 10.2 Staging E2E

体验命中验证：

1. 创建或选择测试用户和设备。
2. 登录拿 bearer token。
3. 调 `/device/listuserdevices/v4` 找代表设备 sn。
4. 调 `/en/engagement/v1/touchpoints/evaluate`：

```bash
curl -X POST "$API_BASE/en/engagement/v1/touchpoints/evaluate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "slugs": ["library_cloud_promo"],
    "sn": "<device_sn>",
    "language": "en",
    "app": {"appType": "iOS", "version": 10000}
  }'
```

命中时检查：

```json
{
  "slotName": "library_cloud_promo",
  "slotType": "blocks",
  "solutionId": "library-ad-fl-7d",
  "experienceKey": "ad_fl_voucher_1008_us02",
  "fatigueKey": "dismiss_escalating",
  "content": {
    "blocks": [
      {"blockType": "heroBanner", "payload": {"title": "..."}}
    ]
  }
}
```

疲劳度验证：

| 步骤 | 期望 |
|---|---|
| 首次命中并曝光 | 返回 `fatigueKey=dismiss_escalating`，展示 heroBanner |
| 第 1 次关闭 | 后端 `dismiss_count=1`，`next_show_at≈now+7d` |
| 7 天内再次 evaluate | 不返回触点，或 `evalResults.library_cloud_promo=hide_cooldown` |
| 调整时间 / 造数让冷却过期 | 再次 evaluate 可展示 |
| 第 2 次关闭 | `next_show_at≈now+15d` |
| 第 3 次关闭 | `next_show_at≈now+30d` |
| 第 4 次关闭 | `permanently_suppressed_at` 非空，后续 evaluate 不展示 |
| 埋点平台扫码验证 | 用 `addx:tracking-lifecycle` 或 `/check/dashboard/` 生成配置，扫码后真实触发展示、关闭、点击/转化路径，确认事件已发布且字段正确 |

L3 app harness 没有真实 MySQL，不适合完整验证冷却时间流转；冷却行为应在 backend L2 或 staging DB 环境验证。

## 11. 线上观察

R0 `OPEN_PAYWALL` 完整漏斗看板：

- `https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/`
- 筛选 `slot_name=library_cloud_promo` 和 R0 的 `paywall_id`。

SDK 基础排障看板：

- `https://superset-us.addx.live/superset/dashboard/527/`

R1 `DEEP_LINK` 的广告 FreeLicense 结果复用已有领域看板和 SQL：

- engagement 仓库 `observability/superset/free-license/01_ad_fl_touchpoint_funnel.sql`
- engagement 仓库 `observability/superset/free-license/02_ad_fl_assignment_match.sql`
- engagement 仓库 `observability/superset/free-license/03_ad_fl_pool_rollup.sql`

重点维度：

| 维度 | 说明 |
|---|---|
| `slot_name=library_cloud_promo` | 相册首页触点 |
| `solution_id` | CMS slug，例如 `library-ad-fl-7d` |
| `experience_key` | 体验变体，例如 `ad_fl_voucher_1008_us02` |
| `fatigue_key` | 疲劳度变体，例如 `dismiss_escalating` |
| `eval_result` | 是否被 targeting / cooldown / permanent / max impressions 过滤 |
| `source=library_cloud_promo` | 领取页归因 |

Prometheus / 后端指标关注：

- `engagement_fatigue_bucket_total{slug="library_cloud_promo"}`：fatigue feature 分桶是否正常。
- `engagement_fatigue_bucket_total{outcome="pe_error"}`：持续 > 1% 说明 PE / GB feature 可能异常。
- `engagement_fatigue_cooldown_applied_total{slug="library_cloud_promo", outcome="cooldown|permanent|fallback"}`：dismiss 后冷却是否被应用。

## 12. 常见问题

| 问题 | 现象 | 处理 |
|---|---|---|
| admin fatigue variantKey 和 GB value 不一致 | `fatigue_key` 为空或使用 fallback | 让 GB value 等于 admin `variantKey` |
| `gbFeatureIdFatigue` 没挂到触点 | PROACTIVE 展示但没有疲劳维度 | admin touchpoint 基础信息补 `engagement_library_cloud_promo_fatigue` |
| 忘记发布 release | admin 页面有配置，evaluate 仍旧 | 创建发布单并确认 staging snapshot 更新 |
| 把 `heroBanner` 误配成 `textBanner` | 相册首页视觉错位或高度异常 | CMS 和 admin variant 改成 Library 专用 `heroBanner` entry |
| iOS header 不展示 | evaluate 命中但 UI 空 | 确认容器先挂 tableHeaderView，`onContentSizeChange` 是否回调高度 |
| 用户关闭后马上又出现 | dismiss 没写入 `next_show_at` 或 fatigue JSON 没解析 | 查 dismiss API、`dismiss_count`、`next_show_at`、后端 fallback metric |
| 关闭第 4 次仍出现 | `permanentAfter` 漏配或 DB 列未生效 | 查 fatigueJson 和 `permanently_suppressed_at` |
| production 不命中 | prod GB experience rules 当前为空 | 配 prod rules、admin prod release、CMS prod 内容和 App 版本依赖 |

## 13. 接入其他带疲劳度 PROACTIVE 触点的最小输入卡

```yaml
slug: <全小写下划线>
type: PROACTIVE
host:
  surface: <页面 / 容器>
  sdkApi: proactive
  context:
    sn: <设备级触点必填；用户级可空>
cms:
  blockType: heroBanner | inlineBanner | textBanner | ...
  variants:
    - variantKey: <experience variantKey>
      cmsSlug: <banner cmsSlug>
      actionType: OPEN_PAYWALL | DEEP_LINK
      actionConfig: <paywallId/deepLink>
growthbook:
  experienceFeature: engagement_<slug>_experience
  fatigueFeature: engagement_<slug>_fatigue
fatigue:
  variants:
    - variantKey: dismiss_escalating
      fatigueJson:
        maxImpressions: null
        fatigue:
          strategy: dismissEscalating
          phases:
            - cooldownDays: 7
            - cooldownDays: 15
            - cooldownDays: 30
          permanentAfter: 4
  gbDefaultValue: dismiss_escalating
testing:
  evaluateShowsFatigueKey: true
  dismissWritesNextShowAt: true
  cooldownHidesEvaluate: true
  permanentAfterVerified: true
```

## 14. 参考资料

- GrowthBook experience：`https://us-ab-management.addx.live/features/engagement_library_cloud_promo_experience`
- GrowthBook fatigue：`https://us-ab-management.addx.live/features/engagement_library_cloud_promo_fatigue`
- engagement-admin：`https://engagement-admin.addx.live`
- marketing-cms staging：`https://marketing-cms-staging-us.addx.live/admin/collections/engagements`
- Superset 触点 + Paywall 通用 P0 看板：`https://superset-us.addx.live/superset/dashboard/paywall-p0-touchpoint-to-paywall-health/`
- Superset SDK 排障看板：`https://superset-us.addx.live/superset/dashboard/527/`
- engagement 仓库 `docs/architecture/engagement/fatigue.md`
- engagement 仓库 `docs/plans/2026-05-22-us02-library-banner-r0-migration-design.md`
- engagement 仓库 `docs/plans/2026-05-21-us02-r1-ad-fl-experiment-design.md`
- engagement 仓库 `backend/tests/l3/library_cloud_promo_e2e_test.go`
