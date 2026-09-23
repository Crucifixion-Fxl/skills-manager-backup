# Publishing and cold-start delivery

This bounded update aligns the existing Skill with the local publishing format:
Chinese purpose/trigger description, Description, Rules and Good/Bad Examples.
It preserves the ten-file semantic import inventory and existing fixed client.

Native delivery provides guidance plus host tools and schemas. Full-clone
delivery also includes executable Python client and frozen contracts. Active
references distinguish these artifacts so a fresh Agent never needs an absent
script/contract or a new SDK to follow the guide.

Evaluation cases live in `evals/evals.json`. Their expected outcomes are authored
acceptance criteria, not execution evidence. Compare with/without Skill only for
read-only mixed-key, native-host and missing-transport cases. The existing
explicitly authorized production cohort is country US, language es, registration
on 2026-09-07 UTC. Its full-chain case runs once with the Skill and requires an
exact5 preview, successful exact-run materialization5 and exact sync completion.
The full-clone cold test receives only Skill, one injected key, the business
cohort and bounded authorization. It receives no Project, origin, route,
plan/run or destination IDs, and no host preflight answers. The Agent discovers
Project via authenticated self-context, checks live gates and selects the
advertised unambiguous ready Brevo Folder. A missing dependency is a truthful
incomplete result, not a reason to inject the answer into the test.
A different preview count stops new effects. Preserve the user's covering
authorization and record actual returned IDs instead of supplying historical IDs.

Validation uses the real publisher SkillValidator on a temporary semantic folder
named audience-sync, plus repository document/contract tests and runtime gates.
The coordinator records actual cold-start outcomes separately; no eval result or
production readiness is claimed merely because format validation succeeds.
