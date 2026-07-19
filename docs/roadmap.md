# Project roadmap

The current lab proves the mechanics and establishes a paired baseline. The
next phases should make the comparison harder and the measurements more honest
before introducing model variability.

## Phase 1 — Measurement and ablations

Status: **five ablation presets and trace-derived milestones implemented;
distribution statistics and stronger scheduler enforcement remain**

`ExperimentConfig` can independently enable or disable:

- parallel tool execution;
- specialist role prompts/contracts;
- independent critique and bounded replanning;
- proactive cancellation;
- bounded failure reassignment.

The shared evidence board, approval gate, and controlled executor remain enabled
for every preset. They are safety invariants held constant, not ablation toggles.

Implemented presets, run against identical cases:

1. `sequential_generalist`
2. `parallel_generalist`
3. `specialists_no_critic`
4. `specialists_with_critic`
5. `full_orchestration`

The permanent-worker study adds `specialists_with_fallback` as an isolated
experimental pair for `specialists_with_critic`; it is not a sixth row in the
normal five-preset ablation.

This separates the value of concurrency, specialization, review, and
cancellation instead of crediting them all to “multi-agent.” Deterministic v1
currently shows specialization as a deliberate null result. Independent review
adds cost and latency without changing recovery on the canonical cases, so its
clean-case score difference is process evidence. The first Phase 2 intervention
supplies a narrow matched case in which review changes recovery, while the
second isolates a bounded fallback response to worker loss.

Implemented measurement improvements:

- Distinguish final action time, first SLO pass, and confirmed recovery.
- Derive policy, cancellation, parallelism, and review facts from events/state.
- Make redundant work a real queued task before awarding cancellation credit.

Remaining measurement improvements:

- Report median and p95 in addition to means.
- Add cost-normalized utility and show latency/cost Pareto tradeoffs.
- Enforce worker count, dependencies, priority, deadlines, and review limits in
  the scheduler rather than only describing them in scenario metadata.

Definition of done: each orchestration feature has an isolated measured effect,
and no score component depends on a strategy-provided self-report.

## Phase 2 — Fault injection

Status: **matched `planning_omission`, `permanent_worker_failure`, and
`fallback_budget_exhaustion` experiments are implemented; evidence, approval,
executor, verification, and broader budget faults remain**

The first implemented fault is an explicit intervention at the planning
boundary. Run its paired matched-control/fault matrix with:

```powershell
py -3 -m aurora_lab fault-matrix --fault planning_omission --start-seed 0 --count 30
```

For each seed and preset, the simulator first builds the same complete candidate
plan. The fault then removes the state-causal upstream mitigation selected by
the seeded incident. Investigation output, evidence, tool and cost limits,
approval policy, and the controlled executor remain unchanged between the
matched arms.

The structural self-review is executable: it checks action shape, uniqueness,
allow-list membership, trusted citations, and review preconditions. Because it
does not independently reconstruct cause-to-action coverage, it accepts the
semantically incomplete plan. The independent critic recomputes that coverage
from trusted shared evidence, rejects the omission, and uses one
bounded replan to restore the action. Presets without the critic finish safely
degraded after failing verification; critic presets require execution of the
restored action and two passing SLO observations before repair is credited.

Fault injection, detection, and repair are reconstructed as linked, ordered
trace lifecycles. The fault matrix pairs every faulted run with a distinct
identity-control study arm and reports recovery, confirmed recovery, diagnostic
precision/recall, required-action recall, policy safety, cost, tool calls, and
detection/repair.
Its isolated critic contrast uses the two specialist presets whose only relevant
toggle is independent review.

The supported claim is intentionally limited: independent causal review repairs
this deterministic causal-coverage omission under the simulator's fixed
contracts. It is not evidence that any critic catches arbitrary errors or that
the effect transfers unchanged to model-generated plans.

The second implemented fault intervenes at the investigation-worker boundary:

```powershell
py -3 -m aurora_lab fault-matrix --fault permanent_worker_failure --start-seed 0 --count 30
```

The seeded target already has a retryable first-attempt failure. The worker
fault preserves the normally successful second attempt's duration, cost, and
tool calls, but replaces its result with a retryable terminal failure and no
committed evidence. The matched identity control retains that same second
attempt's success and the same dormant fallback fixture. Retry exhaustion,
terminal task failure, and detection must agree across trace events and task
results before the intervention receives credit.

Other specialists retain enough evidence to diagnose both causes, but the
missing worker observations contain tags required by one or more production
approvals. Strategies without reassignment therefore finish safely degraded:
unsupported actions are denied, verification remains below threshold, and the
final communication does not claim recovery.

