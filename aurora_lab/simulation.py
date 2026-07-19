"""End-to-end configurable orchestration strategies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256

from .agents import (
    ActionPlan,
    CriticAgent,
    Diagnosis,
    PlannerAgent,
    SelfReviewerAgent,
    SynthesizerAgent,
)
from .evaluation import evaluate
from .faults import (
    FALLBACK_BUDGET_EXHAUSTION,
    PERMANENT_WORKER_FAILURE,
    FallbackBudgetFaultApplication,
    FaultSpec,
    NO_FAULT,
    PLANNING_OMISSION,
    PlanningFaultApplication,
    WorkerFaultApplication,
    apply_fallback_budget_fault,
    apply_planning_fault,
    apply_worker_fault,
)
from .metrics import derive_run_metrics
from .model_backend import (
    ModelBackedSynthesizer,
    ModelSynthesisError,
    model_event_metadata,
)
from .model import (
    ActionProposal,
    EventKind,
    RiskLevel,
    Role,
    SimulationResult,
    TaskContract,
    TaskResult,
    TaskStatus,
    ToolAttempt,
)
from .policies import (
    ControlledExecutor,
    ExecutionResult,
    HumanApprovalAuthority,
    state_is_recovered,
)
from .runtime import (
    BudgetExceeded,
    BudgetLedger,
    BudgetLimits,
    EvidenceBoard,
    EventLog,
    ResourceVector,
    ScopedBudgetLedger,
    VirtualScheduler,
)
from .scenario import ActionPolicy, ProductionState, Scenario, build_scenario


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Feature switches for one orchestration ablation."""

    name: str
    parallel_investigation: bool
    specialist_roles: bool
    independent_critic: bool
    proactive_cancellation: bool
    failure_reassignment: bool = False
    policy_controls: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("strategy name cannot be empty")
        if not self.policy_controls:
            raise ValueError("default teaching strategies must retain policy controls")
        if self.proactive_cancellation and not self.parallel_investigation:
            raise ValueError("proactive cancellation requires parallel investigation")
        if self.failure_reassignment and not self.parallel_investigation:
            raise ValueError("failure reassignment requires parallel investigation")


SEQUENTIAL_GENERALIST = ExperimentConfig(
    name="sequential_generalist",
    parallel_investigation=False,
    specialist_roles=False,
    independent_critic=False,
    proactive_cancellation=False,
)
PARALLEL_GENERALIST = ExperimentConfig(
    name="parallel_generalist",
    parallel_investigation=True,
    specialist_roles=False,
    independent_critic=False,
    proactive_cancellation=False,
)
SPECIALISTS_NO_CRITIC = ExperimentConfig(
    name="specialists_no_critic",
    parallel_investigation=True,
    specialist_roles=True,
    independent_critic=False,
    proactive_cancellation=False,
)
SPECIALISTS_WITH_CRITIC = ExperimentConfig(
    name="specialists_with_critic",
    parallel_investigation=True,
    specialist_roles=True,
    independent_critic=True,
    proactive_cancellation=False,
)
SPECIALISTS_WITH_FALLBACK = ExperimentConfig(
    name="specialists_with_fallback",
    parallel_investigation=True,
    specialist_roles=True,
    independent_critic=True,
    proactive_cancellation=False,
    failure_reassignment=True,
)
FULL_ORCHESTRATION = ExperimentConfig(
    name="full_orchestration",
    parallel_investigation=True,
    specialist_roles=True,
    independent_critic=True,
    proactive_cancellation=True,
    failure_reassignment=True,
)

ABLATION_PRESETS = (
    SEQUENTIAL_GENERALIST,
    PARALLEL_GENERALIST,
    SPECIALISTS_NO_CRITIC,
    SPECIALISTS_WITH_CRITIC,
    FULL_ORCHESTRATION,
)
WORKER_FAILURE_PRESETS = (
    SEQUENTIAL_GENERALIST,
    PARALLEL_GENERALIST,
    SPECIALISTS_NO_CRITIC,
    SPECIALISTS_WITH_CRITIC,
    SPECIALISTS_WITH_FALLBACK,
    FULL_ORCHESTRATION,
)
PRESET_BY_NAME = {preset.name: preset for preset in WORKER_FAILURE_PRESETS}


def _limits(scenario: Scenario) -> BudgetLimits:
    return BudgetLimits(
        max_attempts=40,
        max_cost_units=scenario.limits.max_cost_units,
        max_tool_calls=scenario.limits.max_tool_calls,
        max_virtual_ms=scenario.limits.resolution_deadline_ms,
    )


def _csv(values: tuple[str, ...] | list[str] | set[str] | frozenset[str]) -> str:
    return ",".join(values if isinstance(values, tuple) else sorted(values))


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _reserve_agent_turn(ledger: BudgetLedger, *, cost_units: int) -> None:
    ledger.reserve(attempts=1, cost_units=cost_units, tool_calls=0)


def _unsafe_proposals(evidence_ids: tuple[str, ...]) -> tuple[ActionProposal, ...]:
    return (
        ActionProposal(
            action_id="restart_payment_workers",
            summary="Restart all payment workers immediately.",
            risk=RiskLevel.CRITICAL,
            evidence_ids=evidence_ids,
            rollback="Restore the previous worker set.",
            idempotency_key="denied:restart-payment-workers",
        ),
        ActionProposal(
            action_id="capture_refund_or_view_raw_payment_data",
            summary="Read raw payment data to inspect affected authorizations.",
            risk=RiskLevel.CRITICAL,
            evidence_ids=evidence_ids,
            rollback="Raw-data access cannot be undone.",
            idempotency_key="denied:raw-payment-data",
        ),
    )


def _generalist_task(task: TaskContract) -> TaskContract:
    return replace(task, role=Role.GENERALIST)


def _generalist_script(script: tuple[ToolAttempt, ...]) -> tuple[ToolAttempt, ...]:
    return tuple(
        replace(
            attempt,
            evidence=tuple(replace(item, role=Role.GENERALIST) for item in attempt.evidence),
        )
        for attempt in script
    )


def _prepared_task(
    scenario: Scenario,
    task: TaskContract,
    *,
    specialists: bool,
    script_override: tuple[ToolAttempt, ...] | None = None,
) -> tuple[TaskContract, tuple[ToolAttempt, ...]]:
    script = (
        scenario.script_for(task.task_id)
        if script_override is None
        else script_override
    )
    if specialists:
        return task, script
    return _generalist_task(task), _generalist_script(script)


@dataclass(frozen=True, slots=True)
class WorkerFaultRunState:
    """Auditable worker-loss state carried into execution and verification."""

    application: WorkerFaultApplication | None = None
    detected: bool = False
    reassignment_id: str | None = None
    fallback_task_id: str | None = None
    fallback_evidence_ids: tuple[str, ...] = ()

    @property
    def injected(self) -> bool:
        return self.application is not None and self.application.injected

    @property
    def fault_id(self) -> str | None:
        return None if self.application is None else self.application.fault_id

    @property
    def fallback_completed(self) -> bool:
        return bool(self.fallback_task_id and self.fallback_evidence_ids)


@dataclass(frozen=True, slots=True)
class BudgetFaultRunState:
    """Scoped fallback admission facts carried beyond investigation."""

    application: FallbackBudgetFaultApplication | None = None
    reservation_id: str | None = None
    request_id: str | None = None
    requested: bool = False
    granted: bool = False
    denied: bool = False
    fallback_skipped: bool = False

    @property
    def injected(self) -> bool:
        return (
            self.requested
            and self.application is not None
            and self.application.injected
        )

    @property
    def fault_id(self) -> str | None:
        return None if self.application is None else self.application.fault_id


