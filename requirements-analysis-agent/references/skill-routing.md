# Company Skill Routing

Evaluate every row and record `SELECTED` or `SKIPPED` with a reason. Every selection is `review_only=true`: load the current approved Skill and apply its instructions in a read-only review pass. User authorization to write a document does not alter this Requirements Attempt boundary; writing starts only as a separate action after independent PO acceptance of the exact Artifact ID/hash.

| Company Skill ID | Runtime load key | Select when | Required review output |
|---|---|---|---|
| `addx:story-craftsman` | `story-craftsman` | Every requirement handled by the Requirements Analysis Agent; problem and maintenance inputs are routed before this table applies | Why/Who/What completeness, Epic/stakeholder structure when applicable, testable AC, NFR and explicit non-goals |
| `addx:uat-story-writer` | `uat-story-writer` | User-visible journeys, multiple roles, permissions, devices, environments, recovery paths, or an explicit UAT/testing gap | Missing user journeys, happy/error/recovery scenarios, Gherkin-ready AC gaps |
| `addx:product-tech-research` | `product-tech-research` | Competitive comparison, open-source choice, unfamiliar/emerging domain, standards question, or evidence-dependent product positioning | Use its Requirements review-only mode; return research questions and evidence gaps without files or a separate interview; do not fabricate current market facts |
| `addx:security-compliance-review` | `security-compliance-review` | Auth/authorization, PII, export/bulk action, third-party API/SDK/webhook, secrets, high-sensitive data, location, retention/deletion, or Agent/Skill supply chain | System-type gate, triggered risk map, blocking clarifications, red lines |

The type-first router normally sends pure problem and maintenance inputs elsewhere before this table is evaluated. If an accepted Requirements envelope is classified `BUG_FIX` after intake because it changes intended behavior, it is still handled by this Agent and the `addx:story-craftsman` review remains mandatory.

## Dynamic extension

Search the approved company Skill catalog when the requirement names a domain not covered above. Select an additional Skill only when its declared trigger matches the requirement and its authority is appropriate. Record its canonical ID, source, content hash, selection reason, and findings exactly like the initial routes.

Do not load community or unapproved Skills as company-policy evidence. Research may cite public projects, but only approved company Skills may contribute a `company_skill_reviews` entry.

## Conflict handling

Use this precedence:

1. Company security, privacy, compliance, and destructive-action red lines.
2. Explicit user intent and verified source facts.
3. Project-specific accepted standards.
4. General product-writing guidance.

If two selected Skills conflict at the same level, mark the Artifact blocked and ask the responsible owner to decide. Preserve both findings; do not average them away.
