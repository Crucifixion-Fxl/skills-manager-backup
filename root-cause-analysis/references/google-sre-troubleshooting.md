# Google SRE Troubleshooting

## Applicable Signals
- Production incident with user-visible impact
- Rough direction is known (which service/layer is suspect)
- Observability data available (metrics, logs, or traces)
- Need systematic elimination rather than creative brainstorming

## Investigation Framework

### Step 1: Symptom — What exactly is broken?
- **Objective**: Precisely define the observable problem
- **Action**: Collect error messages, user reports, alerting data. Quantify: what percentage of requests/users are affected? Since when?
- **Evidence standard**: You can state "X is happening since T, affecting N% of Y"
- **Branch**: If symptoms are unclear or contradictory → consider Kepner-Tregoe first

### Step 2: Triage — How bad is it and what's the blast radius?
- **Objective**: Determine severity, urgency, and scope
- **Action**: Check if it's getting worse. Identify affected regions/users/services. Determine if mitigation is needed before investigation.
- **Evidence standard**: Severity level assigned, blast radius mapped, mitigation decision made
- **Branch**: If mitigation is urgent, pause investigation → mitigate → resume

### Step 3: Examine — What changed?
- **Objective**: Identify recent changes that could correlate
- **Action**: Check recent deployments, config changes, infrastructure changes, dependency updates, traffic pattern shifts. Check the timeline: did symptoms start after a specific change?
- **Evidence standard**: List of changes in the relevant time window, with correlation assessment
- **Branch**: If a strong correlating change is found → jump to Step 4 to verify. If no changes found → broaden the search to dependencies and infrastructure.

### Step 4: Diagnose — Narrow down layer by layer
- **Objective**: Isolate the faulty layer/component
- **Action**: Work from user-facing layer inward:
  1. Client / CDN / Load Balancer
  2. API Gateway / Ingress
  3. Application service
  4. Dependencies (DB, cache, queue, external APIs)
  5. Infrastructure (compute, network, storage)
  At each layer: check metrics (latency, error rate, saturation), logs (errors, warnings), traces (where time is spent)
- **Evidence standard**: One or more layers identified as the source of the problem, with supporting data
- **Branch**: If multiple layers are affected → suspect cascading failure, consider STAMP/STPA

### Step 5: Fix — Apply and verify
- **Objective**: Resolve the issue and confirm resolution
- **Action**: Apply the fix (rollback, config change, scaling, etc.). Monitor the same metrics that showed the problem.
- **Evidence standard**: Symptoms have resolved, metrics have returned to baseline
- **Branch**: If symptoms persist, return to Step 4 and check whether the intervention reached the affected path, removed the proposed mechanism and was observed for an adequate recovery window. Persistence blocks a recovery claim; it refutes the scoped hypothesis only when it contradicts a necessary prediction under verified conditions. Other contributing causes may coexist.

### Step 6: Prevent — Ensure non-recurrence
- **Objective**: Prevent the same failure mode from recurring
- **Action**: Identify what systemic gap allowed this to happen. Propose: monitoring improvement, guard rails, testing, process change, documentation.
- **Evidence standard**: Actionable prevention items listed with owners

## Switch Signals
- **Switch to Fishbone**: After Step 3, if no changes found and you have no hypothesis at all
- **Switch to Kepner-Tregoe**: If the problem is intermittent and you can't consistently reproduce it at any layer
- **Switch to STAMP/STPA**: If Step 4 reveals cascading failures across multiple control boundaries
- **Layer FTA**: If Step 4 identifies multiple possible causes at the same layer

## Convergence Template
```
Root cause: [one sentence]
Causal chain: [change/event] → [mechanism] → [symptom 1, symptom 2, ...]
Verification: [scoped test, intervention and measurement coverage, result, and remaining alternatives; correlation alone is not confirmation]
Prevention: [what to change — monitoring / guard rail / process / test]
```