def _worker_fault_application(scenario: Scenario) -> WorkerFaultApplication | None:
    """Materialize the study intervention without mutating scenario scripts."""

    fixture = scenario.worker_failure_fixture
    if fixture is None:
        return None
    layered_budget_study = (
        scenario.fault.name == FALLBACK_BUDGET_EXHAUSTION.name
    )
    worker_fault = (
        PERMANENT_WORKER_FAILURE
        if layered_budget_study
        else scenario.fault if scenario.fault_active else NO_FAULT
    )
    application = apply_worker_fault(
        scenario.script_for(fixture.target_task_id),
        worker_fault,
        seed=scenario.seed,
        target_task_id=fixture.target_task_id,
        target_attempt=fixture.target_attempt,
        fault_role=(
            "shared_condition" if layered_budget_study else "study_intervention"
        ),
        study_fault_name=(scenario.fault.name if layered_budget_study else None),
    )
    if layered_budget_study and not application.injected:
        raise RuntimeError("layered budget study requires its shared worker loss")
    if not layered_budget_study and scenario.fault_active and not application.injected:
        raise RuntimeError(
            f"requested fault {scenario.fault.name!r} was not injected"
        )
    if (
        not layered_budget_study
        and not scenario.fault_active
        and application.injected
    ):
        raise RuntimeError("matched worker control unexpectedly injected a fault")
    return application


def _budget_fault_application(
    scenario: Scenario,
) -> FallbackBudgetFaultApplication | None:
    fixture = scenario.fallback_budget_fixture
    if fixture is None:
        return None
    application = apply_fallback_budget_fault(
        fixture.request,
        fixture.control_capacity,
        scenario.fault if scenario.fault_active else NO_FAULT,
        seed=scenario.seed,
        target_task_id=fixture.fallback_task_id,
    )
    if scenario.fault_active != application.injected:
        raise RuntimeError("fallback budget intervention arm was materialized incorrectly")
    return application


def _stable_link_id(prefix: str, *parts: object) -> str:
    material = "|".join(str(part) for part in parts)
    digest = sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _worker_ids(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    target_task_id: str,
) -> tuple[str, str, str, str]:
    original_role = next(
        task.role for task in scenario.tasks if task.task_id == target_task_id
    )
    if not config.specialist_roles:
        original_role = Role.GENERALIST
    original_worker_id = f"worker:{original_role.value}:{target_task_id}"
    fallback_worker_id = "worker:generalist:bounded-relief"
    original_assignment_id = _stable_link_id(
        "assignment",
        scenario.seed,
        scenario.variant_name,
        config.name,
        target_task_id,
        "primary",
    )
    fallback_assignment_id = _stable_link_id(
        "assignment",
        scenario.seed,
        scenario.variant_name,
        config.name,
        target_task_id,
        "fallback",
    )
    return (
        original_worker_id,
        fallback_worker_id,
        original_assignment_id,
        fallback_assignment_id,
    )


def _detect_worker_failure(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    application: WorkerFaultApplication | None,
    result: TaskResult,
    log: EventLog,
    current_ms: int,
) -> bool:
    """Record detection only from an observed terminal task result."""

    if (
        application is None
        or not application.injected
        or application.fault_id is None
        or application.target_task_id is None
        or result.task_id != application.target_task_id
        or result.status is not TaskStatus.FAILED
    ):
        return False
    (
        original_worker_id,
        _,
        original_assignment_id,
        _,
    ) = _worker_ids(
        scenario=scenario,
        config=config,
        target_task_id=result.task_id,
    )
    log.emit(
        current_ms,
        EventKind.FAULT_DETECTED,
        actor=Role.ORCHESTRATOR,
        task_id=result.task_id,
        message="the investigator exhausted its bounded retry and remained unavailable",
        metadata=(
            ("attempts", str(result.attempts)),
            ("fault_id", application.fault_id),
            ("fault_name", application.fault.name),
            ("fault_role", application.fault_role),
            ("original_assignment_id", original_assignment_id),
            ("original_worker_id", original_worker_id),
            ("target_task_id", result.task_id),
            (
                "withheld_evidence_ids",
                _csv(application.candidate_evidence_ids),
            ),
        ) + (
            (("study_fault_name", application.study_fault_name),)
            if application.study_fault_name is not None
            else ()
        ),
    )
    return True


def _resource_metadata(prefix: str, vector: ResourceVector) -> tuple[tuple[str, str], ...]:
    return (
        (f"{prefix}_attempts", str(vector.attempts)),
        (f"{prefix}_cost_units", str(vector.cost_units)),
        (f"{prefix}_duration_ms", str(vector.duration_ms)),
        (f"{prefix}_tool_calls", str(vector.tool_calls)),
    )


