# Questionnaire design

Use [Research method](research-method.md) for the decision-first brief and quality checks. Author one canonical survey,
then express it as a Typeform-native body.

## Standard question bank (stem skeletons)

Same research intent uses the same Chinese stem skeleton and suggested scale labels so comparable
constructs can be read across studies. This is Agent research guidance, not a platform template ID.
Do not ask for or submit `question_template_bindings` in chat, and do not lock question type
(Likert vs single-choice vs Typeform `opinion_scale`).

For every item, detect intent first, then fill skeleton blanks from Grill Me and the current decision:

- If the construct matches a bank intent and the decision needs it, keep the skeleton wording, fill
  every `（）` from that brief, keep the suggested scale labels when the bank provides them, and mark
  the questionnaire overview as 题库.
- If it does not match, write the business's own wording, mark the overview as 业务定制 or omit the
  bank mark.
- Do not add a bank question only because the intent exists, and do not invent a parallel stem for a
  matched intent.
- Never leave `（）` in the respondent-facing form. Do not label what a blank must contain beyond the
  current brief.
- If the questionnaire language is not Chinese, translate the filled stem and scale labels while
  preserving construct and polarity; keep the same intent identity.

| 意图 | 骨架题干 | 建议量表 |
| --- | --- | --- |
| 价格感知 | 你觉得（）在（）这个定价怎么样？ | 太便宜 / 有点便宜 / 合理 / 有点贵 / 太贵 |
| 购买或订阅意愿 | 在（）的前提下，你有多大可能（）？ | 非常不可能 / 不太可能 / 一般 / 比较可能 / 非常可能 |
| 可接受价格区间 | 对（），你更能接受的（）价格大概在哪一档？ | 档位由 Agent 按上下文写出 |
| 值不值 | 综合目前提供的能力，你觉得（）按（）值不值？ | 非常不值 / 不太值 / 一般 / 比较值 / 非常值 |
| 功能优先级 | 选择（）时，（）对你有多重要？ | 不重要 / 有点重要 / 一般 / 很重要 / 必不可少 |
| 采用或流失障碍 | 最可能让你不用或停用（）的原因是？ | 选项由 Agent 按上下文写出 |

The suggested scale is canonical choice wording, not a Typeform type. Lower it to a native field with
stable question and choice `ref`s. For 可接受价格区间 and 采用或流失障碍, write buckets or options
from the current brief.

## Typeform rules

- Use a short human study title.
- Give every field and choice a unique stable `ref`; keep it stable across wording-only changes.
- `multiple_choice`, `picture_choice`, `dropdown` and `ranking` (including group/matrix children) must have a
  non-empty `properties.choices` list of `{label, ref}` objects. Never send `choices: []` as a placeholder.
- Types that do not take choices (`short_text`, `email`, and similar) must omit `properties.choices`. Do not send an
  empty array to fill later, and do not drop non-empty options.
- Use native field types, properties, validations, variables and Logic Jumps. Do not invent a local question DSL.
- Validate every logic reference, reachable respondent path and terminal outcome.
- Keep identity, email, uid, batch and personalized links out of respondent-authored fields. Platform manages the
  hidden correlation parameters.
- After creation, compare the exact definition readback with the intended semantics: title, field type, ref, choices,
  required flags, validation and logic.
- Walk representative paths before launch. A synthetic walkthrough is design evidence, not a real response.

Default to a short form whose questions can change the stated decision. Use behavior and recent experience when
possible; label scale anchors; make sensitive or uncertain answers optional; and plan analysis before collection.
