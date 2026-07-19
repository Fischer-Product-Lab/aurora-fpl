"""Deterministic, auditable fault injection for orchestration experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256

from .agents import ActionPlan, CAUSE_ACTION, UPSTREAM_CAUSES
from .model import EvidenceItem, Metadata, TaskStatus, ToolAttempt
from .runtime import ResourceVector


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """A named intervention applied at one explicit orchestration boundary."""

    name: str
    stage: str
    description: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.stage.strip() or not self.description.strip():
            raise ValueError("fault fields cannot be empty")


NO_FAULT = FaultSpec(
    name="none",
    stage="none",
    description="Run the unmodified orchestration pipeline.",
)
PLANNING_OMISSION = FaultSpec(
    name="planning_omission",
    stage="planning",
    description=(
        "Remove one seeded, state-causal upstream mitigation from an otherwise "
        "complete candidate plan."
    ),
)
PERMANENT_WORKER_FAILURE = FaultSpec(
    name="permanent_worker_failure",
    stage="investigation",
    description=(
        "Replace one worker's successful final retry with a seeded terminal "
        "failure that commits no evidence."
    ),
)
FALLBACK_BUDGET_EXHAUSTION = FaultSpec(
    name="fallback_budget_exhaustion",
    stage="fallback_admission",
    description=(
        "Reduce one scoped fallback cost reservation below the exact amount "
        "required for its otherwise unchanged attempt."
    ),
)

FAULT_SPECS = (
    NO_FAULT,
    PLANNING_OMISSION,
    PERMANENT_WORKER_FAILURE,
    FALLBACK_BUDGET_EXHAUSTION,
)
FAULT_BY_NAME = {fault.name: fault for fault in FAULT_SPECS}
FAULT_NAMES = tuple(fault.name for fault in FAULT_SPECS)
INJECTABLE_FAULT_NAMES = tuple(
    fault.name for fault in FAULT_SPECS if fault is not NO_FAULT
)


def fault_for(name: str | None) -> FaultSpec:
    """Resolve a public fault name while treating ``None`` as no fault."""

    normalized = "none" if name is None else name
    try:
        return FAULT_BY_NAME[normalized]
    except KeyError as error:
        choices = ", ".join(FAULT_NAMES)
        raise ValueError(
            f"unknown fault {normalized!r}; choose one of: {choices}"
        ) from error


def _fingerprint(plan: ActionPlan) -> str:
    material = "|".join(
        (
            str(plan.version),
            ",".join(sorted(plan.diagnosis.causes)),
            ",".join(sorted(plan.diagnosis.evidence_ids)),
            ",".join(action.action_id for action in plan.actions),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class PlanningFaultApplication:
    """Immutable result of materializing a fault against one candidate plan."""

    fault: FaultSpec
    original_plan: ActionPlan
    plan: ActionPlan
    injected: bool
    fault_id: str | None
    omitted_action_id: str | None
    candidate_fingerprint: str
    faulty_fingerprint: str


_WORKER_TRACE_METADATA_KEYS = frozenset(
    {
        "candidate_evidence_ids",
        "candidate_fingerprint",
        "candidate_status",
        "fault_id",
        "fault_injected",
        "fault_name",
        "fault_role",
        "fault_stage",
        "faulty_fingerprint",
        "faulty_status",
        "selection_seed",
        "study_fault_name",
        "target_attempt",
        "target_task_id",
    }
)


def _evidence_material(item: EvidenceItem) -> tuple[object, ...]:
    """Return a stable, complete representation of one evidence item."""

    return (
        item.evidence_id,
        item.claim,
        item.source,
        item.task_id,
        item.role.value,
        item.observed_at_ms,
        item.value,
        item.confidence,
        item.parent_ids,
        item.trusted,
        item.metadata,
    )


def _worker_script_fingerprint(script: tuple[ToolAttempt, ...]) -> str:
    """Fingerprint a script while excluding the envelope that names the hash.

    Instrumentation fields include the candidate/faulty fingerprints and are
    therefore deliberately excluded from the semantic payload.  All original
    attempt metadata remains covered.
    """

    material = tuple(
        (
            attempt.duration_ms,
            attempt.status.value,
            attempt.output,
            attempt.error,
            tuple(_evidence_material(item) for item in attempt.evidence),
            attempt.retryable,
            attempt.cost_units,
            attempt.tool_calls,
            attempt.security_denial,
            tuple(
                (key, value)
                for key, value in attempt.metadata
                if key not in _WORKER_TRACE_METADATA_KEYS
            ),
        )
        for attempt in script
    )
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class WorkerFaultApplication:
    """Immutable result of materializing a fault against one worker script."""

    fault: FaultSpec
    original_script: tuple[ToolAttempt, ...]
    script: tuple[ToolAttempt, ...]
    injected: bool
    fault_id: str | None
    target_task_id: str | None
    target_attempt: int | None
    candidate_evidence_ids: tuple[str, ...]
    candidate_fingerprint: str
    faulty_fingerprint: str
    trace_metadata: Metadata = ()
    fault_role: str = "study_intervention"
    study_fault_name: str | None = None


def apply_worker_fault(
    script: tuple[ToolAttempt, ...],
    fault: FaultSpec,
    *,
    seed: int,
    target_task_id: str,
    target_attempt: int = 2,
    fault_role: str = "study_intervention",
    study_fault_name: str | None = None,
) -> WorkerFaultApplication:
    """Replace one successful final retry with a deterministic worker failure.

    ``target_attempt`` is one-based.  The permanent failure remains marked
    retryable so the scheduler, whose task contract is bounded to two
    attempts, emits an explicit retry-exhaustion lifecycle.  The attempt is
    nevertheless terminal because no third attempt exists.
    """

    if not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if not target_task_id.strip():
        raise ValueError("target_task_id cannot be empty")
    if target_attempt <= 0:
        raise ValueError("target_attempt must be positive")
    if fault_role not in {"shared_condition", "study_intervention"}:
        raise ValueError("fault_role must identify a shared condition or intervention")
    if study_fault_name is not None and not study_fault_name.strip():
        raise ValueError("study_fault_name cannot be empty")

    candidate_fingerprint = _worker_script_fingerprint(script)
    if fault is NO_FAULT or fault.name == NO_FAULT.name:
        return WorkerFaultApplication(
            fault=fault,
            original_script=script,
            script=script,
            injected=False,
            fault_id=None,
            target_task_id=None,
            target_attempt=None,
            candidate_evidence_ids=(),
            candidate_fingerprint=candidate_fingerprint,
            faulty_fingerprint=candidate_fingerprint,
            fault_role=fault_role,
            study_fault_name=study_fault_name,
        )
    if fault.name != PERMANENT_WORKER_FAILURE.name:
        raise ValueError(f"fault {fault.name!r} is not a worker intervention")
    if target_attempt != 2:
        raise ValueError("permanent_worker_failure targets the second attempt")
    if len(script) < target_attempt:
        raise ValueError("worker fault target script needs a second attempt")

    target_index = target_attempt - 1
    initial_attempt = script[0]
    if (
        initial_attempt.status is TaskStatus.SUCCEEDED
        or not initial_attempt.retryable
    ):
        raise ValueError(
            "worker fault target must follow a retryable first-attempt failure"
        )
    candidate = script[target_index]
    if candidate.status is not TaskStatus.SUCCEEDED:
        raise ValueError("worker fault target attempt must be successful")
    if not candidate.evidence:
        raise ValueError("worker fault target attempt must contain evidence")

    candidate_evidence_ids = tuple(
        item.evidence_id for item in candidate.evidence
    )
    semantic_failure = replace(
        candidate,
        status=TaskStatus.FAILED,
        output=None,
        error=(
            "PERMANENT_WORKER_FAILURE: assigned worker remained unavailable "
            "after its bounded retry"
        ),
        evidence=(),
        retryable=True,
    )
    semantic_script = (
        script[:target_index]
        + (semantic_failure,)
        + script[target_index + 1 :]
    )
    faulty_fingerprint = _worker_script_fingerprint(semantic_script)
    fault_material = "|".join(
        (
            fault.name,
            str(seed),
            target_task_id,
            str(target_attempt),
            candidate_fingerprint,
            faulty_fingerprint,
        )
    )
    fault_id = sha256(fault_material.encode("utf-8")).hexdigest()[:16]
    trace_metadata: Metadata = (
        ("fault_injected", "true"),
        ("fault_id", fault_id),
        ("fault_name", fault.name),
        ("fault_role", fault_role),
        ("fault_stage", fault.stage),
        ("target_task_id", target_task_id),
        ("target_attempt", str(target_attempt)),
        ("candidate_status", candidate.status.value),
        ("faulty_status", semantic_failure.status.value),
        ("candidate_evidence_ids", ",".join(candidate_evidence_ids)),
        ("candidate_fingerprint", candidate_fingerprint),
        ("faulty_fingerprint", faulty_fingerprint),
        ("selection_seed", str(seed)),
    ) + (
        (("study_fault_name", study_fault_name),)
        if study_fault_name is not None
        else ()
    )
    failed_attempt = replace(
        semantic_failure,
        metadata=semantic_failure.metadata + trace_metadata,
    )
    faulted_script = (
        script[:target_index]
        + (failed_attempt,)
        + script[target_index + 1 :]
    )
    if _worker_script_fingerprint(faulted_script) != faulty_fingerprint:
        raise RuntimeError("worker fault instrumentation changed its payload")

    return WorkerFaultApplication(
        fault=fault,
        original_script=script,
        script=faulted_script,
        injected=True,
        fault_id=fault_id,
        target_task_id=target_task_id,
        target_attempt=target_attempt,
        candidate_evidence_ids=candidate_evidence_ids,
        candidate_fingerprint=candidate_fingerprint,
        faulty_fingerprint=faulty_fingerprint,
        trace_metadata=trace_metadata,
        fault_role=fault_role,
        study_fault_name=study_fault_name,
    )


def _resource_fingerprint(vector: ResourceVector) -> str:
    material = "|".join(
        str(value)
        for value in (
            vector.attempts,
            vector.cost_units,
            vector.tool_calls,
            vector.duration_ms,
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class FallbackBudgetFaultApplication:
    """Materialized scoped-capacity intervention for one fallback request."""

    fault: FaultSpec
    request: ResourceVector
    candidate_capacity: ResourceVector
    capacity: ResourceVector
    injected: bool
    fault_id: str | None
    target_task_id: str
    binding_dimensions: tuple[str, ...]
    candidate_fingerprint: str
    faulty_fingerprint: str
    trace_metadata: Metadata = ()


def apply_fallback_budget_fault(
    request: ResourceVector,
    candidate_capacity: ResourceVector,
    fault: FaultSpec,
    *,
    seed: int,
    target_task_id: str,
) -> FallbackBudgetFaultApplication:
    """Tighten only the cost axis of an exact-fit fallback reservation.

    The identity control retains the exact-fit candidate.  The fault arm lowers
    its cost capacity by one unit while attempts, tool calls, duration, request,
    fallback script, and run-wide limits remain identical.
    """

    if not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if not target_task_id.strip():
        raise ValueError("target_task_id cannot be empty")
    if request != candidate_capacity:
        raise ValueError("fallback budget control must be an exact-fit reservation")
    if request.attempts != 1 or request.cost_units != 4 or request.tool_calls != 1:
        raise ValueError("fallback budget request must use 1 attempt, 4 cost, and 1 call")
    if request.duration_ms <= 0:
        raise ValueError("fallback budget request needs a positive duration")

    candidate_fingerprint = _resource_fingerprint(candidate_capacity)
    if fault is NO_FAULT or fault.name == NO_FAULT.name:
        return FallbackBudgetFaultApplication(
            fault=fault,
            request=request,
            candidate_capacity=candidate_capacity,
            capacity=candidate_capacity,
            injected=False,
            fault_id=None,
            target_task_id=target_task_id,
            binding_dimensions=(),
            candidate_fingerprint=candidate_fingerprint,
            faulty_fingerprint=candidate_fingerprint,
        )
    if fault.name != FALLBACK_BUDGET_EXHAUSTION.name:
        raise ValueError(f"fault {fault.name!r} is not a fallback-budget intervention")

    faulty_capacity = replace(
        candidate_capacity,
        cost_units=candidate_capacity.cost_units - 1,
    )
    faulty_fingerprint = _resource_fingerprint(faulty_capacity)
    fault_id = sha256(
        "|".join(
            (
                fault.name,
                str(seed),
                target_task_id,
                candidate_fingerprint,
                faulty_fingerprint,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    trace_metadata: Metadata = (
        ("binding_dimensions", "cost_units"),
        ("candidate_attempts", str(candidate_capacity.attempts)),
        ("candidate_cost_units", str(candidate_capacity.cost_units)),
        ("candidate_duration_ms", str(candidate_capacity.duration_ms)),
        ("candidate_fingerprint", candidate_fingerprint),
        ("candidate_tool_calls", str(candidate_capacity.tool_calls)),
        ("fault_id", fault_id),
        ("fault_injected", "true"),
        ("fault_name", fault.name),
        ("fault_role", "study_intervention"),
        ("fault_stage", fault.stage),
        ("faulty_attempts", str(faulty_capacity.attempts)),
        ("faulty_cost_units", str(faulty_capacity.cost_units)),
        ("faulty_duration_ms", str(faulty_capacity.duration_ms)),
        ("faulty_fingerprint", faulty_fingerprint),
        ("faulty_tool_calls", str(faulty_capacity.tool_calls)),
        ("selection_seed", str(seed)),
        ("target_task_id", target_task_id),
    )
    return FallbackBudgetFaultApplication(
        fault=fault,
        request=request,
        candidate_capacity=candidate_capacity,
        capacity=faulty_capacity,
        injected=True,
        fault_id=fault_id,
        target_task_id=target_task_id,
        binding_dimensions=("cost_units",),
        candidate_fingerprint=candidate_fingerprint,
        faulty_fingerprint=faulty_fingerprint,
        trace_metadata=trace_metadata,
    )


def apply_planning_fault(
    plan: ActionPlan,
    fault: FaultSpec,
    *,
    seed: int,
    target_action_id: str | None = None,
) -> PlanningFaultApplication:
    """Apply a one-shot planning intervention to one candidate plan.

    The experiment harness supplies ``target_action_id`` from its private case
    fixture so fault construction does not share the critic's reconstruction
    path.  Direct unit callers may omit it; that compatibility path selects
    from the public diagnosis-to-action mapping with a stable SHA-256 digest.
    Both paths are repeatable across processes and independent of strategy
    execution order or Python's hash seed.
    """

    candidate_fingerprint = _fingerprint(plan)
    if fault is NO_FAULT or fault.name == NO_FAULT.name:
        return PlanningFaultApplication(
            fault=fault,
            original_plan=plan,
            plan=plan,
            injected=False,
            fault_id=None,
            omitted_action_id=None,
            candidate_fingerprint=candidate_fingerprint,
            faulty_fingerprint=candidate_fingerprint,
        )
    if fault.name != PLANNING_OMISSION.name:
        raise ValueError(f"fault {fault.name!r} is not a planning intervention")

    action_ids = tuple(action.action_id for action in plan.actions)
    if target_action_id is not None:
        if not target_action_id.strip():
            raise ValueError("target_action_id cannot be empty")
        if target_action_id not in action_ids:
            raise ValueError(
                "planning_omission target must be present in the complete plan"
            )
        candidates = (target_action_id,)
    else:
        causal_actions = {
            CAUSE_ACTION[cause]
            for cause in plan.diagnosis.causes
            if cause in UPSTREAM_CAUSES and cause in CAUSE_ACTION
        }
        candidates = tuple(sorted(
            action.action_id
            for action in plan.actions
            if action.action_id in causal_actions
        ))
    if not candidates:
        raise ValueError(
            "planning_omission requires a complete plan with an upstream mitigation"
        )

    selection_material = f"{fault.name}:{seed}:{','.join(candidates)}"
    selection = int.from_bytes(
        sha256(selection_material.encode("utf-8")).digest()[:8],
        "big",
    )
    omitted_action_id = candidates[selection % len(candidates)]
    faulted_plan = replace(
        plan,
        actions=tuple(
            action for action in plan.actions if action.action_id != omitted_action_id
        ),
    )
    faulty_fingerprint = _fingerprint(faulted_plan)
    fault_id = sha256(
        f"{selection_material}:{omitted_action_id}".encode("utf-8")
    ).hexdigest()[:16]
    return PlanningFaultApplication(
        fault=fault,
        original_plan=plan,
        plan=faulted_plan,
        injected=True,
        fault_id=fault_id,
        omitted_action_id=omitted_action_id,
        candidate_fingerprint=candidate_fingerprint,
        faulty_fingerprint=faulty_fingerprint,
    )
