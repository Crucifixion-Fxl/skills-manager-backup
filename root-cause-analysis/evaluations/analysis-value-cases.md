# Issue analysis value calibration

These sanitized source summaries and editorial examples are a review corpus,
not model execution receipts or root-cause evidence. No account, trace ID, signed
URL, raw log or production credential is included. Use only the supplied source
when evaluating SOURCE_ONLY behavior; do not assume repository/runtime access.

## Case A: a detailed incident report already exists

Source: [iot-service #192](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/192),
revision `2026-09-08T02:32:53.514000Z`;
[original output](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/192#note_584973).

The author reports a cache-bypassing entitlement read path, reader TempTable
exhaustion during gray traffic, distinct single-user and batch SQL methods,
roughly 14,774 single-user method calls/s, historical mitigation/recovery
windows, and candidate cache/batching/SQL changes. The author already cautions
that method calls are not endpoint QPS, batch SQL is not the single-user metric,
online plans/indexes remain unchecked, and residual errors may be unrelated.

Reject: an English translation of the causal chain followed by the same cache,
SQL and rollout checklist. Repeating the author's caveats is not a new discovery.
Also reject invented SQL measurements or a claim that this attempt confirmed
the author's root cause.

Acceptable editorial SOURCE_ONLY response:

> 本轮没有新增代码或运行验证。原 Issue 已给出完整事故判断；这里把它已有的
> 单用户/批量口径缺口收敛为一个修复优先级问题：先减少读取次数，还是先降低
> 单次查询开销？当前材料不足以替二者排序。
>
> 建议先由有权限的服务/DB 排查方，在同一窗口按单用户和批量方法分别对齐调用量、
> DB 执行次数、扫描量与临时表使用；缺少归因观测就明确列出缺项。
> 再在受控环境固定数据与请求规模，分别比较“只合并重复读取”和“只改写查询”。
> 若前者明显降低 DB 次数及临时表压力，优先评估合并/缓存的一致性边界；若低调用量下
> 单次查询仍消耗很高，则先评估查询与索引。两者都成立就保留联合修复候选。
> 这些检查尚未执行，不代表已选定方案；也不能从一个方法的调用量推出另一 SQL 的消耗。

Quality criteria: acknowledges the source already contains the caveat; adds a
discriminating comparison and decision consequences; separates plans from results;
does not load production for reproduction or invent numerical pass thresholds.

## Case B: proposed principal propagation in an internal API

Source: [iot-service #193](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/193),
revision `2026-09-08T03:09:23.070000Z`;
[original output](https://gitlab.addx.ai/CLOUD/iot-service-unified/-/issues/193#note_584985).

The author reports internal playlist fetch 400 versus public success for the
same media, an absent request principal on a JWT-exempt internal path, and a
silent unsigned-URL fallback. Proposed fix: pass the existing request principal
explicitly; alternate fix: set a request attribute. Some image-only consumers
are reported unaffected. Correction media copies fail before source retention
expires. No independent code read or reproduction is supplied to this evaluator.

Reject: declaring a confused-deputy vulnerability merely because JWT is bypassed;
mandating new authentication without examining existing service trust; prescribing
an unconditional exception in the shared helper without caller compatibility.

Acceptable editorial SOURCE_ONLY response:

> 本轮未独立核验源码或 staging。原文的显式传参方案仍是候选；我补充的检查是：
> 它传递的是已经校验过的主体，还是把调用者可选的 userId 升格为签名授权？
> JWT 白名单本身不足以回答这个问题，也不足以证明存在越权。
>
> 首先由具备准确版本读取权限的后端排查方，连贯检查内部调用者认证、媒体查询
> 所有权约束及签名前校验。如果这些约束已保证主体与媒体匹配，复用并显式传递
> 现有主体即可评估；若确实缺失，再提出补齐校验的设计。检查前不把风险写成漏洞。
>
> 同时核验共享签名方法哪些调用方依赖原有 fallback。只有契约证明必须签名的
> 路径才能直接改为明确失败；其他路径需单独判定兼容性。原文已排除的图片路径
> 暂保留排除结论的来源标记，不凭推测扩大修复范围。
>
> 业务侧优先由有权限的留存排查方确认失败记录中仍可回补的范围与最早到期时间，
> 以决定修复和回补的先后；当前没有这些数值，不声称已计算损失或恢复媒体。

Quality criteria: conditional security reasoning, distinguishes existing authority
from new authority, preserves compatibility and evidence boundaries, prioritizes
time-sensitive impact without claiming unexecuted replay.

## Holdout: no new evidence is available

Input: a short Issue says intermittent upload fails; no timestamps, samples or
approved repository/runtime context. Existing text already asks for a sample.

Reject: fabricating two “likely root causes”, a broad SRE checklist, or marking
completed because the same request was rewritten. Accept: explicitly no new
verification; retain bounded TRIAGED when source/proof gates permit it, or use
NEEDS_INPUT only if one answerable fact actually unlocks source/context/admission.
Do not misclassify a missing identity/package proof as ordinary missing data.

## How to record an evaluation

For each actual run record model and package revision, supplied source/context,
observed tools, output, source-attribution errors, unsupported causal claims,
useful difference, and probe/decision specificity. Mark unavailable execution as
NOT_RUN. An independent reader compares source/output against the criteria;
neither a string-presence test nor these editorial examples prove model quality.

## Fixed-code transfer check: ordered processing

Use the committed [executable fixture](fixtures/ordered_processing.py), pinned to
its full commit SHA, as an additional case outside A/B. Ask the evaluator to read
both functions, state when a value can remain absent, and propose a scoped cause
hypothesis and a falsifier for a failed processing result. Supply the source and
neutral question only; keep this rubric and test expectations out of its prompt.

Reject claims that an optional branch skips every earlier check/assignment or
that an absent primary input implies absence on every path. Require the exact
ordered conditions and a counterexample check before universal claims. For a proposed
failure hypothesis, the falsifying observation must contradict a necessary
prediction under the same conditions, not describe that predicted failure.
Broad unexplored alternatives remain open. The companion unit tests pin fixture
facts only; they do not score prose or establish model/runtime acceptance. Record
actual blind execution for A, B and this transfer case separately from these
editorial expectations, including input, package SHA, output and independent review.

## Fixed-code recovery check: queue lease

Pin [lease_recovery.py](fixtures/lease_recovery.py) to its commit and ask a fresh
evaluator, using only that source, whether an expired lease followed by another
request restores a job's output, and what readbacks distinguish the outcomes.
Keep this rubric and companion tests out of the prompt. Require separation of
lease release, admission/deduplication, actual execution and output persistence;
reject unconditional recovery from expiry or status closure. Test a successful
path as well as scheduler, input and write failures. Fixture tests establish only
these controlled facts; actual model output still needs independent review and
the existing A/B plus unseen-case evaluation, not a keyword-based PASS.
