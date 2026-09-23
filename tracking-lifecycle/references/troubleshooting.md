# 埋点常见问题排查

## 排查决策树

```
事件未出现在沙盒？
├─ 在 /bad 中？ → 看 error_title
│  ├─ "schema not found"     → Schema 未注册，回 Step 4 检查平台配置
│  ├─ "missing required field X" → 缺少必填参数，检查 base_schema 合并的 required 字段
│  ├─ "unknown field X"      → 参数名不在 Schema 中，检查拼写或多余字段
│  └─ "type mismatch field X" → 参数类型不匹配，检查 string/integer/boolean
├─ 既不在 /good 也不在 /bad？
│  ├─ Namespace 配置是否正确？（App 扫码 / 后端代码写入）
│  ├─ SDK collector URL 是否指向沙盒？（https://us-prod-log-sandbox.theunismart.com）
│  ├─ 测试用例是否实际触发了目标操作？
│  └─ 网络是否可达？（防火墙 / 代理 / VPN）
└─ 在 /good 但字段不对？
   └─ 回 Step 5 检查代码注入的参数值
```

## 沙盒 Bad 事件常见 Error

| error_title | 含义 | 修复方向 |
|------------|------|---------|
| schema not found | Iglu 注册表中找不到该事件的 Schema | 确认事件已在平台创建并发布，Schema 版本号匹配 |
| missing required field `{name}` | 必填参数未传 | 检查 parameters + base_schemas 合并后的 required 列表 |
| unknown field `{name}` | 传了 Schema 中不存在的参数（如创建时漏写） | 删除多余参数，或用 `POST /api/info/saveOrUpdateEventInfo`（带事件 `id`）给已注册事件补该参数定义（纯 API，详见 [api-reference.md](api-reference.md#编辑已注册事件--给已有事件补参数)）；补完平台会重新生成 schema |
| type mismatch `{name}` | 参数值类型与定义不符 | 常见：数字传了字符串、boolean 传了 0/1 |

## 各端特有问题

### Android

| 现象 | 原因 | 修复 |
|------|------|------|
| Debug 模式下 Toast 弹出 "Track invalid" | `LocalTrackValidator` 拦截了不合规事件 | 按 Toast 提示的 field 修复参数 |
| 事件参数部分字段丢失 | `DesensitizationProcessor` 脱敏失败时整个事件被丢弃（failOpen=false） | 检查日志 "Desensitization failed" |
| SPM 警告 "SPMB is null" | CLK/EXP 事件缺少页面级 SPM 绑定 | 确认 Activity 有 `@SpmPage` 注解 |
| 参数含 `.` 号 | 字段名不能包含 `.`，Debug 下会 Toast 警告 | 改用 `_` 替代 |

### iOS

| 现象 | 原因 | 修复 |
|------|------|------|
| 事件完全不发送，日志有 "[PII]失败" | PIIAnonymizer 处理失败，事件被丢弃 | 检查 SmartDeviceCoreSDK 配置 |
| pre_spm 始终为 "smart_camera" | PV 事件未在 viewDidLoad 中最先调用 | 将 PV 埋点移到 viewDidLoad 最前面 |
| 曝光事件重复上报 | willDisplay 未做去重标记 | 用 state marking 防重（参考 platform-impl-spec） |

### 后端 (Java)

| 现象 | 原因 | 修复 |
|------|------|------|
| 日志 "eventType is no mapping" | AlexaSnowPlowEventMapping 中无此事件类型 | 在 mapping HashMap 中补充映射 |
| ThreadLocal 上下文为 null | 异步调用 failedTrackFromThreadLocal 前未 set | 在异步操作前设置 ThreadLocal |
| 事件延迟到达 | 默认 batchSize=10，积攒后批量发送 | 沙盒模式下 batchSize=1 会实时发送 |

### Flutter

| 现象 | 原因 | 修复 |
|------|------|------|
| 埋点不触发 | Platform Channel 未正确注册 | 确认 FlutterBoostChannelPlugin 已初始化 |
| 曝光事件多次触发 | VisibilityDetector 多次回调 | 用 bool flag 或 Set 去重 |

## Staging 验证特有问题

| 现象 | 原因 | 修复 |
|------|------|------|
| not_uploaded 状态 | 数据同步延迟（Snowplow → Athena → MySQL 需 5-10 分钟） | 等 5 分钟后重试，最多重试 2 次 |
| unpassed 状态 | 有上报但验证失败 | 回 Step 5 检查参数拼写和事件标识 |
| goods_latest < bad_latest | 最近一次上报是失败的 | 修复后重新触发一次成功上报 |

## 数据管道架构

事件从客户端到可查询的完整链路：

```
Client/Backend SDK
  → Snowplow Collector (HTTP POST)
    → Kinesis Stream
      → S3 (原始数据存储)
        → Athena (SQL 可查)
          → MySQL (平台验证表 event_version_status)
```

| 阶段 | 预期延迟 |
|------|---------|
| SDK → Collector | 实时（沙盒 batchSize=1） |
| Collector → S3 | 秒级 |
| S3 → Athena | 分钟级 |
| Athena → MySQL (验证表) | 5-10 分钟 |
