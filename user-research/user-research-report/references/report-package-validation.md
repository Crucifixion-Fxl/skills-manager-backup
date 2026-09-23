# 两层报告一致性校验

本流程只用于“主报告＋独立证据附录”的交付。单文件草稿不需要 manifest，也不要为了跑校验拆分文件。

## 为什么需要

两层结构降低主要读者的阅读负担，但会增加版本号、链接、关键数字和研究目标在两个文件间漂移的风险。校验器负责可确定判断的部分；它不能判断结论是否合理，也不能替代研究评审。

## 基础检查

`scripts/validate_report_package.py` 总是检查：

- 主报告和证据附录前 20 行中的版本号一致；
- 主报告链接证据附录，证据附录链接回主报告；
- 两个文件中的本地相对链接都可解析到现有文件。

## Manifest 检查

正式交付复制 `templates/report-package-manifest.json` 到报告目录并填写：

- `version`：交付版本；
- `research_goals`：每个范围内目标的稳定标识，以及主报告和证据附录中应出现的唯一文字标记；
- `shared_facts`：需要在两层都出现的关键事实。两边措辞相同时用 `value`；不同时分别用 `main_marker` 与 `evidence_marker`；
- `required_files`：相对 manifest 所在目录的其他必需文件，如开放题原文。

标记应选择稳定、足够具体的短句，不要只填常见数字或单个词。例如，用“有效答卷：989 份”，不要只用“989”。

## 运行

```bash
python3 /path/to/user-research-report/scripts/validate_report_package.py \
  --main /absolute/path/main-report.md \
  --evidence /absolute/path/evidence-appendix.md \
  --manifest /absolute/path/report-package-manifest.json
```

退出码为 `0` 表示结构一致性通过；退出码为 `1` 表示至少一项失败。正式交付保留命令输出或在版本记录中写明校验结果。

## 边界

该脚本使用精确文本标记，适合发现遗漏和版本漂移，不会验证：

- 百分比计算是否正确；
- 两种不同写法是否表达同一个数字；
- 结论是否存在偏差；
- 统计方法是否适合研究设计。

这些仍由结构化分析、统计检查和报告评审承担。不要把脚本通过写成“研究结论已经验证”。
