# Naturehood moderation: verified historical reference

Read-only verification date: 2026-09-09.

Verification source: LiteLLM staging-US UI, `Virtual Keys` detail view for
`nh-moderation-staging-us`. The check covered the displayed model allowlist, budget, RPM, TPM,
expiry, and Team fields. No Secret Value was opened, copied, or recorded.

`nh-moderation-staging-us` is an existing Naturehood moderation Virtual Key. It is a reference
fact for this staging-US workload only, not a default, template, or production recommendation.

| Field | Verified value |
|---|---|
| Environment / region | staging US |
| Allowed model alias | `nh-text-lite` |
| Budget | `$10/月` |
| RPM | `30` |
| TPM | Unlimited |
| Expiry | Never |
| Team in current UI | `Unknown` (historical state) |

For every new Key, set budget, period, RPM/TPM, concurrency, expiry, and owner/team from its own
capacity, SLA, and cost requirements. Do not copy this Key's `$10/月`, RPM `30`, TPM, expiry, or
`Unknown` Team state. Each staging/prod and US/EU combination needs its own Key, spend boundary,
and region-scoped Vault delivery.

The target contract for moderation is to deliver only `LITELLM_API_KEY` through Vault and
ExternalSecret and to use the `nh-text-lite` alias. This UI verification does not attest the live
Pod injection, runtime traffic, fallback, prompt-storage, or logging state; those require separate
GitOps and runtime acceptance evidence. The actual secret value is not recorded here.

For moderation, the required behavior is to treat `429`, `5xx`, and timeouts as chain failures and
use the business degradation path; they are not evidence that user-generated content is
non-compliant. Prompt storage must remain disabled, and raw prompts, completions, attachments, and
UGC body text must not enter logs or exports.