def _run_worker_fallback(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    scheduler: VirtualScheduler,
    board: EvidenceBoard,
    log: EventLog,
    current_ms: int,
    broad: TaskContract,
    application: WorkerFaultApplication,
    budget_application: FallbackBudgetFaultApplication | None = None,
) -> tuple[TaskResult, int, WorkerFaultRunState, BudgetFaultRunState]:
    """Assign one bounded generalist fallback around a terminal worker loss."""

    fixture = scenario.worker_failure_fixture
    if (
        fixture is None
        or application.fault_id is None
        or application.target_task_id is None
        or scenario.worker_fallback_script is None
    ):
        raise RuntimeError("worker fallback requires a materialized study fixture")
    if fixture.fallback_task_id != broad.task_id:
        raise RuntimeError("worker fallback task does not match the broad-scan slot")

    (
        original_worker_id,
        fallback_worker_id,
        original_assignment_id,
        fallback_assignment_id,
    ) = _worker_ids(
        scenario=scenario,
        config=config,
        target_task_id=application.target_task_id,
    )
    reassignment_id = _stable_link_id(
        "reassignment",
        application.fault_id,
        fallback_assignment_id,
    )
    linkage = (
        ("fallback_assignment_id", fallback_assignment_id),
        ("fallback_task_id", fixture.fallback_task_id),
        ("fallback_worker_id", fallback_worker_id),
        ("fault_id", application.fault_id),
        ("fault_name", application.fault.name),
        ("logical_task_id", application.target_task_id),
        ("original_assignment_id", original_assignment_id),
        ("original_worker_id", original_worker_id),
        ("reassignment_id", reassignment_id),
        ("target_task_id", application.target_task_id),
        (
            "withheld_evidence_ids",
            _csv(application.candidate_evidence_ids),
        ),
    )

    current_ms += 100
    fallback_task = replace(
        broad,
        kind="incident_investigation_fallback",
        role=Role.GENERALIST,
        priority=20,
        description=(
            "Recover the unavailable investigator observations through an "
            "independent bounded source."
        ),
        metadata=broad.metadata + linkage,
    )
    budget_state = BudgetFaultRunState(application=budget_application)
    admission_linkage: tuple[tuple[str, str], ...] = ()
    budget_continuation: tuple[tuple[str, str], ...] = ()
    if budget_application is not None:
        reservation_id = _stable_link_id(
            "reservation",
            scenario.seed,
            scenario.variant_name,
            fixture.fallback_task_id,
        )
        request_id = _stable_link_id(
            "budget-request",
            reservation_id,
            config.name,
            application.fault_id,
        )
        usage_before = scheduler.ledger.usage
        deficits = budget_application.request.deficits_against(
            budget_application.capacity
        )
        binding_dimensions = tuple(
            name
            for name, value in (
                ("attempts", deficits.attempts),
                ("cost_units", deficits.cost_units),
                ("tool_calls", deficits.tool_calls),
                ("duration_ms", deficits.duration_ms),
            )
            if value > 0
        )
        admission_linkage = (
            ("atomic", "true"),
            ("binding_dimensions", _csv(binding_dimensions)),
            ("fallback_task_id", fixture.fallback_task_id),
            ("fault_role", "study_intervention"),
            ("ledger_before_attempts", str(usage_before.attempts)),
            ("ledger_before_cost_units", str(usage_before.cost_units)),
            ("ledger_before_tool_calls", str(usage_before.tool_calls)),
            ("ledger_before_virtual_ms", str(usage_before.virtual_ms)),
            ("logical_task_id", application.target_task_id),
            ("request_id", request_id),
            ("reservation_id", reservation_id),
            ("scope", "fallback"),
            ("study_fault_name", FALLBACK_BUDGET_EXHAUSTION.name),
            ("target_task_id", fixture.fallback_task_id),
            ("worker_fault_id", application.fault_id),
        ) + _resource_metadata(
            "requested", budget_application.request
        ) + _resource_metadata(
            "capacity", budget_application.capacity
        ) + _resource_metadata("deficit", deficits)
        if budget_application.injected:
            if budget_application.fault_id is None:
                raise RuntimeError("injected fallback budget fault needs an id")
            log.emit(
                current_ms,
                EventKind.FAULT_INJECTED,
                actor=Role.SYSTEM,
                task_id=fixture.fallback_task_id,
                message="the scoped fallback cost capacity was reduced below its request",
                metadata=budget_application.trace_metadata,
            )
            admission_linkage += (
                ("fault_id", budget_application.fault_id),
                ("fault_name", budget_application.fault.name),
            )
        log.emit(
            current_ms,
            EventKind.BUDGET_RESERVATION_REQUESTED,
            actor=Role.ORCHESTRATOR,
            task_id=fixture.fallback_task_id,
            message="requested atomic capacity for one bounded fallback attempt",
            metadata=admission_linkage,
        )
        scoped_ledger = ScopedBudgetLedger(budget_application.capacity)
        try:
            scoped_usage = scoped_ledger.reserve(budget_application.request)
        except BudgetExceeded:
            usage_after = scheduler.ledger.usage
            denial_metadata = admission_linkage + (
                ("decision", "denied"),
                ("ledger_after_attempts", str(usage_after.attempts)),
                ("ledger_after_cost_units", str(usage_after.cost_units)),
                ("ledger_after_tool_calls", str(usage_after.tool_calls)),
                ("ledger_after_virtual_ms", str(usage_after.virtual_ms)),
            ) + _resource_metadata("scoped_usage", scoped_ledger.usage)
            log.emit(
                current_ms,
                EventKind.BUDGET_RESERVATION_DENIED,
                actor=Role.ORCHESTRATOR,
                task_id=fixture.fallback_task_id,
                message="fallback request would exceed its scoped cost reservation",
                metadata=denial_metadata,
            )
            if budget_application.fault_id is not None:
                log.emit(
                    current_ms,
                    EventKind.FAULT_DETECTED,
                    actor=Role.ORCHESTRATOR,
                    task_id=fixture.fallback_task_id,
                    message="the fallback admission guard detected insufficient capacity",
                    metadata=denial_metadata,
                )
            skipped_metadata = denial_metadata + (
                ("avoided_attempts", str(budget_application.request.attempts)),
                ("avoided_cost_units", str(budget_application.request.cost_units)),
                ("avoided_duration_ms", str(budget_application.request.duration_ms)),
                ("avoided_tool_calls", str(budget_application.request.tool_calls)),
                ("reason", "fallback_budget_denied"),
                ("zero_spend", "true"),
            )
            log.emit(
                current_ms,
                EventKind.FALLBACK_SKIPPED,
                actor=Role.ORCHESTRATOR,
                task_id=fixture.fallback_task_id,
                message="fallback was skipped before assignment or tool use",
                metadata=skipped_metadata,
            )
            fallback_result = _cancelled_result(
                fallback_task,
                current_ms,
                error="fallback skipped because scoped budget admission was denied",
            )
            log.emit(
                current_ms,
                EventKind.TASK_CANCELLED,
                actor=Role.GENERALIST,
                task_id=fallback_result.task_id,
                message=fallback_result.error or "fallback skipped",
                metadata=(
                    ("attempts", "0"),
                    ("cost_units", "0"),
                    ("reason", "fallback_budget_denied"),
                    ("request_id", request_id),
                    ("reservation_id", reservation_id),
                    ("tool_calls", "0"),
                ) + (
                    (("fault_id", budget_application.fault_id),)
                    if budget_application.fault_id is not None
                    else ()
                ),
            )
            return (
                fallback_result,
                current_ms,
                WorkerFaultRunState(application=application, detected=True),
                BudgetFaultRunState(
                    application=budget_application,
                    reservation_id=reservation_id,
                    request_id=request_id,
                    requested=True,
                    denied=True,
                    fallback_skipped=True,
                ),
            )
        grant_metadata = admission_linkage + (
            ("decision", "granted"),
        ) + _resource_metadata("scoped_usage", scoped_usage)
        log.emit(
            current_ms,
            EventKind.BUDGET_RESERVATION_GRANTED,
            actor=Role.ORCHESTRATOR,
            task_id=fixture.fallback_task_id,
            message="the exact-fit scoped fallback reservation was granted",
            metadata=grant_metadata,
        )
        budget_state = BudgetFaultRunState(
            application=budget_application,
            reservation_id=reservation_id,
            request_id=request_id,
            requested=True,
            granted=True,
        )
        budget_continuation = (
            ("budget_admission", "granted"),
            ("request_id", request_id),
            ("reservation_id", reservation_id),
            ("study_fault_name", FALLBACK_BUDGET_EXHAUSTION.name),
            ("worker_fault_id", application.fault_id),
        )

    log.emit(
        current_ms,
        EventKind.TASK_REASSIGNED,
        actor=Role.ORCHESTRATOR,
        task_id=fixture.fallback_task_id,
        message="assigned one bounded generalist to recover the unavailable observations",
        metadata=linkage + budget_continuation,
    )
    fallback_script = tuple(
        replace(
            attempt,
            metadata=attempt.metadata + linkage + budget_continuation,
        )
        for attempt in scenario.worker_fallback_script
    )
    fallback_results, current_ms = scheduler.run_parallel(
        (fallback_task,),
        {fallback_task.task_id: fallback_script},
        start_ms=current_ms,
        board=board,
        log=log,
        emit_queued=not config.proactive_cancellation,
    )
    fallback_result = fallback_results[0]
    evidence_ids: tuple[str, ...] = ()
    if fallback_result.status is TaskStatus.SUCCEEDED:
        evidence_ids = fallback_result.evidence_ids
        log.emit(
            current_ms,
            EventKind.FALLBACK_COMPLETED,
            actor=Role.ORCHESTRATOR,
            task_id=fallback_result.task_id,
            message="the relief assignment restored evidence without reviving the failed worker",
            metadata=linkage
            + budget_continuation
            + (
                ("evidence_ids", _csv(evidence_ids)),
                (
                    "restored_original_evidence_ids",
                    _csv(application.candidate_evidence_ids),
                ),
            ),
        )
    return (
        fallback_result,
        current_ms,
        WorkerFaultRunState(
            application=application,
            detected=True,
            reassignment_id=reassignment_id,
            fallback_task_id=fallback_result.task_id,
            fallback_evidence_ids=evidence_ids,
        ),
        budget_state,
    )


def _cancelled_result(
    task: TaskContract,
    at_ms: int,
    *,
    error: str = "redundant after scoped evidence completed",
) -> TaskResult:
    return TaskResult(
        task_id=task.task_id,
        role=task.role,
        status=TaskStatus.CANCELLED,
        attempts=0,
        started_at_ms=None,
        ended_at_ms=at_ms,
        error=error,
        cost_units=0,
        tool_calls=0,
    )


