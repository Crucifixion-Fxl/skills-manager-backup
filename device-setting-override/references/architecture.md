# Setting Override 后端实现速查
# Backend Implementation Quick Reference

本文件是 SKILL.md 的辅助参考。后端代码在 `iot-service-cloud`,主要文件路径以
`iot-service-cloud/src/main/java/com/addx/iotcamera/...` 为根。

## 1. 入口 — DeviceSettingService.initSetParameterRequest

`/deviceMsg/setting` 调用栈最终走到 `DeviceSettingService.initSetParameterRequest`,
该方法在型号默认 setting 构造完成后,调用 `applySettingOverride` 做按 SN 覆盖。

`applySettingOverride` 内部分 5 个 stage,每个 stage 对应监控 label:

| Stage | 行为 | 失败兜底 |
|:--|:--|:--|
| `fetch` | `SettingOverrideDao.selectBySn(sn)` | base 未动,直接返回原 response |
| `parse` | `JSON.parseObject(row.getOverrideJson())` 取 `$.value` | base 未动 |
| `merge` | `SettingOverrideMerger.merge(base, override)` 原地深合并 | catch 后用 `JSON.toJSONString(base)` 备份反序列化还原 |
| `emit` | observability emit 指标 + 结构化日志 | 同上备份还原 |

## 2. Inviolable Invariant — 不可违反的最高准则

```
覆盖路径任何阶段抛异常都不得影响给设备的响应
```

实现方式: `applySettingOverride` 在进入 `merge` stage 之前先 `JSON.toJSONString(request.getValue())`
做深拷贝快照;catch-all 块在 emit 出 `setting_override_failed_total{stage}` 后,把快照反序列化回 `request.getValue()`。

设计文档: `docs/architecture/setting-override/overview.md` §7。
US AC-1.10 是该不变量的验收标准。

**对运维侧的含义:** 即使你 admin upsert 写错了 JSON 形状或 DB 临时挂了,
设备拿到的 setting 仍是型号默认,**不会**有半合并的脏数据。所以做实验
不需要担心"写坏了影响存量设备"。

## 3. 合并语义 — SettingOverrideMerger

| 场景 | 行为 |
|:--|:--|
| Map x Map | 递归(只访问 override 里出现的 key,兄弟节点逐值保留) |
| List x List | 整组替换(NOT 下标合并) |
| scalar x scalar | 替换 |
| any x null | no-op (override 里写 null 表示**不动**) |
| 类型不一致 | override 胜出 |
| 空 `{}` 作 override | no-op |
| override 里出现 base 里没有的新 key | 写穿(C7 path) |

**关键含义:** override 里没出现的 key,base 里的对应项**保留不动**。
所以 `{"value":{"pirSensor":{"MotionDetection":{...}}}}` 不会动 `value` 下
其它字段如 `liveSpeakerVolume` / `pir` / `codecProfile`。

## 4. Admin API 端点

全部在 `/inner-api/setting-override`,鉴权方式相同(AK/SK HmacSHA1),
来源 `SettingOverrideAdminController`:

| Method + Path | 用途 | 关键参数 | 限制 |
|:--|:--|:--|:--|
| `POST /upsert` | 批量新增/更新 | body: `{items[], repushSetting}` | items ≤ 1000;单 JSON ≤ 16KB |
| `POST /delete` | 按 SN 列表 OR 按 experiment 删除 | body: `{serialNumbers[], experiment, repushSetting}` 二选一 | SN 列表 ≤ 1000;按 experiment 删时 repush 只覆盖前 1000 SN |
| `GET /?sn=<sn>` | 单 SN 查询 | `?sn=<sn>` | 未命中 result=404 + `SETTING_OVERRIDE_NOT_FOUND` |
| `GET /?experiment=<tag>&pageNum=<n>&pageSize=<k>` | 按 experiment 分页 | pageSize 默认 200,硬上限 1000 | pageSize > 1000 时 clamp 不报错 |
| `GET /stats` | 实验级聚合活跃数 | 无参 | 仅统计 active 行 |

所有写/读端点都会发 admin 审计指标 `setting_override_admin_action_total{action, result, ...}`,
`result ∈ {success, validation_error, server_error, not_found}`。

## 5. Validator 强约束

来源 `SettingOverrideValidator`,**任 1 项违反 → 整批 1000 项被拒**(AC-2.7 不允许部分成功):

| 约束 | 错误码 |
|:--|:--|
| SN 长度 1-64,非空 | `SETTING_OVERRIDE_SN_INVALID` |
| `experiment` 正则 `^[a-z0-9_-]{1,64}$` | `SETTING_OVERRIDE_EXPERIMENT_INVALID` |
| `expireAt` 必填,且 `0` 或 `> now` | `SETTING_OVERRIDE_EXPIRE_AT_INVALID` |
| `overrideJson` ≤ 16 KB | `SETTING_OVERRIDE_JSON_TOO_LARGE` |
| `overrideJson` 必须是 JSON object | `SETTING_OVERRIDE_JSON_PARSE_FAILED` |
| `overrideJson` 不能为空 `{}` | `SETTING_OVERRIDE_JSON_EMPTY` |
| 顶层 key ⊂ `{name, id, time, value}` | `SETTING_OVERRIDE_KEYS_INVALID` |
| `value` 必须是 JSON object(若存在) | `SETTING_OVERRIDE_VALUE_NOT_OBJECT` |

## 6. 缓存 TTL

`SettingOverrideAdminController` 上 upsert / delete 都有 `@CacheEvict(allEntries=true,
cacheManager="settingOverrideCacheManager")`;跨 pod 陈旧度由该 cache manager 的 60s TTL 兜底。
所以"upsert 后能立刻看到"在本 pod 是即刻,跨 pod 最迟 60s。

## 7. 已知缺口(MR 2063 review 未修)

| Issue | 影响 |
|:--|:--|
| AC-1.7 `emitUnknownKeys` 定义但生产主路径未调用 | 未知 key 进入 override 不会 WARN 审计 |
| `delete-by-experiment` 的 repush 只覆盖前 1000 SN | 大于 1000 行的 experiment 删除时,后续 SN 要等下次主动拉 setting 才回到默认 |
| L3 E2E 缺失 | 仅 L1/L2 测试,端到端断裂只能靠 staging 烟雾测试兜底 |

操作建议:
- 删除 >1000 行的 experiment → 用 `list --all` + `delete --sn-file` 分批模式
- 大批量 upsert 后必须人工抽查 1 个 SN 调 `deviceMsg/setting` 验证字段生效

## 8. 监控指标

| 指标 | label | 含义 |
|:--|:--|:--|
| `setting_override_applied_total` | `model_no, experiment` | 设备请求 setting 时命中并成功合并 |
| `setting_override_failed_total` | `stage(fetch/parse/merge/emit/unknown)` | 覆盖路径异常(不影响响应,仅审计) |
| `setting_override_admin_action_total` | `accessKey, action, result, experiment` | admin API 审计 |

## 9. 上游文档链接

- User Story (含 Inviolable Invariant): `docs/product/user-stories/setting-override.md`
- Architecture Overview: `docs/architecture/setting-override/overview.md`
- Observability Design: `docs/architecture/setting-override/observability.md`
- Metric Spec: `docs/architecture/setting-override/metric-spec.md`
- Tracking Design: `docs/architecture/setting-override/tracking-design.md`
- Testing Strategy: `docs/testing/services/setting-override/strategy.md`
- Staging Smoke Test (curl 版): `docs/testing/services/setting-override/staging-smoke-test.md`
- 引入 MR: gitlab.addx.ai/CLOUD/iot-service-unified/-/merge_requests/2063
