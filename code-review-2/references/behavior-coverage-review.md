# 行为差异与漏报复核

finding 复现验证不能替代入口覆盖核验；新增流程合理，也不证明旧入口仍满足契约。

## 触发与记录层级（唯一判定入口）

Step 0 按实际行为影响选择下列层级；主流程、角色和 CI 均引用本节，不另设条件。
配置、常量、声明式规则和 Skill 指令也可能改变行为，不能只按扩展名判断。

| 层级 | 触发条件 | 必须完成的记录与复核 |
|---|---|---|
| 不适用 | 纯格式、机械替换或文档变动，且已确认不改变行为 | 一行“不适用 + 理由 + diff 范围证据”；不生成 JSON |
| 简要自查 | 其他实际行为变更，且不满足下列详细覆盖条件 | 简短记录 base/head 的受影响入口、用户意图、相关条件、结果和证据；零 finding 也自查漏项。无需 JSON、检查器或专门的独立漏报 agent |
| 详细覆盖 | 改变影响多个入口/用户意图的共享入口、分发或兼容行为；改变授权、租户隔离、交易或不可逆数据副作用等高风险行为；或用户/项目明确要求详细覆盖 | 逐入口记录、漏报复核及内部 coverage JSON；固定 commit 对必须运行随包检查器 |

改动小不自动豁免高风险行为；仅触及共享文件或支付模块也不自动触发，需说明实际行为影响。
普通 CR 没有 JSON 不代表未完成；简要自查仍需列出证据缺口，可选的详细记录也必须真实。

范围限本次行为及其直接调用者、必要桥接和直接下游，能判定入口最终结果即停止；不扩展为
全仓审计或历史债治理。沿用 [`review-data-contract.md`](review-data-contract.md) 和 CI 取证边界：
材料不全则列缺口，不把无法访问解释成不存在，不绕过认证、网络或用户授权限制。

## Step 0 → Step 3：先列入口，再比较结果

1. 固定 base/head SHA，从原始变更提取分发点、公共方法及状态/副作用；在授权范围内核调用点、
   事件/路由注册及相关测试，按“入口 + 用户意图 + 相关条件”列清单。同一方法的不同调用意图
   不合并；只列本次相关的可达开关、provider 或状态组合，不做无界笛卡尔积。
2. 逐项追踪“触发 → 桥接 → 分发 → 最终页面/返回值/状态/副作用”，比较新流程与需保留的旧入口。
   实现仍存在不等于原入口仍可到达。按验收和旧契约区分保持、预期改变、回归；回归须证明同一
   前提在 base 可工作、head 失败及本 MR 的因果改动，不能把所有行为变化都报 bug。
3. 记录结论及前后证据：`verified` 另标 `preserved / intended_change / regression`；回归 finding
   仍需 Step 4 事实、归因与严重性复核。`not_applicable` 写排除理由和证据，不删除候选项；
   `unverified` 写缺少的源码、契约或必要运行证据及验证方法，不计为 confirmed bug。
4. 静态链路足以证明时不强求 E2E，也不声称已运行验证。测试须覆盖实际入口、相关意图并绑定
   revision；测试文件存在、CI 总体 success 或共享方法覆盖率高不能替代入口核验。
   外仓调用者绑定仓库和固定 revision，拿不到必要源码则标未核验。

简要自查用短句即可；详细覆盖按下述 schema 记录。主报告第 4 段只保留覆盖结论、关键证据
定位和未决缺口，不重复展开覆盖表或 JSON。

## Step 4：漏报自查与详细覆盖复核，即使 finding 为零

所有行为变更在总结前重新从原始变更检查入口、意图和条件是否遗漏，不能只复核已报告 bug。
简要自查由当前审查者完成，不为此增派 agent。

详细覆盖复用现有“追溯链路审查员”。支持独立 agent 时，提供固定 diff、范围来源和必要调用
文件，**不先给主审的 finding、结论或覆盖表**；独立列入口/意图/条件及证据，再对账。
prompt 见 [`review-roles.md`](review-roles.md)。没有可靠独立 agent 时，单 agent 重新枚举再对账，
记录 `self_check` 和“未独立漏报复核”；不能换角色名或 reviewer ID 声称独立执行，不循环重审。

保留主审及复核/重枚举两份实际记录与执行标识。只统一同义 ID，不静默删项制造集合相等；
缺项补审前标 `unverified`，新候选 bug 回到 finding 复现验证。完整性依赖原始材料和重新枚举，
不由勾选数量证明。

## 测试材料与源码审查覆盖分开判断