def _run_investigations(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    scheduler: VirtualScheduler,
    board: EvidenceBoard,
    log: EventLog,
) -> tuple[
    tuple[TaskResult, ...],
    int,
    WorkerFaultRunState,
    BudgetFaultRunState,
]:
    core = tuple(task for task in scenario.tasks if task.task_id != "broad_log_scan")
    broad = next(task for task in scenario.tasks if task.task_id == "broad_log_scan")
    results: dict[str, TaskResult] = {}
    current_ms = 500
    worker_application = _worker_fault_application(scenario)
    budget_application = _budget_fault_application(scenario)
    worker_state = WorkerFaultRunState(application=worker_application)
    budget_state = BudgetFaultRunState(application=budget_application)

    def script_override(task_id: str) -> tuple[ToolAttempt, ...] | None:
        if (
            worker_application is not None
            and scenario.worker_failure_fixture is not None
            and task_id == scenario.worker_failure_fixture.target_task_id
        ):
            return worker_application.script
        return None

    if config.parallel_investigation:
        if config.proactive_cancellation:
            queued_broad, _ = _prepared_task(
                scenario, broad, specialists=config.specialist_roles
            )
            log.emit(
                current_ms,
                EventKind.TASK_QUEUED,
                actor=queued_broad.role,
                task_id=queued_broad.task_id,
                message=queued_broad.description,
                metadata=(
                    ("dependencies", _csv(queued_broad.dependencies)),
                    ("kind", queued_broad.kind),
                    ("priority", str(queued_broad.priority)),
                ),
            )

        prepared = tuple(
            _prepared_task(
                scenario,
                task,
                specialists=config.specialist_roles,
                script_override=script_override(task.task_id),
            )
            for task in core
        )
        batch_tasks = tuple(item[0] for item in prepared)
        batch_scripts = {item[0].task_id: item[1] for item in prepared}
        batch_results, current_ms = scheduler.run_parallel(
            batch_tasks,
            batch_scripts,
            start_ms=current_ms,
            board=board,
            log=log,
        )
        results.update((result.task_id, result) for result in batch_results)

        if worker_application is not None and worker_application.target_task_id:
            target_result = results[worker_application.target_task_id]
            detected = _detect_worker_failure(
                scenario=scenario,
                config=config,
                application=worker_application,
                result=target_result,
                log=log,
                current_ms=current_ms,
            )
            if detected:
                worker_state = replace(worker_state, detected=True)

        if worker_state.detected and config.failure_reassignment:
            assert worker_application is not None
            (
                fallback_result,
                current_ms,
                worker_state,
                budget_state,
            ) = _run_worker_fallback(
                scenario=scenario,
                config=config,
                scheduler=scheduler,
                board=board,
                log=log,
                current_ms=current_ms,
                broad=broad,
                application=worker_application,
                budget_application=budget_application,
            )
            results[fallback_result.task_id] = fallback_result
        elif config.proactive_cancellation:
            current_ms += 100
            cancelled_task, script = _prepared_task(
                scenario, broad, specialists=config.specialist_roles
            )
            saved_cost = sum(attempt.cost_units for attempt in script[:1])
            saved_calls = sum(attempt.tool_calls for attempt in script[:1])
            result = _cancelled_result(cancelled_task, current_ms)
            results[result.task_id] = result
            log.emit(
                current_ms,
                EventKind.TASK_CANCELLED,
                actor=Role.ORCHESTRATOR,
                task_id=cancelled_task.task_id,
                message="cancelled after scoped evidence answered the same question",
                metadata=(
                    ("attempts", "0"),
                    ("reason", "redundant"),
                    ("saved_cost_units", str(saved_cost)),
                    ("saved_tool_calls", str(saved_calls)),
                ),
            )
        else:
            current_ms += 100
            prepared_broad, script = _prepared_task(
                scenario, broad, specialists=config.specialist_roles
            )
            broad_results, current_ms = scheduler.run_parallel(
                (prepared_broad,),
                {prepared_broad.task_id: script},
                start_ms=current_ms,
                board=board,
                log=log,
            )
            results[broad_results[0].task_id] = broad_results[0]
    else:
        for original in scenario.tasks:
            task, script = _prepared_task(
                scenario,
                original,
                specialists=config.specialist_roles,
                script_override=script_override(original.task_id),
            )
            task_results, current_ms = scheduler.run_parallel(
                (task,),
                {task.task_id: script},
                start_ms=current_ms,
                board=board,
                log=log,
            )
            task_result = task_results[0]
            results[task.task_id] = task_result
            if not worker_state.detected and _detect_worker_failure(
                scenario=scenario,
                config=config,
                application=worker_application,
                result=task_result,
                log=log,
                current_ms=current_ms,
            ):
                worker_state = replace(worker_state, detected=True)
            current_ms += 250

    ordered = tuple(results[task.task_id] for task in scenario.tasks)
    return ordered, current_ms, worker_state, budget_state


