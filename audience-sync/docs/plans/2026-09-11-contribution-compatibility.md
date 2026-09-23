# Shared Skill contribution compatibility

The engineering/skills security validator requires new evaluation suites to use
its version 1 evaluation contract. Preserve the seven existing cases, prompts,
setups and outcomes while assigning contiguous integer IDs and retaining the
old IDs as stable slugs. Each original outcome becomes an assertion check
verbatim; these checks are the single canonical source of detailed expectations.
The expected-output summary describes the reply and effect boundary, rather
than duplicating the checks. Six cases remain read-only comparisons and the
production chain remains a single explicitly authorized effect, never replayed
for a baseline. The harness must provision the unchanged setup before submitting
the unchanged canonical prompt. Unsupported setup means a skipped/reported case,
not ignored fixture inputs or newly inferred authorization for live effects. Contract regression also preserves count gating, exact-run sync,
existing authorization and returned Audience/Brevo URL behavior.

Canonical owner metadata lives explicitly in `contracts/semantic-owner.json`,
which the source-lock generator reads and hashes with the declared files. Its
repository, original origin and availability values are unchanged. Keeping source
metadata in a data file separates it from executable scripts; it adds no scanner
exemption or obfuscation and changes no runtime transport, API schema, credential
handling or result-link validation. The old script literal matched the shared
validator's internal-domain rule; rewriting it to HTTPS would still match.
Existing Chinese description assertions move to the shared language fixture.

Validate the canonical regression suite, source lock and secret checker, then
copy the full distribution into an engineering/skills-shaped temporary tree and
run its scoped format/security validator. Report nonblocking warnings separately
from errors; do not suppress warnings or claim evaluation prompts are live test
results. Keep any later contribution copy byte-identical to the merged source.
