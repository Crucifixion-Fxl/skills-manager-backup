# GitLab MR 示例

### Bad — Driver done 就声称完成

```
Driver → status: done
# → "MR !42 可合并！"  ← 没派 Auditor，可能是 Driver 误判
```

### Bad — 按评论行数判断风险

```text
AI bot 建议改 1 行事务/数据源代码
→ 因为少于 20 行直接自动修复
```

问题：语义风险与行数无关。必须 invoke `face-review-repair`。

### Bad — 手工从记忆拼生产分支

```text
从 staging 相关 MR 中挑几个 commit cherry-pick 到 release
→ 忘记后续 Review 修复和一个生产 profile 配置
```

问题：生产晋级必须以 canonical branch + verified SHA 为输入执行 parity check。

### Bad — 替用户 resolve 人工 reviewer 的 discussion

```
Driver 改完代码就自动 PUT discussions/<id>?resolved=true  ← 违反规则，人工 discussion 只能 reply
```

### Bad — 贴无关文档链接

```
glab mr create --description "User Story: .../README.md"  # 与变更无关，对 reviewer 无用
```

### Bad — 目标 staging 却跳过本地 code-review，靠 CI 打回

```
git push → glab mr create --target-branch staging
# CI 的 code-review gate 报红线：已实现 handler 无 L2/L3 test
# → 又得改一轮、再 push、再等 CI  ← 应该在 Step 2.9 就修掉
```

### Good — push 前先过 code-review，CI 一次过

```
Step 2.9: 本地跑 /code-review → 红线：handler 无 E2E + TODO.md 未同步
→ TODO.md 自动补上并 commit
→ E2E 缺失：问用户 → 用户同意 → invoke testing-strategy 补 L3 用例 → commit
→ 复跑 /code-review → 通过 8.5/10
→ Step 3 push → CI code-review gate 绿
```

### Good — 全链路驱动到真正可合并

```
创建 MR → 派后台 Driver + liveness → Driver 自主修低风险 →
→ 高风险攒清单返回 → 用户逐条决议 → 重派 Driver →
→ Driver done → Auditor 独立验证 VERIFIED → 报告 MR URL
```

---
