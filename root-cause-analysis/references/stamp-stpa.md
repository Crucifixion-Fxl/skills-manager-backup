# STAMP/STPA (Systems-Theoretic Accident Model and Processes)

## Applicable Signals
- Cascading failure across multiple systems or services
- Safety/control mechanisms failed to prevent or contain the incident
- The failure involves feedback loops, autoscaling, circuit breakers, or other control systems
- Individual components worked correctly but the system as a whole failed
- "Everyone did their job but things still broke"

## Key Concepts

**Control structure**: The hierarchy of controllers, actuators, sensors, and controlled processes in your system. In software terms:
- **Controllers**: Autoscalers, circuit breakers, rate limiters, deployment pipelines, alerting systems, human operators
- **Actuators**: The mechanisms controllers use to act (scaling API, feature flags, rollback commands)
- **Sensors**: Metrics, logs, traces, health checks, alerts — how controllers observe the system
- **Controlled process**: The actual service/infrastructure being managed

**Unsafe control actions (UCAs)**: Four ways a controller can fail:
1. **Not providing** a control action when needed (autoscaler didn't scale up)
2. **Providing** a control action that shouldn't be given (circuit breaker tripped prematurely)
3. **Too early/too late** (alert fired 20 minutes after the issue started)
4. **Stopped too soon/applied too long** (rollback was partial, rate limit stayed on after recovery)

## Investigation Framework

### Step 1: Identify the Control Structure
- **Objective**: Map the control hierarchy relevant to the incident
- **Action**: Draw the control structure involved in the failure:
  - What controllers were supposed to prevent/detect/mitigate this?
  - What sensors feed them information?
  - What actuators do they use?
  - What is the controlled process?
  Include human controllers (on-call, deployment team) alongside automated ones.
- **Evidence standard**: A clear control structure diagram (can be text-based) with all relevant controllers, sensors, and actuators identified
- **Branch**: If only one controller is involved and it simply broke → this is a component failure, use SRE Troubleshooting instead

### Step 2: Analyze Control Loops for Unsafe Control Actions
- **Objective**: Identify which control actions were unsafe and why
- **Action**: For each controller in the structure, check all 4 UCA types:
  1. Did it fail to act when it should have? (e.g., alert didn't fire)
  2. Did it act when it shouldn't have? (e.g., autoscaler scaled down during peak)
  3. Was the timing wrong? (e.g., health check interval too long)
  4. Was the duration wrong? (e.g., retry storm continued after service recovered)

  For each UCA found, trace why it happened:
  - **Incorrect process model**: Controller had wrong information about system state
  - **Sensor failure**: Metrics were missing, delayed, or wrong
  - **Actuator failure**: The control action was issued but didn't take effect
  - **Conflicting controllers**: Two controllers issued contradictory actions
- **Evidence standard**: List observed UCAs with supporting causal evidence and explicit unknowns; do not force a root cause for each.
- **Branch**: If observed UCAs are sensor-related, prioritize the sensing and feedback path, including upstream inputs and controller interpretation. This narrows the investigation; it does not establish observability as the root cause.

### Step 3: Find Constraint Failures
- **Objective**: Identify which safety constraints were violated and why they weren't enforced
- **Action**: For each UCA, identify the safety constraint it violated:
  - "System shall scale up when CPU exceeds 80%" — was this constraint defined? Was it implemented? Was it testable?
  - Look for: missing constraints, poorly specified constraints, constraints that conflict with each other, constraints that worked in isolation but failed in combination
- **Evidence standard**: A list of violated constraints with assessment of whether they were defined, implemented, and effective
- **Branch**: If constraints were well-defined but violated due to implementation bugs → switch to FTA on the specific component

### Step 4: Trace Systemic Factors
- **Objective**: Understand the organizational and design factors that allowed the control structure to fail
- **Action**: Look beyond the immediate technical failure:
  - **Design gaps**: Was the control structure adequate for the failure mode that occurred?
  - **Communication gaps**: Did controllers have the information they needed?
  - **Feedback gaps**: Were there missing feedback loops that would have caught this earlier?
  - **Evolution gaps**: Has the system changed in ways that invalidated existing control structures?
- **Evidence standard**: Systemic factors identified that go beyond "X component broke"
- **Branch**: If the systemic factor is purely organizational → this is beyond technical RCA, flag for management review

## Switch Signals
- **Switch to SRE Troubleshooting**: The failure is a simple component failure, not a control structure breakdown
- **Switch to FTA**: A specific control component failed in a complex way — need to understand the causal tree within that component
- **Switch to Kepner-Tregoe**: The control structure failure is intermittent — need to define the IS/IS NOT boundary of when controls work vs. don't

## Convergence Template
```
Root cause: [which control loop(s) failed and why]
Unsafe control actions: [list of UCAs identified]
Control structure gap: [what was missing or broken in the control hierarchy]
Systemic factor: [design/communication/feedback/evolution gap]
Verification: [how we confirmed the control structure analysis]
Prevention: [how to fix the control structure — new constraints, better sensors, redesigned feedback loops]
```