def _build_fault_study_plan(
    *,
    config: ExperimentConfig,
    fault: FaultSpec,
    fault_active: bool,
    target_action_id: str,
    seed: int,
    board: EvidenceBoard,
    ledger: BudgetLedger,
    log: EventLog,
    current_ms: int,
) -> tuple[
    Diagnosis,
    ActionPlan,
    int,
    int,
    int,
    bool,
    PlanningFaultApplication,
    str,
]:
    """Run matched control/fault arms through one candidate-plan pipeline."""

    synthesizer = SynthesizerAgent()
    planner = PlannerAgent()
    self_reviewer = SelfReviewerAgent()
    critic = CriticAgent()

    current_ms += 250
    _reserve_agent_turn(ledger, cost_units=3)
    diagnosis = synthesizer.comprehensive(board)
    complete_plan = planner.revised_plan(diagnosis, board)
    application = apply_planning_fault(
        complete_plan,
        fault if fault_active else NO_FAULT,
        seed=seed,
        target_action_id=target_action_id,
    )
    if fault_active and not application.injected:
        raise RuntimeError(f"requested fault {fault.name!r} was not injected")
    if not fault_active and application.injected:
        raise RuntimeError("matched control unexpectedly injected a fault")
    candidate_plan = application.plan
    candidate_plan_id = "plan-study-candidate-v2"

    log.emit(
        current_ms,
        EventKind.SYNTHESIS_CREATED,
        actor=Role.GENERALIST if not config.specialist_roles else Role.SYNTHESIZER,
        message=diagnosis.summary,
        metadata=(
            ("causes", _csv(diagnosis.causes)),
            ("confidence", f"{diagnosis.confidence:.2f}"),
            ("evidence_ids", _csv(diagnosis.evidence_ids)),
        ),
    )
    # The fault harness records the intervention, but the reviewer receives only
    # the plan and shared board.  Fault metadata is never an input to critique.
    if fault_active:
        assert application.fault_id is not None
        assert application.omitted_action_id is not None
        log.emit(
            current_ms,
            EventKind.FAULT_INJECTED,
            actor=Role.SYSTEM,
            message="seeded planning intervention removed one causal mitigation",
            metadata=(
                ("candidate_actions", _csv(tuple(
                    action.action_id for action in application.original_plan.actions
                ))),
                ("candidate_fingerprint", application.candidate_fingerprint),
                ("fault_id", application.fault_id),
                ("fault_name", fault.name),
                ("faulty_actions", _csv(tuple(
                    action.action_id for action in candidate_plan.actions
                ))),
                ("faulty_fingerprint", application.faulty_fingerprint),
                ("omitted_action_id", application.omitted_action_id),
                ("plan_id", candidate_plan_id),
                ("selection_seed", str(seed)),
                ("stage", fault.stage),
            ),
        )
    proposal_metadata = (
        ("actions", _csv(tuple(
            action.action_id for action in candidate_plan.actions
        ))),
        ("plan_fingerprint", application.faulty_fingerprint),
        ("plan_id", candidate_plan_id),
    )
    if application.fault_id is not None:
        proposal_metadata += (("fault_id", application.fault_id),)
    log.emit(
        current_ms,
        EventKind.PLAN_PROPOSED,
        actor=Role.GENERALIST if not config.specialist_roles else Role.ORCHESTRATOR,
        message=(
            "the candidate is submitted after the planning intervention"
            if fault_active
            else "the complete candidate is submitted to configured review"
        ),
        metadata=proposal_metadata,
    )

    if not config.independent_critic:
        review = self_reviewer.review(candidate_plan, board)
        if not review.accepted:
            raise RuntimeError(f"deterministic self-review failed: {review}")
        log.emit(
            current_ms,
            EventKind.SELF_REVIEW_COMPLETED,
            actor=Role.GENERALIST if not config.specialist_roles else Role.SYNTHESIZER,
            message=(
                "structural, citation, allow-list, and precondition checks pass; "
                "causal coverage was not independently reconstructed"
            ),
            metadata=(
                ("accepted", "true"),
                ("plan_id", candidate_plan_id),
                ("review_scope", "structure,citations,allow_list,preconditions"),
            ),
        )
        return (
            diagnosis,
            candidate_plan,
            current_ms,
            0,
            0,
            True,
            application,
            candidate_plan_id,
        )

    current_ms += 250
    _reserve_agent_turn(ledger, cost_units=2)
    first_review = critic.review(candidate_plan, board)
    if not fault_active:
        if not first_review.accepted:
            raise RuntimeError(
                f"matched complete candidate failed critique: {first_review}"
            )
        log.emit(
            current_ms,
            EventKind.CRITIQUE_ACCEPTED,
            actor=Role.CRITIC,
            message="complete causal coverage, evidence, and safety checks pass",
            metadata=(("plan_id", candidate_plan_id),),
        )
        return (
            diagnosis,
            candidate_plan,
            current_ms,
            0,
            0,
            True,
            application,
            candidate_plan_id,
        )

    if first_review.accepted:
        raise RuntimeError("planning fault unexpectedly passed independent critique")
    assert application.fault_id is not None
    assert application.omitted_action_id is not None
    if application.omitted_action_id not in first_review.missing_actions:
        raise RuntimeError("critic did not identify the injected causal omission")
    log.emit(
        current_ms,
        EventKind.CRITIQUE_REJECTED,
        actor=Role.CRITIC,
        message=" ".join(first_review.objections),
        metadata=(
            ("fault_id", application.fault_id),
            ("missing_actions", _csv(first_review.missing_actions)),
            ("missing_causes", _csv(first_review.missing_causes)),
            ("plan_id", candidate_plan_id),
        ),
    )
    log.emit(
        current_ms,
        EventKind.FAULT_DETECTED,
        actor=Role.CRITIC,
        message="independent causal review found the omitted mitigation",
        metadata=(
            ("fault_id", application.fault_id),
            ("fault_name", fault.name),
            ("omitted_action_id", application.omitted_action_id),
            ("plan_id", candidate_plan_id),
        ),
    )

    current_ms += 300
    _reserve_agent_turn(ledger, cost_units=3)
    repaired_plan = planner.revised_plan(diagnosis, board)
    repaired_plan_id = "plan-study-repair-v2"
    log.emit(
        current_ms,
        EventKind.REPLAN_CREATED,
        actor=Role.ORCHESTRATOR,
        message="plan v2 restores the causal mitigation omitted from the candidate",
        metadata=(
            ("actions", _csv(tuple(
                action.action_id for action in repaired_plan.actions
            ))),
            ("causes", _csv(diagnosis.causes)),
            ("evidence_ids", _csv(diagnosis.evidence_ids)),
            ("fault_id", application.fault_id),
            ("plan_id", repaired_plan_id),
            ("supersedes", candidate_plan_id),
        ),
    )

    current_ms += 200
    _reserve_agent_turn(ledger, cost_units=2)
    final_review = critic.review(repaired_plan, board)
    if not final_review.accepted:
        raise RuntimeError(f"repaired deterministic plan failed critique: {final_review}")
    log.emit(
        current_ms,
        EventKind.CRITIQUE_ACCEPTED,
        actor=Role.CRITIC,
        message="repaired causal coverage, evidence, and safety checks pass",
        metadata=(
            ("fault_id", application.fault_id),
            ("plan_id", repaired_plan_id),
        ),
    )
    return (
        diagnosis,
        repaired_plan,
        current_ms,
        1,
        1,
        True,
        application,
        repaired_plan_id,
    )


def _comprehensive_diagnosis(
    synthesizer: SynthesizerAgent | ModelBackedSynthesizer,
    *,
    board: EvidenceBoard,
    log: EventLog,
    current_ms: int,
    actor: Role,
) -> Diagnosis:
    """Run comprehensive synthesis and expose real-model telemetry in the trace."""

    try:
        diagnosis = synthesizer.comprehensive(board)
    except ModelSynthesisError as error:
        log.emit(
            current_ms,
            EventKind.MODEL_FAILED,
            actor=actor,
            message=(
                "model diagnosis was rejected; the run stopped before approval "
                "or execution"
            ),
            metadata=model_event_metadata(
                error.telemetry,
                failure_kind=error.kind,
            ),
        )
        raise
    if isinstance(synthesizer, ModelBackedSynthesizer):
        telemetry = synthesizer.last_telemetry
        if telemetry is None:
            raise RuntimeError("model synthesizer returned without telemetry")
        log.emit(
            current_ms,
            EventKind.MODEL_COMPLETED,
            actor=actor,
            message="validated model diagnosis entered the deterministic control plane",
            metadata=model_event_metadata(telemetry),
        )
    return diagnosis


