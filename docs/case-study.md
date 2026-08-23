# Aurora Agent Orchestration Lab — case study

[Open the public Run Explorer](https://aurora-fpl.vercel.app/demo)

## In one sentence

Aurora is a test lab for the machinery around AI agents: it shows how a control
plane assigns work, checks evidence, limits permissions and spending, recovers
from faults, and proves whether a simulated service actually recovered.

## Problem

Most agent demonstrations show the happy path: a request goes in and a polished
answer comes out. That leaves a more important operational question unanswered:
what should happen when a plan is incomplete, a specialist becomes unavailable,
or recovery work costs more than the remaining budget?

Aurora uses a fictional flash-sale outage to make those failures visible and
repeatable. The goal is not to prove that an AI is always right. It is to test
whether the system around an agent stays controlled when something goes wrong.

## Architecture

Aurora is a deterministic Python simulator with scripted roles and a virtual
clock. Each run follows the same understandable path:

1. Specialists investigate telemetry, releases, payments, and security in
   parallel.
2. Their findings enter an append-only evidence board with source information.
3. A planner assembles a response, and an optional independent reviewer checks
   for missing steps. Comprehensive diagnosis can use the deterministic default
   or a validated, recorded model response behind the same contract.
4. Scoped approvals and budget gates decide whether simulated actions may run.
5. Two health checks determine whether recovery is real.

A fault harness changes one condition at a time, while trace-derived metrics
rebuild the outcome from ordered events. The web dashboard turns those traces
into business-language stories without running the agents itself. Its replay
mode progressively reveals agent activity, evidence, decisions, and health
checks while withholding the recorded result until completion. See the
[architecture notes](architecture.md) for the component-level design.

## Three results

### 1. Independent review rescued incomplete plans

Across 30 matched deterministic cases, deliberately incomplete plans recovered
in 0% of runs without the reviewer and 100% with it. The reviewer produced 30
rescues, no regressions, and both strategies remained policy-safe.

### 2. A bounded backup contained a missing specialist

Across 30 matched cases, recovery rose from 0% without fallback to 100% with one
contract-preserving reassignment. The original worker remained failed; recovery
came from replacing its missing evidence rather than hiding the failure.

### 3. Atomic admission prevented partial work and overspend

A four-unit fallback request recovered in 100% of 30 exact-fit cases. When only
three units were available, all 30 requests were denied before dispatch and all
30 recorded zero fallback spend. The service did not recover, but the system
stopped safely instead of starting work it could not finish.

The versioned study reports are in
[`dashboard/public/data/reports`](../dashboard/public/data/reports/).

## Limitations

The published matched studies use scripted agents, deterministic faults, and a
virtual clock. They demonstrate control-plane behavior under stated conditions,
not general model reliability or production performance. An optional model
adapter and offline replay fixture demonstrate how one probabilistic diagnosis
can enter safely, but they are not a repeated-trial model benchmark. The project
does not yet cover correlated outages, multiple simultaneous failures, a
failing backup, or real concurrency. The dashboard is a static artifact reader,
not a live operations console.

## Lessons learned

- Reliability comes from the system around an agent—typed contracts, evidence
  provenance, bounded retries, approvals, and verification—not confidence in a
  generated answer.
- Independent review and fallback solve different failures. A reviewer can
  repair an incomplete plan, but it cannot recreate evidence that was never
  collected.
- Budget checks are safest when they reserve the complete requirement before
  dispatch, so the system either has enough capacity or spends nothing.
- Observability is stronger when outcomes are reconstructed from linked events
  instead of asserted in summary labels.
- The model-backed diagnosis enters behind the same contract, with its model,
  prompt hash, tokens, wall latency, failures, retries, and replay data recorded;
  approvals and execution remain deterministic.

## Build rationale

Aurora was built as a laboratory instead of another chatbot demo because
reliability claims need controlled comparisons. Keeping the incident fixed and
changing one orchestration safeguard at a time makes it possible to explain not
only what happened, but why the outcome changed and what evidence supports that
claim.
