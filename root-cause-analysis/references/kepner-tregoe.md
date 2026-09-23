# Kepner-Tregoe Problem Analysis

## Applicable Signals
- Problem is intermittent or hard to reproduce
- The boundary of the problem is fuzzy (affects some users but not others, some regions but not all)
- Multiple hypotheses exist but you can't distinguish between them
- Need precise problem definition before investigation

## Investigation Framework

### Step 1: IS / IS NOT — Define the Problem Boundary
- **Objective**: Precisely define what the problem IS and what it IS NOT across 4 dimensions
- **Action**: Fill in this matrix:

| Dimension | IS (affected) | IS NOT (unaffected) |
|-----------|---------------|---------------------|
| **WHAT** | What object/service has the problem? | What similar objects/services do NOT have it? |
| **WHERE** | Where is the problem observed? (region, cluster, node) | Where is it NOT observed? |
| **WHEN** | When did it start? When does it occur? | When does it NOT occur? (time of day, load level) |
| **EXTENT** | How many? How much? How severe? | What is the boundary of impact? |

- **Evidence standard**: Fill observed cells with specific factual data; mark unobserved cells unknown and record the missing comparison.
- **Branch**: If the IS NOT column lacks observations, mark it unknown and seek an authorized comparison. Missing unaffected observations do not establish universal impact.

### Step 2: Identify Distinctions — What's Unique About the IS?
- **Objective**: Find what distinguishes the affected from the unaffected
- **Action**: For each IS/IS NOT pair, ask: "What is distinctive about the IS that is not true of the IS NOT?" Look for:
  - Different software versions
  - Different configurations
  - Different infrastructure (node type, AZ, network path)
  - Different data characteristics
  - Different traffic patterns
- **Evidence standard**: A list of distinctions with factual backing
- **Branch**: If no distinctions found → the IS/IS NOT boundary may be wrong, revisit Step 1

### Step 3: Identify Changes — What Changed Near the Distinction?
- **Objective**: Find changes that could explain the distinctions
- **Action**: For each distinction, ask: "What changed in or around this distinction recently?" Check:
  - Deployments, config changes, infrastructure changes
  - Traffic pattern shifts, data migration, certificate rotation
  - Upstream/downstream dependency changes
  - Time correlation: did the change happen before the problem started?
- **Evidence standard**: A list of changes with timestamps, correlated to the problem timeline
- **Branch**: If no changes found → the problem may be a latent issue newly exposed, broaden the time window

### Step 4: Develop and Test Hypotheses
- **Objective**: Generate cause hypotheses and test them against the IS/IS NOT data
- **Action**: For each change + distinction combination, form a hypothesis: "If [change] caused [distinction], it would explain [IS] and would NOT affect [IS NOT]." Test each hypothesis:
  - Does it explain ALL the IS data?
  - Does it explain ALL the IS NOT data?
  - An unexplained boundary blocks confirmation. Eliminate a scoped hypothesis only when reliable IS/IS NOT evidence contradicts a necessary prediction under the same applicable conditions; missing data or a possible coexisting cause is not a refutation.
- **Falsifier direction check**: Write the scoped hypothesis's necessary prediction first. If the proposed falsifying observation is true under those same conditions, it must contradict that prediction. An observation of the predicted failure supports it instead; do not label that observation a falsifier. If a broad branch has no such discriminating prediction yet, retain it OPEN and narrow its scope.
- **Evidence standard**: One or more hypotheses that pass both tests
- **Branch**: If no hypothesis passes → you're missing data, go back to Step 1 to refine boundaries

### Step 5: Verify the Most Probable Cause
- **Objective**: Confirm the winning hypothesis is the actual cause
- **Action**: Design a verification test:
  - Can you reproduce the problem by reintroducing the cause?
  - Can you fix the problem by removing the cause?
  - Does the fix hold across all IS cases?
- **Evidence standard**: Controlled reproduction or removal tests distinguish the scoped mechanism from alternatives under verified conditions; record coverage and unresolved explanations.
- **Branch**: If verification fails, return to Step 4 with the result. Check intervention fidelity, measurement coverage and necessary predictions before marking the hypothesis eliminated; a failed attempt alone is inconclusive.

## Switch Signals
- **Switch to SRE Troubleshooting**: IS/IS NOT shows the problem is universal (no IS NOT cases exist) — boundary analysis won't help, use layer-by-layer instead
- **Switch to Fishbone**: Can't find distinctions — need broader brainstorming about possible dimensions
- **Switch to STAMP/STPA**: The cause traces back to a control loop failure (e.g., the distinction is between systems with and without a certain safety mechanism)
- **Layer FTA**: Multiple hypotheses pass the IS/IS NOT test — need FTA to determine which combination of factors is the actual cause

## Convergence Template
```
Root cause: [the change that explains the IS/IS NOT boundary]
IS/IS NOT summary: [key boundary that identified the cause]
Key distinction: [what was unique about affected cases]
Key change: [what changed that triggered the problem]
Verification: [how we confirmed — reproduction / fix / both]
Prevention: [how to prevent this change from causing this problem again]
```
