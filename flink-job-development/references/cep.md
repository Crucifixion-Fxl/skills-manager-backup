# CEP（Complex Event Processing）

> 由 [../SKILL.md](../SKILL.md) 路由进入。本文聚焦 Flink CEP Pattern API：模式定义、NFA 状态、超时与丢失事件、与 DataStream 集成。

## 何时选 CEP

| ✅ 选 CEP | ❌ 改用其他 |
|---|---|
| 模式匹配："A 后 5 分钟内出现 B 但没有 C" | 简单去重 / 聚合 → SQL |
| 风控规则（多步操作组合判定） | 单一事件触发 → KeyedProcessFunction |
| 用户行为序列识别（漏斗 / 路径） | 模式无时间窗口 / 太复杂 → 直接 KeyedProcessFunction 自己写状态机 |
| 反欺诈（短时间内异常操作链） | 模式数量多（100+） → 单独的规则引擎（Drools / Easy Rules） |

CEP 的 sweet spot：**少量（< 50）规则、规则相对静态、需要时间窗口约束的模式匹配**。

## 依赖

```xml
<dependency>
  <groupId>org.apache.flink</groupId>
  <artifactId>flink-cep</artifactId>
  <version>${flink.version}</version>
</dependency>
```

## Pattern 基本语法

```java
import org.apache.flink.cep.CEP;
import org.apache.flink.cep.PatternStream;
import org.apache.flink.cep.pattern.Pattern;
import org.apache.flink.cep.pattern.conditions.SimpleCondition;
import org.apache.flink.streaming.api.windowing.time.Time;

// 输入流（必须 KeyedStream，CEP state 按 key 隔离）
KeyedStream<Transaction, String> keyed = txStream.keyBy(Transaction::userId);

// 模式：login → 失败 3 次以上 → 5 分钟内成功 login
Pattern<Transaction, ?> pattern = Pattern.<Transaction>begin("first-fail")
    .where(SimpleCondition.of(tx -> tx.action().equals("login_fail")))
    .timesOrMore(3)
    .consecutive()                                         // 连续，不允许中间穿插其他事件
    .next("success")
    .where(SimpleCondition.of(tx -> tx.action().equals("login_success")))
    .within(Duration.ofMinutes(5));                        // Flink 2.x 用 Duration，Time.* 已删除

PatternStream<Transaction> patternStream = CEP.pattern(keyed, pattern);

public static final OutputTag<TimeoutEvent> TIMEOUT_TAG =
    new OutputTag<TimeoutEvent>("timeout") {};

// 匿名类不能 implements 接口，必须用 named class
public static class SuspiciousLoginHandler
        extends PatternProcessFunction<Transaction, AlertEvent>
        implements TimedOutPartialMatchHandler<Transaction> {

    @Override
    public void processMatch(Map<String, List<Transaction>> match, Context ctx,
                             Collector<AlertEvent> out) {
        List<Transaction> fails = match.get("first-fail");
        Transaction success = match.get("success").get(0);
        out.collect(new AlertEvent(success.userId(), fails.size(), success.ts()));
    }

    @Override
    public void processTimedOutMatch(Map<String, List<Transaction>> partial, Context ctx) {
        // pattern 超时未完成（如等到 success 但 5 分钟到了）
        // emit "持续失败但没成功登录" 类型的事件
        ctx.output(TIMEOUT_TAG, new TimeoutEvent(/* ... */));
    }
}

SingleOutputStreamOperator<AlertEvent> alerts = patternStream
    .process(new SuspiciousLoginHandler())
    .uid("cep-suspicious-login").name("cep-suspicious-login");

DataStream<TimeoutEvent> timeouts = alerts.getSideOutput(TIMEOUT_TAG);
```

## 量词与连接

| 写法 | 语义 |
|---|---|
| `.times(N)` | 严格 N 次 |
| `.timesOrMore(N)` | N 次或更多 |
| `.oneOrMore()` | 1 次或更多 |
| `.optional()` | 0 或 1 次 |
| `.consecutive()` | 严格相邻，不容忍中间事件 |
| `.allowCombinations()` | 允许中间穿插其他事件 |
| `.greedy()` | 贪婪匹配（尽可能多匹配） |

