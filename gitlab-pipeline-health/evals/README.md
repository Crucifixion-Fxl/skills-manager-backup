# gitlab-pipeline-health evals

离线夹具。结构校验：

```bash
uv run python scripts/validate.py --skill skills/gitlab-pipeline-health
```

这条只检查 JSON 契约，不跑模型。行为评测：新会话只加载本 skill + 该例 `setup`/`prompt` 与 `evals/fixtures/`，不把 `expected_output` 和 `assertions` 喂给被测模型，不访问 GitLab。

| 例 | 卡住什么 |
|---|---|
| 1 | 人读结论 + 三张表 + 根因建议一对一 |
| 2 | 拒绝全局 retry / allow_failure 刷绿 / 加 runner |
| 3 | trace 脱敏 |
| 4 | 没有 live yaml 不编造根因 |
| 5 | 失败率 ≠ 提交质量；不出图 |
| 6 | API 字段清单不是报告 |

通过 = 该例实际输出满足全部 assertion。不要把结构校验绿写成 6 例通过。