MR 描述/附件是可用测试材料，本地 L3 受限且有具体说明、平台未采集测试结果或发布关系
未知，按 `l3-release-gate.md` 完成评审；不把这些情况填成行为覆盖 `unverified/incomplete`。
行为记录核验的是实际入口、意图与代码变化；运行验证未执行单独注明，不虚构执行成功。
真实入口源码未读到或未完成必要枚举才属于本节的审查覆盖缺口。

## 记录校验与完成条件

仅详细覆盖要求 coverage JSON，固定 commit 对在总结前必须运行随包检查器。
JSON 只作内部临时记录，不在主报告重复展开。检查器只读 stdin，不联网、不取源码、
不执行记录中的命令、不写 MR；证据记录只含定位信息，不含完整 diff、token 或用户数据。
使用可信的随包脚本，不执行被审 MR 提供的同名脚本。CI 可暂存本次执行记录，不上传或
提交为报告 artifact；最终仍按现有 5 段格式输出。

```bash
node <skill-path>/scripts/validate_behavior_coverage.cjs <fixed-base-sha> <fixed-head-sha> < coverage.json
```

SHA 参数来自 Step 0 固定的 revision，不能从待校验 JSON 自取以绕过旧版本检查。记录结构：

```json
{
  "schema_version": 1,
  "base_sha": "<40位base SHA>", "head_sha": "<40位head SHA>",
  "reviewer_id": "<实际主审执行标识>",
  "scope": {"status": "complete", "evidence": [{"revision": "<head SHA>", "locator": "<完整范围索引>"}]},
  "applicability": "required",
  "entries": [{
    "id": "<入口/意图/条件的稳定ID>", "entry": "<调用点到分发点>",
    "intent": "<用户意图>", "condition": "<相关条件>",
    "before": "<旧结果>", "after": "<新结果>", "expected": "<应有结果及依据>",
    "status": "verified", "outcome": "preserved",
    "before_evidence": [{"revision": "<base SHA>", "locator": "<file:line>"}],
    "after_evidence": [{"revision": "<head SHA>", "locator": "<file:line>"}]
  }],
  "audit": {
    "base_sha": "<base SHA>", "head_sha": "<head SHA>",
    "reviewer_id": "<实际复核执行标识>", "mode": "independent", "status": "complete",
    "required_entry_ids": ["<独立枚举并对账后的ID>"],
    "evidence": [{"revision": "<head SHA>", "locator": "<调用清单/源文件位置>"}],
    "notes": "<枚举依据、补审项和实际执行记录位置>"
  }
}
```

- 本仓前后证据必须分别包含固定 base/head 的定位。外仓证据可追加 `repository`，并绑定
  该仓完整 SHA；不允许用浮动分支替代。本次新增入口的 before 证据可引用 base 的注册表/
  完整树索引，说明该入口尚不存在，而不是捏造旧文件行号。
- `regression` 另填 `finding` 引用；`not_applicable / unverified` 填 `reason`。
  `required_entry_ids` 包含所有候选项（含有证据排除的项）。schema 保留
  `applicability=not_applicable` 的兼容形式（`entries=[]`、顶层 `reason` 和 scope 证据、无需 audit），
  不因此要求无行为变更的审查生成 JSON。
- `self_check` 的 reviewer ID 必须与主审相同；`independent` 必须不同。ID 和证据必须来自
  实际记录，不能为了过校验伪造。检查器只检查格式、SHA 与集合对账，无法证明独立性、
  证据位置真实或入口完整；两边同时删项仍可能通过，不能据此声称没有遗漏。
- 退出码 `0`：记录完整或确有依据不适用；`1`：覆盖未完成；`2`：输入/调用无效。
  `complete` **不是审查通过**：含已确认 bug 的完整记录也会返回 0。
- 必要行为存在未核验项、范围不全，或应做的详细覆盖对账/复核未完成，审查状态为 `incomplete`；不得报
  “无回归/通过/有条件通过”，不评分、不把覆盖缺口计入产品 bug。已确认红线仍按原规则阻断。
- 详细覆盖的检查器不可用时如实记“未执行”，不声称机器校验通过；未提交工作树没有固定 head，使用
  实际 diff/相关文件摘要绑定快照，人工执行同样的清单对账并明确说明。只读取证缺口仍须
  标未完成，不能因改用人工就跳过检查。新增提交或快照改变后，旧记录不可用于新结论。

这是 Skill 执行要求和记录完整性检查，不是已经接入所有调用方的强制平台门禁。
仓库 CI 只运行检查器自身的契约测试，不自动验证每个业务 MR 的行为覆盖记录。
