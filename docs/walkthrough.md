# Guided walkthrough

This walkthrough treats the simulator as a lab rather than a finished agent
framework. Each experiment changes one orchestration decision while leaving the
incident and evaluator stable.

## 1. Establish the baseline

```powershell
py -3 -m aurora_lab compare --seed 101 --trace summary
```

Both strategies have the same available work, production policy, evidence
fixtures, and hidden case. The orchestrated strategy starts four scoped
specialists together, cancels a redundant broad scan, and adds an independent
review loop. The generalist invokes all five investigations one at a time and
performs a one-pass self-review.

Compare four values:

- **Last action** shows when the final approved mutation completes. It is a
  latency proxy, not observed recovery; the first and second verification events
  show initial and confirmed recovery.
- **Cost units** show that coordination and independent review are not free.
- **Score** combines correctness, safety, provenance, resilience, and efficiency.
- **Outcome** prevents a small performance advantage from hiding a failed run.

The canonical case is intentionally solvable by both strategies. A useful
orchestrator should earn its complexity through measurable latency, resilience,
or safety—not merely because the baseline was made incompetent.

Run a wider paired measurement before drawing a conclusion from one seed:

```powershell
py -3 -m aurora_lab matrix --start-seed 0 --count 30
```

The matrix reports success, score, last-action time, first SLO pass, confirmed
recovery, total time, cost, tool calls, paired wins, and per-variant aggregates.

## 2. Run controlled ablations

The paired comparison bundles several design choices together. Use the
five-preset ablation to isolate them:

```powershell
py -3 -m aurora_lab ablation --start-seed 0 --count 30
```

The presets form a deliberate progression:

1. `sequential_generalist` runs every investigation serially and self-reviews.
2. `parallel_generalist` changes only the investigation schedule to parallel.
3. `specialists_no_critic` assigns typed specialist roles but keeps self-review.
4. `specialists_with_critic` adds independent rejection and bounded replanning.
5. `full_orchestration` also cancels the queued broad scan when scoped evidence
   has already answered its question.

All five retain the same approval policy and controlled executor. This keeps a
useful safety boundary from becoming a confound in the orchestration comparison.

Read adjacent rows as controlled comparisons:

- Sequential versus parallel generalist isolates concurrency.
- Parallel generalist versus specialists without a critic isolates role
  specialization. In deterministic v1 this is intentionally a null result:
  ownership changes, while scripted evidence, latency, cost, and outcome do not.
- Specialists without versus with a critic exposes the cost and latency of an
  independent review loop. Because the canonical cases are recoverable by both,
  the current score difference is process evidence, not proof of better recovery.
- Specialists with a critic versus full orchestration isolates proactive
  cancellation. Cancellation credit requires a queued task, a cancelled task
  result, and a matching trace event; summary metadata cannot invent it.

Use `--show-runs` to inspect every seed/preset combination. Write the stable
structured report with `--json ablation.json`, or pin one case family with
`--variant regional_gateway_degradation`. The JSON preserves absent milestones
as `null`; the text report renders them as `n/a`.

The three time columns answer different questions. **Last act** is when the final
approved mutation finishes, **First SLO** is the first passing observation, and
**Confirmed** is the second passing observation. Treat confirmed recovery as the
outcome milestone.

## 3. Inject a matched planning omission

The canonical cases show review cost but do not show review changing recovery.
The first Phase 2 experiment introduces one controlled planning failure:

```powershell
py -3 -m aurora_lab fault-matrix --fault planning_omission --start-seed 0 --count 30
```

For every seed and preset, the matrix runs a matched identity control and a
faulted arm. This study control is deliberately distinct from an ordinary
`--fault none` run: both study arms use the same comprehensive candidate-plan
pipeline and configured review, while only the fault arm removes an action.
Both arms receive the same incident, investigation scripts, evidence, tool and
cost limits, approval policies, and executor. In the faulted arm, the simulator
first constructs the same complete candidate plan for every preset and then
removes the seeded incident's upstream mitigation. The intervention is stable,
one-shot, and recorded with candidate and faulted plan fingerprints.

This setup distinguishes two review contracts:

- Structural self-review executes checks for a non-empty unique action list,
  runbook allow-list membership, trusted evidence citations, and the configured
  review precondition. It shares the planner's causal frame and does not
  independently reconstruct cause-to-action coverage, so the omitted action
  passes this review.
- The independent critic receives only the candidate plan and shared evidence,
  not the fault specification or hidden scenario truth. It reconstructs the
  required causal coverage, identifies the missing mitigation, and triggers one
  bounded repair and final review.

Inspect the contrast directly:

```powershell
py -3 -m aurora_lab run --strategy specialists_no_critic --seed 101 --fault planning_omission --trace full
py -3 -m aurora_lab run --strategy specialists_with_critic --seed 101 --fault planning_omission --trace full
py -3 -m aurora_lab run --strategy specialists_with_critic --seed 101 --fault planning_omission --fault-control --trace full
```