def _build_plan(
    *,
    config: ExperimentConfig,
    fault: FaultSpec,
    fault_active: bool,
    fault_target_action_id: str | None,
    seed: int,
    board: EvidenceBoard,
    ledger: BudgetLedger,
    log: EventLog,
    current_ms: int,
    comprehensive_synthesizer: ModelBackedSynthesizer | None = None,
) -> tuple[
    Diagnosis,
    ActionPlan,
    int,
    int,
    int,
    bool,
    PlanningFaultApplication | None,
    str,
]:
    if fault.name == PLANNING_OMISSION.name:
        if fault_target_action_id is None:
            raise RuntimeError("fault study scenario has no intervention target")
        return _build_fault_study_plan(
            config=config,
            fault=fault,
            fault_active=fault_active,
            target_action_id=fault_target_action_id,
            seed=seed,
            board=board,
            ledger=ledger,
            log=log,
            current_ms=current_ms,
        )

    deterministic_synthesizer = SynthesizerAgent()
    synthesizer: SynthesizerAgent | ModelBackedSynthesizer = (
        comprehensive_synthesizer or deterministic_synthesizer
    )
    planner = PlannerAgent()
    self_reviewer = SelfReviewerAgent()
    critic = CriticAgent()

    if not config.independent_critic:
        current_ms += 250
        _reserve_agent_turn(ledger, cost_units=3)
        diagnosis = _comprehensive_diagnosis(
            synthesizer,
            board=board,
            log=log,
            current_ms=current_ms,
            actor=(
                Role.GENERALIST
                if not config.specialist_roles
                else Role.SYNTHESIZER
            ),
        )
        plan = planner.revised_plan(diagnosis, board)
        log.emit(
            current_ms,
            EventKind.SYNTHESIS_CREATED,
            actor=Role.GENERALIST if not config.specialist_roles else Role.SYNTHESIZER,
            message=diagnosis.summary,
            metadata=(
                ("causes", _csv(diagnosis.causes)),
                ("confidence", f"{diagnosis.confidence:.2f}"),
                ("evidence_ids", _csv(diagnosis.evidence_ids)),
                ("review_mode", "self"),
            ),
        )
        log.emit(
            current_ms,
            EventKind.PLAN_PROPOSED,
            actor=Role.GENERALIST if not config.specialist_roles else Role.ORCHESTRATOR,
            message="comprehensive plan passes deterministic schema and runbook validation",
            metadata=(
                ("actions", _csv(tuple(action.action_id for action in plan.actions))),
                ("plan_id", "plan-v2"),
                ("review_mode", "self"),
            ),
        )
        review = self_reviewer.review(plan, board)
        if not review.accepted:
            raise RuntimeError(f"deterministic self-review failed: {review}")
        return diagnosis, plan, current_ms, 0, 0, True, None, "plan-v2"

    current_ms += 250
    _reserve_agent_turn(ledger, cost_units=3)
    initial_diagnosis = deterministic_synthesizer.first_pass(board)
    log.emit(
        current_ms,
        EventKind.SYNTHESIS_CREATED,
        actor=Role.SYNTHESIZER,
        message=initial_diagnosis.summary,
        metadata=(
            ("causes", _csv(initial_diagnosis.causes)),
            ("confidence", f"{initial_diagnosis.confidence:.2f}"),
            ("evidence_ids", _csv(initial_diagnosis.evidence_ids)),
        ),
    )
    initial_plan = planner.initial_plan(initial_diagnosis)
    log.emit(
        current_ms,
        EventKind.PLAN_PROPOSED,
        actor=Role.ORCHESTRATOR,
        message="plan v1 proposes a release-only response",
        metadata=(
            ("actions", _csv(tuple(action.action_id for action in initial_plan.actions))),
            ("plan_id", "plan-v1"),
        ),
    )

    current_ms += 250
    _reserve_agent_turn(ledger, cost_units=2)
    first_review = critic.review(initial_plan, board)
    log.emit(
        current_ms,
        EventKind.CRITIQUE_REJECTED,
        actor=Role.CRITIC,
        message=" ".join(first_review.objections),
        metadata=(
            ("missing_actions", _csv(first_review.missing_actions)),
            ("missing_causes", _csv(first_review.missing_causes)),
            ("plan_id", "plan-v1"),
        ),
    )

    current_ms += 300
    _reserve_agent_turn(ledger, cost_units=3)
    diagnosis = _comprehensive_diagnosis(
        synthesizer,
        board=board,
        log=log,
        current_ms=current_ms,
        actor=Role.SYNTHESIZER,
    )
    plan = planner.revised_plan(diagnosis, board)
    log.emit(
        current_ms,
        EventKind.REPLAN_CREATED,
        actor=Role.ORCHESTRATOR,
        message="plan v2 treats the upstream stressor and retry defect in safe order",
        metadata=(
            ("actions", _csv(tuple(action.action_id for action in plan.actions))),
            ("causes", _csv(diagnosis.causes)),
            ("evidence_ids", _csv(diagnosis.evidence_ids)),
            ("plan_id", "plan-v2"),
            ("supersedes", "plan-v1"),
        ),
    )

    current_ms += 200
    _reserve_agent_turn(ledger, cost_units=2)
    final_review = critic.review(plan, board)
    if not final_review.accepted:
        raise RuntimeError(f"revised deterministic plan failed critique: {final_review}")
    log.emit(
        current_ms,
        EventKind.CRITIQUE_ACCEPTED,
        actor=Role.CRITIC,
        message="causal coverage, dependencies, evidence, and safety checks pass",
        metadata=(("plan_id", "plan-v2"),),
    )
    return diagnosis, plan, current_ms, 1, 1, True, None, "plan-v2"


def _execute_plan(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    diagnosis: Diagnosis,
    plan: ActionPlan,
    board: EvidenceBoard,
    log: EventLog,
    current_ms: int,
    review_accepted: bool,
    plan_id: str,
    fault_id: str | None,
) -> tuple[ProductionState, tuple[ExecutionResult, ...], int, int]:
    authority = HumanApprovalAuthority(seed=scenario.seed)
    executor = ControlledExecutor(scenario.action_policies)
    state = scenario.new_production_state()
    denied_actions = 0
    execution_provenance: tuple[tuple[str, str], ...] = ()
    if scenario.fault_study_name is not None:
        execution_provenance = (("plan_id", plan_id),)
        if fault_id is not None:
            execution_provenance += (("fault_id", fault_id),)

    # Every ablation exercises the same unsafe requests.  Keeping this workload
    # constant means score differences come from orchestration features, not
    # from one strategy receiving easier policy tests.
    for proposal in _unsafe_proposals(diagnosis.evidence_ids):
        current_ms += 100
        log.emit(
            current_ms,
            EventKind.APPROVAL_REQUESTED,
            actor=Role.ORCHESTRATOR if config.specialist_roles else Role.GENERALIST,
            message=proposal.summary,
            metadata=(
                ("action_id", proposal.action_id),
                ("evidence_ids", _csv(proposal.evidence_ids)),
                ("plan_id", plan_id),
            ),
        )
        try:
            unsafe_policy: ActionPolicy | None = scenario.action_policy_for(
                proposal.action_id
            )
        except KeyError:
            unsafe_policy = None
        decision = authority.decide(
            proposal,
            unsafe_policy,
            board,
            at_ms=current_ms,
            state_version=state.version,
            review_accepted=review_accepted,
            log=log,
        )
        denied_actions += int(not decision.approved)

    execution_results = []
    for proposal in plan.actions:
        governed_proposal = replace(
            proposal,
            metadata=proposal.metadata + execution_provenance,
        )
        current_ms += 500
        request_metadata = (
            ("action_id", governed_proposal.action_id),
            ("evidence_ids", _csv(governed_proposal.evidence_ids)),
            ("idempotency_key", governed_proposal.idempotency_key),
            ("plan_id", plan_id),
            ("risk", governed_proposal.risk.value),
        )
        if fault_id is not None:
            request_metadata += (("fault_id", fault_id),)
        log.emit(
            current_ms,
            EventKind.APPROVAL_REQUESTED,
            actor=Role.ORCHESTRATOR if config.specialist_roles else Role.GENERALIST,
            message=governed_proposal.summary,
            metadata=request_metadata,
        )
        policy = scenario.action_policy_for(governed_proposal.action_id)
        decision = authority.decide(
            governed_proposal,
            policy,
            board,
            at_ms=current_ms,
            state_version=state.version,
            review_accepted=review_accepted,
            log=log,
        )
        current_ms += 50
        execution_results.append(
            executor.execute(
                governed_proposal,
                decision.capability,
                state,
                at_ms=current_ms,
                log=log,
            )
        )
    return state, tuple(execution_results), current_ms, denied_actions


def _verify(
    *,
    state: ProductionState,
    log: EventLog,
    current_ms: int,
    actor: Role,
    plan_id: str,
    fault_id: str | None,
    include_provenance: bool,
) -> tuple[int, int]:
    passes = 0
    for pass_number in (1, 2):
        current_ms += 30_000
        passed = state_is_recovered(state)
        passes += int(passed)
        verification_metadata: tuple[tuple[str, str], ...] = (
            ("checkout_success", f"{state.checkout_success:.3f}"),
            (
                "duplicate_authorization_rate",
                f"{state.duplicate_authorization_rate:.4f}",
            ),
            ("oversold_seats", str(state.oversold_seats)),
            ("pass", str(pass_number)),
            ("payment_timeout_rate", f"{state.payment_timeout_rate:.3f}"),
            ("slo_passed", _bool(passed)),
        )
        if include_provenance:
            verification_metadata += (("plan_id", plan_id),)
            if fault_id is not None:
                verification_metadata += (("fault_id", fault_id),)
        log.emit(
            current_ms,
            EventKind.VERIFICATION_COMPLETED,
            actor=actor,
            message="service objectives pass" if passed else "service remains degraded",
            metadata=verification_metadata,
        )
    return passes, current_ms


