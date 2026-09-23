# Fishbone (Ishikawa) Analysis

## Applicable Signals
- Investigation direction is completely unclear — you don't know where to start
- Need to brainstorm broadly before focusing
- The fault could originate from any layer of the stack
- Previous focused investigation hit dead ends

## Full-Stack Dimensions

| Dimension | What to check |
|-----------|---------------|
| **Human** | Recent deployments, manual config changes, access control changes, on-call handoff gaps |
| **Infrastructure** | Compute (CPU/memory/disk), network (latency/packet loss/DNS), storage (IOPS/throughput), cloud provider status |
| **Dependencies** | Upstream services, downstream consumers, databases, caches, queues, third-party APIs, certificate validity |
| **Application** | Code bugs, race conditions, resource leaks, error handling gaps, recent code changes |
| **Observability** | Missing metrics, alerting gaps, false alerts masking real issues, delayed detection, log rotation losing data |
| **Config & Data** | Environment variables, feature flags, config drift between environments, data corruption, unexpected data volume, schema changes |

## Investigation Framework

### Step 1: Draw the Fishbone — List All Dimensions
- **Objective**: Ensure no dimension is overlooked
- **Action**: Present the 6 dimensions above. For each, spend 2 minutes brainstorming possible causes specific to the current fault. Don't evaluate yet — just list.
- **Evidence standard**: Record plausible branches grounded in the available context. Mark dimensions with no evidence or candidates explicitly; do not manufacture candidates to meet a count.
- **Branch**: If a dimension clearly dominates (>5 candidates with strong signals) → may want to jump to FTA on that dimension

### Step 2: Score and Prioritize
- **Objective**: Rank candidates by likelihood and verifiability
- **Action**: For each candidate cause, assess:
  - **Likelihood**: Does it match the symptom timeline? Could it produce the observed impact?
  - **Verifiability**: Can we check this quickly with available data?
  Prioritize: high likelihood + easy to verify first. Mark each as: investigate / unlikely / can't verify yet.
- **Evidence standard**: A ranked list with top 3-5 candidates to investigate first
- **Branch**: If all candidates are "can't verify yet" → you have an observability gap, address that first

### Step 3: Evidence Collection
- **Objective**: Gather data on top candidates
- **Action**: Scope each top candidate using the implementation and applicable conditions, then identify observations that distinguish it and collect authorized evidence. Leave broad branches open when they lack a necessary prediction; do not invent a falsifier.
- **Evidence standard**: Mark observed conditions separately from causal conclusions. RULED OUT requires a contradiction of a necessary prediction with adequate coverage; CONFIRMED cause requires the main Skill root-cause gate. Otherwise keep the candidate INCONCLUSIVE.
- **Branch**: If evidence supports multiple candidate mechanisms, use FTA to investigate whether and how they combine; observations alone do not establish each cause.

### Step 4: Focus and Deep Dive
- **Objective**: Drill into evidence-supported leads to investigate the causal mechanism
- **Action**: For each evidence-supported lead, inspect the causal links, conditions and observations that distinguish it from alternatives. Do not require prior root-cause confirmation to investigate a lead.
- **Evidence standard**: A clear causal chain from root cause to symptoms, with evidence at each link
- **Branch**: If the causal chain crosses multiple dimensions → consider STAMP/STPA for systemic analysis

## Switch Signals
- **Switch to FTA**: Evidence supports multiple candidate mechanisms whose logical relationship (AND/OR) needs investigation
- **Switch to Kepner-Tregoe**: Candidates identified but you can't distinguish between them — need IS/IS NOT boundary analysis
- **Switch to SRE Troubleshooting**: One dimension clearly dominates — switch to systematic layer-by-layer within that dimension
- **Switch to STAMP/STPA**: Root cause traces back to control system failures (e.g., autoscaler didn't react, circuit breaker didn't trip)

## Convergence Template
```
Root cause: [the confirmed candidate that explains the symptoms]
Dimensions investigated: [which dimensions were explored]
Ruled out: [what was eliminated and why — this is valuable for documentation]
Causal chain: [root cause] → [mechanism] → [symptoms]
Verification: [what evidence confirmed this and ruled out alternatives]
Prevention: [what to change in the relevant dimension]
```