The first command intentionally returns a nonzero process status because a safe
degraded result is not labeled as success.

The self-reviewed run executes only supported, approved actions and reports a
degraded outcome after two failing health checks. This is **safe degradation**,
not recovery. The critic run detects the omission, restores the action, and
requires two passing observations before repair is credited.

The fault matrix reports paired recovery, confirmed recovery, diagnostic and
required-action coverage, policy safety, cost, tool calls, and trace-derived
injection, detection, and repair. Detection requires a matching rejection for
the faulted plan and omitted action. Repair additionally requires a linked
accepted replan, execution of that action, and two numeric, threshold-passing
verification snapshots carrying the same plan and fault provenance. Summary
metadata or a forged `slo_passed` flag cannot create any of these facts.

For the cleanest causal claim, compare `specialists_no_critic` with
`specialists_with_critic`: their schedule, specialist roles, tools, and
cancellation behavior match, while the critic toggle differs. The report's
matched contrast can therefore attribute the recovery difference to this review
loop within the deterministic experiment. Do not generalize that result to
arbitrary faults, model-generated plans, or all critic designs. The full preset
also changes cancellation, so its advantage is not a critic-only estimate.

## 4. Exhaust a worker and reassign its contract

The second Phase 2 experiment moves the intervention from planning to task
execution:

```powershell
py -3 -m aurora_lab fault-matrix --fault permanent_worker_failure --start-seed 0 --count 30
```

The worker matrix retains the five normal presets and adds one study-only
`specialists_with_fallback` preset. For every seed and strategy, its identity
control and fault arm share the incident, task contracts, successful candidate
attempt, fallback fixture, limits, policies, and executor. The control retains
the seeded worker's successful retry. The fault replaces only that second
attempt with a retryable terminal worker failure: its duration, cost, and call
count are unchanged, while its output and evidence are absent. Because the task
allows two attempts, the retry is then visibly exhausted.

Inspect the isolated fallback contrast directly:

```powershell
py -3 -m aurora_lab run --strategy specialists_with_critic --seed 101 --fault permanent_worker_failure --trace full
py -3 -m aurora_lab run --strategy specialists_with_fallback --seed 101 --fault permanent_worker_failure --trace full
py -3 -m aurora_lab run --strategy specialists_with_fallback --seed 101 --fault permanent_worker_failure --fault-control --trace full
```

The first command exits nonzero after safe degradation. Redundant evidence from
other specialists still gives it 100% diagnosis precision and recall, but the
failed worker did not commit tags required for one or more production approvals.
The policy gate denies those actions, the numeric health snapshots remain below
threshold, and communication says the service is still degraded. Independent
critique is not a substitute for unavailable observations.

The second command turns on exactly one additional behavior:
`failure_reassignment`. After observing the terminal task result, the
orchestrator assigns the broad-scan slot to one bounded, separate generalist.
The fallback commits new `FB-*` evidence with its own task and role provenance;
it does not reuse the missing IDs or revive the failed worker. That evidence is
synthesized and cited before the same approval and controlled-execution path
runs. Two passing SLO observations are required before containment is credited.

For seeds 0 through 29, the isolated comparison reports:

- 0% fault recovery without fallback and 100% with fallback;
- 0 percentage-point control lift and +100 percentage-point recovery
  difference-in-differences;
- 30 rescues, 0 regressions, and 100% fault containment with fallback;
- 100% policy safety in every worker-fault arm.

The target worker is still terminally failed at the end. `fault_contained`
means the system routed around that loss, restored trusted evidence, executed
the complete approved response, and observed two valid SLO passes. It does not
mean the worker was repaired. The result is limited to one deterministic worker,
one available separate fallback, fixed budgets, and scripted evidence; it
does not establish resilience to shared outages, multiple losses, or arbitrary
model-backed agents. The fallback fixture materializes deterministic replacement
observations, so this experiment also does not validate a live alternate data
source.

## 5. Deny fallback before it overspends

The third matched fault study keeps the permanent worker failure active in both
arms and changes only the fallback admission capacity:

```powershell
py -3 -m aurora_lab fault-matrix --fault fallback_budget_exhaustion --start-seed 0 --count 30
```

Inspect the exact-fit control and tight-capacity fault directly:

```powershell
py -3 -m aurora_lab run --strategy specialists_with_fallback --seed 101 --fault fallback_budget_exhaustion --fault-control --trace full
py -3 -m aurora_lab run --strategy specialists_with_fallback --seed 101 --fault fallback_budget_exhaustion --trace full
```

Both runs lose the same worker after the same retry lifecycle. The fallback
requests one attempt, four cost units, one call, and its seeded duration. The
control's scoped ledger has exactly that capacity and grants the request as one
atomic unit. The fault ledger has only three cost units, denies the whole vector,
and does not assign or start the fallback.

Follow the active fault's study-intervention lifecycle:

`fault_injected → budget_reservation_requested →
budget_reservation_denied → fault_detected → fallback_skipped → task_cancelled`