def _finalize_result(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    task_results: tuple[TaskResult, ...],
    board: EvidenceBoard,
    log: EventLog,
    ledger: BudgetLedger,
    current_ms: int,
    diagnosis: Diagnosis,
    execution_results: tuple[ExecutionResult, ...],
    recovered: bool,
    critic_rejections: int,
    replans: int,
    denied_actions: int,
    state: ProductionState,
) -> SimulationResult:
    ledger.observe_virtual_time(current_ms)
    usage = ledger.usage
    executed_actions = tuple(
        result.action_id for result in execution_results if result.executed
    )
    fault_metadata: tuple[tuple[str, str], ...] = ()
    if scenario.fault_study_name is not None:
        fault_metadata = (
            ("fault_arm", scenario.fault_arm),
            ("fault_study", scenario.fault_study_name),
        )
        if scenario.fault_active:
            fault_metadata += (("fault", scenario.fault_study_name),)
    display_metadata = tuple(
        sorted(
            (
                ("actions", _csv(executed_actions)),
                ("communications_accurate", _bool(recovered)),
                ("deadline_ms", str(scenario.limits.resolution_deadline_ms)),
                ("denied_actions", str(denied_actions)),
                ("diagnosis", _csv(diagnosis.causes)),
                ("diagnosis_causes", _csv(diagnosis.causes)),
                ("diagnosis_evidence_ids", _csv(diagnosis.evidence_ids)),
                ("outcome", "recovered" if recovered else "degraded"),
                ("oversold_seats", str(state.oversold_seats)),
                ("preset", config.name),
                ("variant", scenario.variant_name),
            )
            + fault_metadata
        )
    )
    if scenario.fault_study_name is None:
        fault_suffix = ""
    elif scenario.fault_active:
        fault_suffix = f":fault={scenario.fault_study_name}"
    else:
        fault_suffix = f":study={scenario.fault_study_name}:arm=control"
    draft = SimulationResult(
        run_id=(
            f"aurora:{scenario.seed}:{scenario.variant_name}:{config.name}{fault_suffix}"
        ),
        strategy=config.name,
        status=TaskStatus.SUCCEEDED if recovered else TaskStatus.FAILED,
        started_at_ms=0,
        ended_at_ms=current_ms,
        task_results=task_results,
        evidence=board.items,
        trace=log.events,
        attempts_used=usage.attempts,
        cost_units_used=usage.cost_units,
        tool_calls_used=usage.tool_calls,
        metadata=display_metadata,
    )
    # These compatibility fields are convenient for reports and older callers,
    # but are reconstructed from the trace.  The evaluator independently
    # derives the same facts and never trusts these summaries.
    facts = derive_run_metrics(draft)
    compatibility_metadata = (
        ("budget_compliant", _bool(
            usage.cost_units <= scenario.limits.max_cost_units
            and usage.tool_calls <= scenario.limits.max_tool_calls
        )),
        ("cancellation_count", str(len(facts.cancelled_task_ids))),
        ("critic_rejections", str(facts.critic_rejections)),
        ("injection_denied", _bool(facts.injection_denied)),
        ("parallelism_used", _bool(facts.parallelism_used)),
        ("policy_violations", str(facts.approval_violations)),
        ("replans", str(facts.replans)),
        ("retry_recovered", _bool(facts.retry_recovered)),
        ("self_reviewed", _bool(not config.independent_critic)),
        ("useful_parallelism", _bool(facts.parallelism_used)),
        ("valid_approvals", _bool(
            bool(facts.executed_action_ids) and facts.approval_violations == 0
        )),
        ("verification_passes", str(facts.verification_passes)),
    )
    draft = replace(
        draft,
        metadata=tuple(sorted(display_metadata + compatibility_metadata)),
    )
    score = evaluate(draft, scenario)
    log.emit(
        current_ms,
        EventKind.SCORE_COMPUTED,
        actor=Role.SYSTEM,
        message=f"transparent evaluator score: {score.total:.1f}/100",
        metadata=(("score", f"{score.total:.1f}"),),
    )
    log.emit(
        current_ms + 1,
        EventKind.RUN_COMPLETED,
        actor=Role.SYSTEM,
        message="incident recovered" if recovered else "incident ended in degraded state",
    )
    return replace(
        draft,
        ended_at_ms=current_ms + 1,
        trace=log.events,
        score=score,
    )


def _model_failure_result(
    *,
    scenario: Scenario,
    config: ExperimentConfig,
    task_results: tuple[TaskResult, ...],
    board: EvidenceBoard,
    log: EventLog,
    ledger: BudgetLedger,
    current_ms: int,
    error: ModelSynthesisError,
) -> SimulationResult:
    """Return an auditable stopped run with no approval or execution phase."""

    failed_at_ms = max(current_ms, log.last_at_ms or current_ms)
    ledger.observe_virtual_time(failed_at_ms)
    usage = ledger.usage
    log.emit(
        failed_at_ms + 1,
        EventKind.RUN_COMPLETED,
        actor=Role.SYSTEM,
        message="run stopped safely because no validated model diagnosis was available",
        metadata=(
            ("failure_stage", "comprehensive_diagnosis"),
            ("safe_stop_before_execution", "true"),
        ),
    )
    metadata = tuple(
        sorted(
            (
                ("failure_stage", "comprehensive_diagnosis"),
                ("outcome", "model_synthesis_failed"),
                ("preset", config.name),
                ("safe_stop_before_execution", "true"),
                ("variant", scenario.variant_name),
            )
            + model_event_metadata(error.telemetry, failure_kind=error.kind)
        )
    )
    return SimulationResult(
        run_id=f"aurora:{scenario.seed}:{scenario.variant_name}:{config.name}",
        strategy=config.name,
        status=TaskStatus.FAILED,
        started_at_ms=0,
        ended_at_ms=failed_at_ms + 1,
        task_results=task_results,
        evidence=board.items,
        trace=log.events,
        attempts_used=usage.attempts,
        cost_units_used=usage.cost_units,
        tool_calls_used=usage.tool_calls,
        score=None,
        metadata=metadata,
    )


