# Fault Tree Analysis (FTA)

## Applicable Signals
- Multiple possible causes at the same layer or across layers
- Need to exhaustively enumerate and eliminate possibilities
- The failure may result from a combination of conditions (AND logic)
- Complex system where a single cause doesn't fully explain the symptoms

## Investigation Framework

### Step 1: Define the Top Event
- **Objective**: Precisely state the undesired outcome (the failure you're investigating)
- **Action**: Write a single, unambiguous statement. Example: "API response time exceeds 5s for >10% of requests in region US-East"
- **Evidence standard**: A clear, measurable top event statement that all stakeholders agree on
- **Branch**: If you can't define a single top event → there may be multiple independent issues, investigate separately

### Step 2: Expand the Causal Tree
- **Objective**: Build a tree of all possible causes using logic gates
- **Action**: For each node, ask "what could directly cause this?" and connect with:
  - **OR gate**: Any single child is sufficient for the parent under the stated model; children may coexist or be correlated. OR does not establish statistical independence or mutual exclusion.
  - **AND gate**: All children must be true simultaneously to cause the parent (combined conditions)
  Work top-down, expanding until you reach "basic events" — things you can directly verify with evidence.

  Common dimension checklist for expansion:
  - Code: recent changes, bugs, race conditions, resource leaks
  - Config: misconfigurations, feature flags, environment drift
  - Infrastructure: compute, network, storage, DNS
  - Dependencies: upstream/downstream services, databases, caches, queues
  - Data: corrupt data, unexpected volume, schema mismatch
  - External: third-party APIs, cloud provider issues, certificate expiry
- **Evidence standard**: A complete tree where every leaf is a verifiable basic event
- **Branch**: If the tree is growing too large (>20 leaves) → use Fishbone first to narrow dimensions, then FTA on the most likely branch

### Step 3: Verify Leaf Nodes
- **Objective**: Test each basic event against available evidence
- **Action**: For each leaf, determine: TRUE (evidence confirms), FALSE (evidence rules out), or UNKNOWN (no data). Prioritize verification by:
  1. Leaves that are cheapest/fastest to check
  2. Leaves under OR gates (one TRUE is enough)
  3. Leaves under AND gates (one FALSE eliminates the branch)
- **Evidence standard**: Every leaf marked TRUE/FALSE/UNKNOWN with supporting data
- **Branch**: If too many UNKNOWN leaves → need more observability data before continuing

### Step 4: Locate Root Cause
- **Objective**: Trace from verified TRUE leaves back up through the tree
- **Action**: Apply gate logic: OR gates need any TRUE child, AND gates need all TRUE children. A satisfied path identifies a candidate sufficient combination within the model. Verify the causal edges, timing, magnitude, scope and competing explanations before applying the main Skill root-cause gate; true leaves alone do not prove the model explains the incident.
- **Evidence standard**: A clear path from basic events through gates to the top event, with evidence at every node
- **Branch**: If multiple valid paths exist → there may be multiple contributing causes, list them all

## Switch Signals
- **Switch to Fishbone**: Tree is exploding with too many branches — need dimensional filtering first
- **Switch to Kepner-Tregoe**: Many leaves are UNKNOWN and you need to narrow the search space before gathering more data
- **Switch to STAMP/STPA**: The tree reveals that the failure involves feedback loops or control system breakdowns rather than simple causal chains

## Convergence Template
```
Root cause: [basic event(s) that are TRUE and produce the top event]
Causal chain: [basic event] --[OR/AND]--> [intermediate event] --> [top event]
Gate logic: [which gates were satisfied and why]
Verification: [evidence for each TRUE leaf]
Prevention: [what to change to make the root cause basic events impossible or detectable]
```