The denial's before/after ledger snapshots must match. Scoped usage and the
cancelled task must both show zero attempts, cost, calls, and duration. This is
why the lower total usage in the fault arm is avoided work, not efficiency from
successful recovery.

The matrix is a 2×2. `specialists_with_critic` is the negative control: because
it does not enable reassignment, neither arm reaches fallback admission and both
degrade identically. `specialists_with_fallback` recovers under exact-fit
capacity and degrades safely under tight capacity. The fallback benefit changes
from +100 percentage points to 0, a -100 point difference-in-differences, while
all arms remain policy-safe and budget-safe.

## 6. Follow the control flow

Run the complete trace:

```powershell
py -3 -m aurora_lab run --strategy orchestrated --seed 101 --trace full
```

Find these event sequences:

1. Four `task_started` events share a timestamp: fan-out.
2. Task completions have different timestamps: deterministic parallel latency.
3. `task_retry_scheduled` follows a rate limit and is bounded to one retry.
4. `security_denied` records an embedded instruction without turning it into work.
5. Evidence commits precede synthesis: shared-state provenance.
6. `critique_rejected → replan_created → critique_accepted`: a bounded review loop.
7. Every `action_executed` has a preceding scoped approval.
8. Two verification events precede resolution.

In a planning-omission critic run, also follow
`fault_injected → plan_proposed → critique_rejected → fault_detected →
replan_created → critique_accepted`. After the omitted action executes and two
SLO checks pass, `fault_repaired` closes the linked lifecycle. In a self-reviewed
faulted run, `self_review_completed` appears instead, and no detection or repair
event earns credit.

In a permanent-worker-failure run, follow
`fault_injected → retry_exhausted → task_failed → fault_detected`. The fallback
strategy continues with `task_reassigned → task_started → evidence_committed →
task_succeeded → fallback_completed`; approved actions and two passing health
checks then precede `fault_contained`. Fault, target-task, worker, assignment,
fallback, evidence, plan, and verification identifiers must agree throughout the
chain. Summary metadata alone cannot earn containment credit.

## 7. Change the hidden case

```powershell
py -3 -m aurora_lab compare --seed 202 --trace summary
py -3 -m aurora_lab compare --seed 303 --trace summary
```

The retry defect remains, but the upstream stressor and failed tool change. The
orchestrator must use evidence to choose between bot mitigation, a feature-flag
rollback, and payment-gateway routing.

## 8. Inspect the contracts

Read the code in this order:

1. `model.py` — immutable inputs and outputs between components.
2. `scenario.py` — public evidence versus evaluator-only truth.
3. `faults.py` — named, deterministic interventions at explicit boundaries.
4. `runtime.py` — virtual scheduling, budgets, retries, and event ordering.
5. `agents.py` — synthesis, structural self-review, causal critique, and replanning.
6. `policies.py` — approval capabilities and the sole state writer.
7. `simulation.py` — configuration switches and end-to-end strategies.
8. `metrics.py` — facts reconstructed from events and task results.
9. `evaluation.py` — the transparent scoring rubric.

The most important boundary is not an agent class. It is the contract between
the probabilistic decision layer and deterministic policy/runtime layers.

## 9. Suggested modifications

Make one change at a time and rerun the tests plus the paired comparison.

### Remove independent critique

Compare `specialists_no_critic` with `specialists_with_critic` in the ablation
report, then run the implemented `planning_omission` fault matrix. Inspect how
the matched recovery contrast differs from the clean-arm process-score contrast.

### Tighten the budget

Lower `max_cost_units` in the scenario. Confirm that the ledger denies work
before overspending and that the trace still ends with an auditable degraded
result.

### Bind a different reservation dimension

The implemented study binds `cost_units`. Change the scoped fixture so calls or
duration is the only one-unit deficit, then retain the same exact-fit control.
Verify that atomic denial, zero scoped usage, and safe degradation are invariant
to the binding dimension.

### Change approval scope

Issue a capability for a different action or state version. The executor should
deny it without changing metrics.

### Replay or add a model-backed diagnosis

Run the bundled offline replay without credentials:

```powershell
py -3 -m aurora_lab run --strategy orchestrated --seed 101 --agent-backend replay --model-record tests/fixtures/model_diagnosis_replay.jsonl --trace full
```

For a live call, install `.[openai]`, set `OPENAI_API_KEY`, and use
`--agent-backend openai --model YOUR_MODEL_ID --model-record PATH`. Only
`SynthesizerAgent.comprehensive` is replaced. The critic, planner, approvals,
executor, verification, and fault studies remain deterministic. Invalid output
emits `model_failed` and stops before approval instead of falling back silently.

## 10. Questions to keep asking

- Did another agent add independent information or only more tokens?
- Can the system explain why each task was launched or cancelled?
- Is shared state compact, sourced, and versionable?
- Are retry, review, and delegation loops bounded?
- Can untrusted content ever become authority?
- Can an agent mutate state without an executor-enforced capability?
- Does the evaluator test the whole system rather than only the final prose?
