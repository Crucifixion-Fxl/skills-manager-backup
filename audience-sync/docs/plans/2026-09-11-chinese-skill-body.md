# Chinese Skill body and English rollback

## Goal
Make the active Skill guide easier for Chinese-speaking maintainers to review while preserving API usage and execution constraints. Translate SKILL.md and eight references from main f93d88b8da9b651f9ef11c648b28af8dd3ef9fd7.

## Language boundary
Query Plan is presented as 圈人计划; Audience business entities or member sets as 圈定人群. Preserve product names and technical terms such as Project, key, run, schema, revision, binding, registry, hash, idempotency_key and fresh preview attestation. Ordinary action verbs remain Chinese. Preserve API identifiers, URLs, fields, status values, commands, JSON, generated whitelist snapshot and source provenance markers.

Keep referenced heading anchors valid. Move localized documentation expectations to tests/fixtures/guide-zh-CN.json while retaining runtime assertions and generated-inventory checks. The source-lock generator adds only declarations for this fixture and this note.

## Validation
156 tests passed; lint, publisher validation, source lock, registry and secret checks passed. All fenced code blocks and 17 runtime/contract files are byte-identical to the English baseline. Independent paragraph-level review fixed one translation ambiguity: each optional result link is displayed independently when present and validated.

Independent English and Chinese Agents evaluated eight fixed offline scenarios. Core authorization, pagination, parameter types, historical-condition handling, exact A/R/5 binding, pending status, missing transport and single-link results agree. Navigation-link display while queued varies under an ambiguity already present in main; complete output equivalence is not claimed. No new production effects or live end-to-end run was performed.

## Rollback
Retain remote branch rollback/english-skill-20260911 at f93d88b8da9b651f9ef11c648b28af8dd3ef9fd7 as the exact pre-translation English baseline. It is independent of the MR source branch and must remain available after the Chinese MR is merged or deleted. To roll back a local Skill installation, clone that branch and point the host Skill entry to that clone. A rollback MR can also restore its semantic files, matching documentation tests and source lock without overwriting later unrelated work.

MR35/MR36 are separate pending changes and are not included in this translation. This MR does not change the currently installed local Skill or merge itself.
