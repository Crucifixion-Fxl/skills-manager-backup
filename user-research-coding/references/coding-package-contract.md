# Coding Package Contract

`coding-package.json` 是默认唯一交付文件。字段可按研究方法扩展，但不要拆成多个重复产物。

## 最小结构

```jsonc
{
  "package_version": 1,
  "metadata": {
    "study_id": "string",
    "source_snapshot": "path, URI or hash",
    "source_snapshot_sha256": "64-character hex digest of the coded source snapshot",
    "question_or_task": "participant-facing wording",
    "preceding_context": "optional",
    "population_context": "optional",
    "language": ["en"],
    "n_raw": 0,
    "n_nonempty": 0,
    "n_substantive": 0,
    "unit_of_analysis": "response | segment",
    "counting_unit": "unique_respondent | segment | mention",
    "method": "inductive | deductive | hybrid",
    "depth": "rapid | standard | high_assurance",
    "codebook_version": "v1"
  },
  "codebook": [
    {
      "code_id": "stable_id",
      "label": "reader-facing label",
      "level": "descriptive | interpretive",
      "definition": "string",
      "include_when": ["string"],
      "exclude_when": ["string"],
      "nearby_codes": ["code_id"],
      "examples": [{"kind": "positive | negative | boundary", "text": "sanitized excerpt"}]
    }
  ],
  "coded_units": [
    {
      "response_ref": "stable de-identified reference",
      "segment_id": "optional",
      "source_text": "optional sanitized text",
      "labels": [
        {
          "code_id": "stable_id",
          "evidence_spans": [{"text": "verbatim sanitized excerpt"}],
          "stance": "positive | negative | mixed | neutral | conditional | not_applicable",
          "ambiguity": "optional explanation"
        }
      ],
      "review_status": "single_pass | self_rechecked | independently_reviewed | adjudicated",
      "notes": "optional"
    }
  ],
  "theme_summary": [
    {
      "code_id": "stable_id",
      "n": 0,
      "denominator": 0,
      "counting_unit": "unique_respondent",
      "counterexamples": ["response_ref"],
      "signal_type": "common | directional | rare_important | isolated",
      "interpretation": "evidence-bounded explanation"
    }
  ],
  "quality": {
    "uncoded_n": 0,
    "ambiguous_n": 0,
    "reviewed_n": 0,
    "review_type": "none | same_agent_repeat | independent_model | independent_human",
    "agreement_metrics": [],
    "disagreements": [],
    "limitations": ["string"]
  }
}
```

## 计数与复核

- `source_snapshot_sha256` 绑定本次编码的输入快照；源文本变化后必须重新编码或明确创建新版本。
- 默认报告 `unique_respondent` 人数；一次回答多次提到同一主题只计一人。
- `segment` 或 `mention` 计数只有在研究问题确实需要时使用，并在每张表重复标明单位。
- 多标签主题人数之和可以超过分母。
- `independently_reviewed` 只用于第二编码者没有看到第一轮标签的情况；同一 agent 复跑使用 `self_rechecked` 或 `same_agent_repeat`。
- 一致性按 code 报告，而不是只给一个总分。没有仲裁基准时不把 agreement 称为 accuracy。
- 交付前运行 `scripts/validate_coding_package.py`。校验器会从 `coded_units` 重算每个主题的 n、分母以及 quality 计数，检查重复单元、重复标签、枚举值和 evidence span；不能手工填写汇总后跳过校验。

## 主题与洞察

描述性 code 回答“原文说了什么”；解释性 theme 回答“这些表达可能共同说明什么”。解释性 theme 必须列出支持、反例和适用语境。

低频重要信号使用 `rare_important`，并写明重要性的证据来源，例如潜在安全后果、明确流失、强 workaround 或未预见需求。重要性是分析判断，不等于发生率。