`specialists_with_fallback` differs from `specialists_with_critic` only by the
`failure_reassignment` toggle. It assigns one separate generalist to the
`broad_log_scan` slot, commits new `FB-*` observations with distinct provenance,
and then follows the unchanged synthesis, approval, executor, and verification
boundaries. Containment requires the linked fallback, complete approved action
set, and two valid SLO passes; the original worker remains terminally failed.

For seeds 0 through 29, fault recovery and containment are 0% without fallback
and 100% with it. The control recovery lift is 0 percentage points, the recovery
difference-in-differences is +100 percentage points, all 30 fault cases are
rescued with no regressions, and every arm remains policy-safe. This supports
bounded reassignment for this one deterministic, separate-fallback fixture;
it does not cover multiple workers, shared outages, constrained call/time
budgets, or model behavior.

The third study layers a scoped fallback-admission fault over that same worker
loss:

```powershell
py -3 -m aurora_lab fault-matrix --fault fallback_budget_exhaustion --start-seed 0 --count 30
```

Both arms share the permanent worker failure. The fallback requests one attempt,
four cost units, one call, and its seeded duration. The matched control exposes
that exact vector; the fault exposes only three cost units. A dedicated scoped
ledger either reserves the whole vector or mutates nothing. Denial precedes any
assignment, attempt, call, cost, duration, or evidence, then the simulation
continues through the existing safe-degradation path.

The 2×2 isolated contrast uses `specialists_with_critic` as the no-fallback
negative control and `specialists_with_fallback` as the admission-sensitive
strategy. Fallback benefit is +100 percentage points under exact fit and 0 under
tight capacity, for a -100 point difference-in-differences. Grant, denial,
skip, zero-spend, no-dispatch, policy-safety, and budget-safety claims are all
reconstructed from ordered trace events and ledger snapshots.

Remaining fault families should be introduced independently and then in
combinations:

- stale, contradictory, low-confidence, or missing evidence;
- tight global call/time budgets and alternate scoped binding dimensions;
- repeated rate limits and correlated worker or fallback failures;
- multiple prompt-injection payloads;
- approval delay, denial, expiry, and stale-state races;
- executor acknowledgement loss and idempotent replay;
- verification lag or a false-positive health check;
- irrelevant deployment changes and correlated distractors;
- multi-fault and no-fault cases.

Future matrices should retain matched identity controls, trace-derived safe
degradation, explicit survivor denominators, and stage-specific lifecycles, and
add median/p95 latency and cost-normalized comparisons where relevant.

Definition of done: full orchestration remains safe under every injected fault
and its additional cost produces a measurable recovery or safety advantage.

## Phase 3 — Introduce one model-backed agent

Add a narrow `AgentBackend` protocol while keeping deterministic handlers as the
default. Replace only comprehensive synthesis first.

The model-backed synthesizer must:

- return the existing typed diagnosis contract;
- cite only committed evidence IDs;
- select only allow-listed cause and action IDs;
- receive no private evaluator truth or mutable production state;
- record model, prompt/version hash, tokens, latency, parse errors, and retries;
- distinguish transport retries from semantic correction retries;
- support recorded-response replay for deterministic tests.

Give the single-agent baseline the same model, tools, total token budget, and
production safeguards. Run repeated trials per case because a seed alone no
longer controls model sampling.

Definition of done: model runs can be replayed, invalid structured output fails
safely, and the paired evaluation remains fair.

## Phase 4 — Observability

Status: **a static recruiter-facing Run Explorer and portable showcase bundle
are implemented; critical-path and telemetry export remain**

Implemented:

- guided clean, planning-fault, worker-fault, and budget-fault stories;
- task/control-plane timelines with selectable events;
- evidence lineage, governance chains, numeric verification, and budget views;
- atomic reservation vectors and grant/deny/skip proof;
- raw trace inspection, generic unknown-event rendering, and shareable deep links;
- a versioned relative-path manifest with full run and compact report envelopes.

Remaining observability work:

- parent/dependency links and the critical path;
- model token and latency accounting once a model-backed agent exists;
- redacted JSONL export, with optional OpenTelemetry-compatible spans.

Definition of done: a failed or expensive run can be explained from the trace
without reading implementation code.

## Phase 5 — Real tools and human interaction

Replace simulated tools incrementally:

1. Read-only local fixtures
2. Sandboxed read-only APIs
3. Staged reversible mutations
4. Explicit interactive human approvals
5. Persistent run/evidence storage

Keep the controlled executor boundary even when the upstream agents become
model-backed. A model recommendation should never itself be a production
capability.

## Recommended immediate build

Add one independent stale or contradictory-evidence fault before combining any
faults. Preserve the matched identity-control pattern and require the critic and
policy gate to distinguish low-quality evidence from missing evidence. Then add
median/p95 latency, cost-normalized utility, and critical-path visualization.
After those deterministic measurements are stable, introduce one replayable
model-backed synthesizer behind the existing typed contract.