| 连接 | 语义 |
|---|---|
| `.next(name)` | 严格紧接（中间不能有其他事件） |
| `.followedBy(name)` | 宽松接（中间可有其他事件，匹配第一个符合的） |
| `.followedByAny(name)` | 宽松接（匹配所有符合的，组合爆炸警惕） |
| `.notNext(name)` | 紧接**不能**是该事件 |
| `.notFollowedBy(name)` | 后续**不能**出现该事件（必须配 `within(...)` 闭合） |

## 超时事件（必须处理，否则匹配不完整数据丢失）

见上方主示例的 `SuspiciousLoginHandler` —— 关键点：

- **必须用 named class**（`extends PatternProcessFunction implements TimedOutPartialMatchHandler`）。Java 匿名类不能写 `implements` 子句
- `processTimedOutMatch` 拿到 partial match 时，通过 side output 把"未完成 pattern"的事件外发，不要静默丢
- 主流 + side output **双路消费**才是完整模式

## NFA 状态与 savepoint 兼容

CEP 用 NFA（Non-deterministic Finite Automaton）管理部分匹配，state 结构 = NFA states + 每个 partial match 的事件 buffer。

**升级时的 state 兼容性**：

| 改动 | state 是否兼容 |
|---|---|
| Pattern 名（`"first-fail"`）改名 | ❌ 不兼容（NFA state key 变） |
| 加 / 删 Pattern step | ❌ 不兼容（NFA 拓扑变） |
| 改 `where` 条件实现（但 step 结构不变） | ✅ 兼容（条件每次 evaluate 新数据） |
| 改 `within(...)` 时间 | ⚠️ 部分兼容（已存在的 partial match 仍用旧时间窗口） |
| 改 `times(N)` 的 N | ❌ 不兼容 |

**实践建议**：
- 改 Pattern 结构 → 必须 **stop-with-savepoint** + 业务可接受 partial match 丢失（或写迁移工具）
- 改条件逻辑 → 可热升级，但要确认 stateful 算子的 `.uid()` 没变

## 与外部规则配置联动（动态规则）

CEP 不支持动态加载规则——Pattern 是编译期 fix 的。如要"规则配置在 Redis / DB，运行时变更"，三个方案：

1. **Broadcast State**（推荐）：规则放 broadcast stream，主 stream join 规则后用 `KeyedBroadcastProcessFunction` 自己实现匹配逻辑（不用 CEP）
2. **CEP + 多 Pattern 静态注册**：规则有限且更新少，预编译多 Pattern + `union()` 分发
3. **改 Drools / 自定义规则引擎**：规则数量大、频繁变更，CEP 不适合

## CEP 特有常见坑

| 坑 | 现象 | 解决 |
|---|---|---|
| 没 keyBy 直接 `CEP.pattern(stream, ...)` | 全局 state，跨 key 错误匹配 | 必须先 `.keyBy(...)` |
| `notFollowedBy` 没配 `within(...)` | DAG 编译失败 / 永远不触发 | `notFollowedBy` 必须有时间窗口闭合 |
| `followedByAny` 组合爆炸 | TM 内存涨、checkpoint 巨大 | 改 `followedBy`（取第一个）或加 `greedy()` 限定 |
| 改 Pattern 后直接重启 | NFA state 不兼容，匹配混乱或异常 | stop-with-savepoint + 业务确认 partial match 损失可接受 |
| Pattern 名重复 | DAG 编译失败 | Pattern step 名全 job 唯一 |
| 超时事件没处理 | partial match 数据丢失（pattern 永不完成） | 实现 `TimedOutPartialMatchHandler` + side output |
| `within(...)` 用 processing time | 事件回放时不可预期 | 用 event time（CEP 默认跟 stream watermark） |
| 没设 `.uid()` | savepoint 恢复时 NFA state 丢 | `process(...).uid("cep-<name>").name("cep-<name>")` |

## 下一步

- savepoint 升级（CEP 升级风险高，强读） → [state-time-checkpoint.md](state-time-checkpoint.md)
- 测试 CEP（构造事件序列 + harness） → [testing.md](testing.md)
- 部署 → [deployment.md](deployment.md)