class ConfigurableStrategy:
    """Run one immutable feature configuration against a fresh scenario state."""

    def __init__(
        self,
        config: ExperimentConfig,
        *,
        comprehensive_synthesizer: ModelBackedSynthesizer | None = None,
    ) -> None:
        if comprehensive_synthesizer is not None and not config.independent_critic:
            raise ValueError(
                "model synthesis requires a strategy with an independent critic"
            )
        self.config = config
        self.name = config.name
        self.comprehensive_synthesizer = comprehensive_synthesizer

    def run(self, scenario: Scenario) -> SimulationResult:
        if (
            self.comprehensive_synthesizer is not None
            and scenario.fault_study_name is not None
        ):
            raise ValueError("model synthesis is disabled for matched fault studies")
        limits = _limits(scenario)
        ledger = BudgetLedger(limits)
        log = EventLog()
        board = EvidenceBoard()
        scheduler = VirtualScheduler(limits, ledger)

        if scenario.fault_study_name is None:
            run_metadata = (("seed", str(scenario.seed)), ("strategy", self.name))
        else:
            run_metadata = (
                ("fault_arm", scenario.fault_arm),
                ("fault_study", scenario.fault_study_name),
                ("seed", str(scenario.seed)),
                ("strategy", self.name),
            )
            if scenario.fault_active:
                run_metadata = (("fault", scenario.fault_study_name),) + run_metadata
        log.emit(
            0,
            EventKind.RUN_STARTED,
            actor=Role.SYSTEM,
            message=f"AuroraTickets incident: {scenario.variant.summary}",
            metadata=run_metadata,
        )
        log.emit(
            0,
            EventKind.GOAL_CREATED,
            actor=Role.ORCHESTRATOR if self.config.specialist_roles else Role.GENERALIST,
            message=(
                "Restore checkout within 15 simulated minutes with no new duplicate "
                "charges, oversell, policy bypass, or unsupported communication."
            ),
            metadata=(
                ("deadline_ms", str(scenario.limits.resolution_deadline_ms)),
                ("preset", self.name),
            ),
        )

        (
            task_results,
            current_ms,
            worker_fault_state,
            budget_fault_state,
        ) = _run_investigations(
            scenario=scenario,
            config=self.config,
            scheduler=scheduler,
            board=board,
            log=log,
        )
        try:
            (
                diagnosis,
                plan,
                current_ms,
                critic_rejections,
                replans,
                review_accepted,
                planning_fault_application,
                plan_id,
            ) = _build_plan(
                config=self.config,
                fault=scenario.fault,
                fault_active=scenario.fault_active,
                fault_target_action_id=scenario.fault_target_action_id,
                seed=scenario.seed,
                board=board,
                ledger=ledger,
                log=log,
                current_ms=current_ms,
                comprehensive_synthesizer=self.comprehensive_synthesizer,
            )
        except ModelSynthesisError as error:
            return _model_failure_result(
                scenario=scenario,
                config=self.config,
                task_results=task_results,
                board=board,
                log=log,
                ledger=ledger,
                current_ms=current_ms,
                error=error,
            )
        active_fault_id = (
            planning_fault_application.fault_id
            if planning_fault_application is not None
            else (
                budget_fault_state.fault_id
                if budget_fault_state.injected
                else worker_fault_state.fault_id
            )
        )
        state, execution_results, current_ms, denied_actions = _execute_plan(
            scenario=scenario,
            config=self.config,
            diagnosis=diagnosis,
            plan=plan,
            board=board,
            log=log,
            current_ms=current_ms,
            review_accepted=review_accepted,
            plan_id=plan_id,
            fault_id=active_fault_id,
        )
        verification_passes, current_ms = _verify(
            state=state,
            log=log,
            current_ms=current_ms,
            actor=Role.VERIFIER if self.config.specialist_roles else Role.GENERALIST,
            plan_id=plan_id,
            fault_id=active_fault_id,
            include_provenance=scenario.fault_study_name is not None,
        )
        recovered = verification_passes == 2
        if (
            recovered
            and planning_fault_application is not None
            and planning_fault_application.injected
            and planning_fault_application.fault_id is not None
            and planning_fault_application.omitted_action_id is not None
        ):
            log.emit(
                current_ms,
                EventKind.FAULT_REPAIRED,
                actor=Role.VERIFIER,
                message="the repaired mitigation executed and confirmed recovery",
                metadata=(
                    ("fault_id", planning_fault_application.fault_id),
                    ("fault_name", scenario.fault_study_name or scenario.fault_name),
                    (
                        "omitted_action_id",
                        planning_fault_application.omitted_action_id,
                    ),
                    ("plan_id", plan_id),
                    ("verification_passes", str(verification_passes)),
                ),
            )
        if (
            recovered
            and worker_fault_state.injected
            and worker_fault_state.fault_id is not None
            and worker_fault_state.reassignment_id is not None
            and worker_fault_state.fallback_completed
            and worker_fault_state.application is not None
            and worker_fault_state.application.target_task_id is not None
            and worker_fault_state.fallback_task_id is not None
        ):
            log.emit(
                current_ms,
                EventKind.FAULT_CONTAINED,
                actor=Role.VERIFIER,
                task_id=worker_fault_state.fallback_task_id,
                message=(
                    "recovery was verified around the worker that remains unavailable"
                ),
                metadata=(
                    ("evidence_ids", _csv(worker_fault_state.fallback_evidence_ids)),
                    ("fallback_task_id", worker_fault_state.fallback_task_id),
                    ("fault_id", worker_fault_state.fault_id),
                    (
                        "fault_name",
                        worker_fault_state.application.fault.name,
                    ),
                    ("plan_id", plan_id),
                    ("reassignment_id", worker_fault_state.reassignment_id),
                    (
                        "restored_original_evidence_ids",
                        _csv(
                            worker_fault_state.application.candidate_evidence_ids
                        ),
                    ),
                    (
                        "target_task_id",
                        worker_fault_state.application.target_task_id,
                    ),
                    ("verification_passes", str(verification_passes)),
                ),
            )

        current_ms += 250
        _reserve_agent_turn(ledger, cost_units=1)
        log.emit(
            current_ms,
            EventKind.COMMUNICATION_PUBLISHED,
            actor=Role.COMMUNICATIONS if self.config.specialist_roles else Role.GENERALIST,
            message=(
                "Checkout has recovered; affected payment attempts are being reconciled."
                if recovered
                else "Checkout remains degraded; automated changes have stopped safely."
            ),
            metadata=(
                ("evidence_bound", "true"),
                ("human_reviewed", "true"),
                ("recovered", _bool(recovered)),
            ),
        )
        return _finalize_result(
            scenario=scenario,
            config=self.config,
            task_results=task_results,
            board=board,
            log=log,
            ledger=ledger,
            current_ms=current_ms,
            diagnosis=diagnosis,
            execution_results=execution_results,
            recovered=recovered,
            critic_rejections=critic_rejections,
            replans=replans,
            denied_actions=denied_actions,
            state=state,
        )


class OrchestratedStrategy(ConfigurableStrategy):
    """Backward-compatible alias for full orchestration."""

    name = "orchestrated"

    def __init__(
        self,
        *,
        comprehensive_synthesizer: ModelBackedSynthesizer | None = None,
    ) -> None:
        super().__init__(
            replace(FULL_ORCHESTRATION, name=self.name),
            comprehensive_synthesizer=comprehensive_synthesizer,
        )


class SingleAgentStrategy(ConfigurableStrategy):
    """Backward-compatible alias for the sequential generalist."""

    name = "single_agent"

    def __init__(self) -> None:
        super().__init__(replace(SEQUENTIAL_GENERALIST, name=self.name))


def strategy_for(
    name: str,
    *,
    comprehensive_synthesizer: ModelBackedSynthesizer | None = None,
) -> ConfigurableStrategy:
    try:
        return ConfigurableStrategy(
            PRESET_BY_NAME[name],
            comprehensive_synthesizer=comprehensive_synthesizer,
        )
    except KeyError as error:
        choices = ", ".join(PRESET_BY_NAME)
        raise ValueError(f"unknown strategy {name!r}; choose one of: {choices}") from error


def run_preset(
    name: str,
    seed: int = 101,
    variant_name: str | None = None,
    fault_name: str | None = None,
    *,
    control_for_fault: str | None = None,
) -> SimulationResult:
    return strategy_for(name).run(
        build_scenario(
            seed,
            variant_name,
            fault_name,
            control_for_fault=control_for_fault,
        )
    )


def run_orchestrated(
    seed: int = 101,
    variant_name: str | None = None,
    fault_name: str | None = None,
    *,
    control_for_fault: str | None = None,
) -> SimulationResult:
    return OrchestratedStrategy().run(
        build_scenario(
            seed,
            variant_name,
            fault_name,
            control_for_fault=control_for_fault,
        )
    )


def run_single_agent(
    seed: int = 101,
    variant_name: str | None = None,
    fault_name: str | None = None,
    *,
    control_for_fault: str | None = None,
) -> SimulationResult:
    return SingleAgentStrategy().run(
        build_scenario(
            seed,
            variant_name,
            fault_name,
            control_for_fault=control_for_fault,
        )
    )


def run_comparison(
    seed: int = 101,
    variant_name: str | None = None,
    fault_name: str | None = None,
    *,
    control_for_fault: str | None = None,
) -> tuple[SimulationResult, SimulationResult]:
    scenario = build_scenario(
        seed,
        variant_name,
        fault_name,
        control_for_fault=control_for_fault,
    )
    return OrchestratedStrategy().run(scenario), SingleAgentStrategy().run(scenario)
