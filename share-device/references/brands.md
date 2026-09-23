# Brand × Region × Env preset

跟 [create-pir/references/brands.md](../../create-pir/references/brands.md) **完全一致**：share-device 复用 create-pir 的域名 + APP_META 表。

---

## 速查

```bash
python scripts/share_device.py --show-presets
```

输出：

```
  --brand kiwibit    --region us  --env staging   →  https://api-staging-us.kiwibit.com
  --brand kiwibit    --region us  --env pre       →  https://api-pre-us.kiwibit.com
  --brand kiwibit    --region us  --env prod      →  https://api-us.kiwibit.com
  --brand vicohome   --region us  --env staging   →  https://api-staging-us.vicohome.io
  --brand vicohome   --region us  --env pre       →  https://api-pre-us.vicohome.io
  --brand vicohome   --region us  --env prod      →  https://api-us.vicohome.io
  --brand vicohome   --region eu  --env staging   →  https://api-staging-eu.vicohome.io
  --brand vicohome   --region eu  --env pre       →  https://api-pre-eu.vicohome.io
  --brand vicohome   --region eu  --env prod      →  https://api-eu.vicohome.io
  --brand viconature --region us  --env staging   →  https://api-staging-us.vicohome.io
  --brand viconature --region us  --env pre       →  https://api-pre-us.vicohome.io
  --brand viconature --region us  --env prod      →  https://api-us.vicohome.io
```

注意 share-device **不需要 device_api**（不调 `/deviceMsg/*`），所以 preset 比 create-pir 简洁，只有 business_api。

---

## 已 E2E 验证矩阵

| Brand | Region | Env | 验证日期 | 备注 |
|---|---|---|---|---|
| KiwiBit | us | prod | 2026-04-29 | a2xtest → a6xtest，bird feeder `4293ff85...`，全 9 步 15s ✅ |
| 其他 | — | — | 未跑 | 链路通用性高（仅域名差异），按需补 |

---

## tenantId 表

| Brand | tenantId | 业务 API 域 |
|---|---|---|
| VicoHome | `vicoo` | `*.vicohome.io` |
| KiwiBit | `kiwibit` | `*.kiwibit.com`（OEM 独立租户） |
| VicoNature | `vicoo`（OEM 壳） | `*.vicohome.io`（与 VH 共用租户） |

---

## 同步 create-pir preset 的方式

share-device 和 create-pir 的 `PRESETS` / `_APP_META_TEMPLATES` 是**独立副本**（避免跨 skill import 路径耦合）。

后端有变动需要更新时：
1. 在 create-pir 改完
2. diff 同步过来 share-device，主要文件：`scripts/share_device.py`，结构 102~170 行（PRESETS）+ 175~232 行（_APP_META_TEMPLATES）
3. 跑两个 skill 的 `--show-presets` 对比一下输出，确认行数一致

后端 preset 改动频次很低（~每年 1-2 次），手动同步可接受。
