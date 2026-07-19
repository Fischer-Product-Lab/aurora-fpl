"""Trace-derived run metrics used by evaluation and experiments."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .model import EventKind, Role, SimulationResult, TaskStatus


@dataclass(frozen=True, slots=True)
class BudgetReservationFact:
    """One trace-linked fallback admission decision and its resource vectors."""

    reservation_id: str
    request_id: str
    study_fault_name: str
    target_task_id: str
    logical_task_id: str
    worker_fault_id: str
    fault_id: str | None
    decision: str
    requested_attempts: int
    requested_cost_units: int
    requested_tool_calls: int
    requested_duration_ms: int
    capacity_attempts: int
    capacity_cost_units: int
    capacity_tool_calls: int
    capacity_duration_ms: int
    scoped_usage_attempts: int
    scoped_usage_cost_units: int
    scoped_usage_tool_calls: int
    scoped_usage_duration_ms: int
    binding_dimensions: tuple[str, ...]
    atomic: bool
    skipped: bool
    zero_spend: bool
    no_dispatch: bool
    ledger_unchanged: bool
    valid: bool
    budget_safe: bool


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """Facts reconstructed from immutable events and task results."""

    last_action_ms: int | None
    first_slo_pass_ms: int | None
    confirmed_recovery_ms: int | None
    verification_passes: int
    max_concurrency: int
    parallelism_used: bool
    cancelled_task_ids: tuple[str, ...]
    retry_recovered: bool
    injection_denied: bool
    critic_rejections: int
    replans: int
    approval_violations: int
    executed_action_ids: tuple[str, ...]
    diagnosis_causes: tuple[str, ...]
    diagnosis_evidence_ids: tuple[str, ...]
    oversold_seats: int | None
    injected_fault_ids: tuple[str, ...]
    injected_fault_names: tuple[str, ...]
    fault_target_action_ids: tuple[str, ...]
    fault_target_task_ids: tuple[str, ...]
    detected_fault_ids: tuple[str, ...]
    repaired_fault_ids: tuple[str, ...]
    terminal_fault_ids: tuple[str, ...]
    reassigned_fault_ids: tuple[str, ...]
    fallback_completed_fault_ids: tuple[str, ...]
    contained_fault_ids: tuple[str, ...]
    fallback_task_ids: tuple[str, ...]
    study_intervention_fault_ids: tuple[str, ...]
    study_intervention_detected_fault_ids: tuple[str, ...]
    budget_reservations: tuple[BudgetReservationFact, ...]
    fallback_skipped_reservation_ids: tuple[str, ...]

    @property
    def recovered(self) -> bool:
        return self.confirmed_recovery_ms is not None

    @property
    def fault_injected(self) -> bool:
        return bool(self.injected_fault_ids)

    @property
    def fault_detected(self) -> bool:
        return bool(self.detected_fault_ids)

    @property
    def fault_repaired(self) -> bool:
        return bool(self.repaired_fault_ids)

    @property
    def target_terminal_failure(self) -> bool:
        return bool(self.terminal_fault_ids)

    @property
    def reassignment_attempted(self) -> bool:
        return bool(self.reassigned_fault_ids)

    @property
    def fallback_succeeded(self) -> bool:
        return bool(self.fallback_completed_fault_ids)

    @property
    def fault_contained(self) -> bool:
        return bool(self.contained_fault_ids)

    @property
    def study_fault_injected(self) -> bool:
        return bool(self.study_intervention_fault_ids)

    @property
    def study_fault_detected(self) -> bool:
        return bool(self.study_intervention_detected_fault_ids)

    @property
    def budget_safe(self) -> bool:
        return all(
            fact.valid and fact.budget_safe for fact in self.budget_reservations
        )


def _event_value(event: object, key: str) -> str | None:
    values = [value for candidate, value in getattr(event, "metadata", ()) if candidate == key]
    return values[-1] if values else None


def _csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _verification_snapshot(
    event: object,
) -> tuple[float, float, float, int] | None:
    """Parse one complete, finite, range-valid service snapshot."""

    try:
        checkout_success = float(_event_value(event, "checkout_success") or "")
        payment_timeout_rate = float(
            _event_value(event, "payment_timeout_rate") or ""
        )
        duplicate_rate = float(
            _event_value(event, "duplicate_authorization_rate") or ""
        )
        oversold_seats = int(_event_value(event, "oversold_seats") or "")
    except (TypeError, ValueError):
        return None
    rates = (checkout_success, payment_timeout_rate, duplicate_rate)
    if not all(isfinite(value) and 0.0 <= value <= 1.0 for value in rates):
        return None
    if oversold_seats < 0:
        return None
    return checkout_success, payment_timeout_rate, duplicate_rate, oversold_seats


def _verification_passed(event: object) -> bool:
    """Validate a verification claim against its complete numeric snapshot."""

    explicit = _event_value(event, "slo_passed")
    snapshot = _verification_snapshot(event)
    if snapshot is None:
        return False
    checkout_success, payment_timeout_rate, duplicate_rate, oversold_seats = snapshot
    snapshot_passed = (
        checkout_success >= 0.90
        and payment_timeout_rate < 0.05
        and duplicate_rate < 0.001
        and oversold_seats == 0
    )
    if explicit is None:
        return snapshot_passed
    return (explicit.lower() == "true") and snapshot_passed


def _concurrency(result: SimulationResult) -> int:
    """Measure overlapping tool attempts, excluding retry backoff time."""

    active: dict[str, int] = {}
    intervals: list[tuple[int, int]] = []
    terminal_kinds = {
        EventKind.TASK_SUCCEEDED,
        EventKind.TASK_FAILED,
        EventKind.TASK_TIMED_OUT,
        EventKind.TASK_CANCELLED,
        EventKind.TASK_RETRY_SCHEDULED,
    }
    for event in result.trace:
        task_id = event.task_id
        if not task_id:
            continue
        if event.kind is EventKind.TASK_STARTED:
            # A repeated start without a closing event is malformed.  Close the
            # prior interval at the new start so it cannot inflate concurrency.
            prior = active.pop(task_id, None)
            if prior is not None and event.at_ms > prior:
                intervals.append((prior, event.at_ms))
            active[task_id] = event.at_ms
        elif event.kind in terminal_kinds:
            started_at = active.pop(task_id, None)
            if started_at is not None and event.at_ms > started_at:
                intervals.append((started_at, event.at_ms))

    boundaries: list[tuple[int, int]] = []
    for started_at, ended_at in intervals:
        boundaries.append((started_at, 1))
        boundaries.append((ended_at, -1))
    active = 0
    maximum = 0
    for _, delta in sorted(boundaries, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def _approval_facts(result: SimulationResult) -> tuple[int, tuple[str, ...]]:
    available: dict[str, int] = {}
    violations = 0
    executed: list[str] = []
    for event in result.trace:
        action_id = _event_value(event, "action_id")
        if event.kind is EventKind.APPROVAL_GRANTED and action_id:
            available[action_id] = available.get(action_id, 0) + 1
        elif event.kind is EventKind.ACTION_EXECUTED:
            if not action_id:
                violations += 1
                continue
            executed.append(action_id)
            if available.get(action_id, 0) < 1:
                violations += 1
            else:
                available[action_id] -= 1
    return violations, tuple(executed)


def _diagnosis_facts(result: SimulationResult) -> tuple[tuple[str, ...], tuple[str, ...]]:
    causes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    for event in reversed(result.trace):
        if event.kind not in {EventKind.REPLAN_CREATED, EventKind.SYNTHESIS_CREATED}:
            continue
        if not causes:
            causes = _csv(_event_value(event, "causes"))
        if not evidence_ids:
            evidence_ids = _csv(_event_value(event, "evidence_ids"))
        if causes and evidence_ids:
            break
    return causes, evidence_ids


def _planning_fault_facts(
    result: SimulationResult,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Validate fault detection and repair as linked, ordered lifecycles."""

    injections = tuple(
        event for event in result.trace if event.kind is EventKind.FAULT_INJECTED
    )
    injected_ids: list[str] = []
    names: list[str] = []
    targets: list[str] = []
    detected: list[str] = []
    repaired: list[str] = []

    def later_events(kind: EventKind, sequence: int) -> tuple[object, ...]:
        return tuple(
            event
            for event in result.trace
            if event.kind is kind and event.sequence > sequence
        )

    for injection in injections:
        fault_id = _event_value(injection, "fault_id")
        fault_name = _event_value(injection, "fault_name")
        target = _event_value(injection, "omitted_action_id")
        plan_id = _event_value(injection, "plan_id")
        if not all((fault_id, fault_name, target, plan_id)):
            continue
        assert fault_id is not None
        assert fault_name is not None
        assert target is not None
        assert plan_id is not None
        candidate_actions = _csv(_event_value(injection, "candidate_actions"))
        faulty_actions = _csv(_event_value(injection, "faulty_actions"))
        if (
            target not in candidate_actions
            or target in faulty_actions
            or len(set(candidate_actions)) != len(candidate_actions)
            or len(set(faulty_actions)) != len(faulty_actions)
            or set(faulty_actions) != set(candidate_actions) - {target}
        ):
            continue
        injected_ids.append(fault_id)
        names.append(fault_name)
        targets.append(target)

        proposed = next(
            (
                event
                for event in later_events(EventKind.PLAN_PROPOSED, injection.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == plan_id
                and target not in _csv(_event_value(event, "actions"))
                and set(_csv(_event_value(event, "actions"))) == set(faulty_actions)
            ),
            None,
        )
        if proposed is None:
            continue
        rejection = next(
            (
                event
                for event in later_events(
                    EventKind.CRITIQUE_REJECTED, proposed.sequence
                )
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == plan_id
                and target in _csv(_event_value(event, "missing_actions"))
            ),
            None,
        )
        if rejection is None:
            continue
        detection = next(
            (
                event
                for event in later_events(EventKind.FAULT_DETECTED, rejection.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "omitted_action_id") == target
                and _event_value(event, "plan_id") == plan_id
            ),
            None,
        )
        if detection is None:
            continue
        detected.append(fault_id)

        replan = next(
            (
                event
                for event in later_events(EventKind.REPLAN_CREATED, detection.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "supersedes") == plan_id
                and target in _csv(_event_value(event, "actions"))
            ),
            None,
        )
        if replan is None:
            continue
        repaired_plan_id = _event_value(replan, "plan_id")
        if not repaired_plan_id or repaired_plan_id == plan_id:
            continue
        accepted = next(
            (
                event
                for event in later_events(EventKind.CRITIQUE_ACCEPTED, replan.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == repaired_plan_id
            ),
            None,
        )
        if accepted is None:
            continue
        execution = next(
            (
                event
                for event in later_events(EventKind.ACTION_EXECUTED, accepted.sequence)
                if _event_value(event, "action_id") == target
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == repaired_plan_id
            ),
            None,
        )
        if execution is None:
            continue
        last_action_sequence = max(
            (
                event.sequence
                for event in result.trace
                if event.kind is EventKind.ACTION_EXECUTED
            ),
            default=execution.sequence,
        )
        first_pass = next(
            (
                event
                for event in later_events(
                    EventKind.VERIFICATION_COMPLETED, last_action_sequence
                )
                if _verification_passed(event)
                and _event_value(event, "pass") == "1"
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == repaired_plan_id
            ),
            None,
        )
        if first_pass is None:
            continue
        second_pass = next(
            (
                event
                for event in later_events(
                    EventKind.VERIFICATION_COMPLETED, first_pass.sequence
                )
                if _verification_passed(event)
                and _event_value(event, "pass") == "2"
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == repaired_plan_id
            ),
            None,
        )
        if second_pass is None:
            continue
        repair = next(
            (
                event
                for event in later_events(EventKind.FAULT_REPAIRED, second_pass.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "omitted_action_id") == target
                and _event_value(event, "plan_id") == repaired_plan_id
            ),
            None,
        )
        if repair is not None:
            repaired.append(fault_id)

    return (
        tuple(injected_ids),
        tuple(names),
        tuple(targets),
        tuple(detected),
        tuple(repaired),
    )


def _worker_fault_facts(
    result: SimulationResult,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Validate terminal worker loss and bounded containment end to end."""

    injected: list[str] = []
    names: list[str] = []
    target_tasks: list[str] = []
    terminal: list[str] = []
    detected: list[str] = []
    reassigned: list[str] = []
    fallback_completed: list[str] = []
    contained: list[str] = []
    fallback_tasks: list[str] = []

    def later(kind: EventKind, sequence: int) -> tuple[object, ...]:
        return tuple(
            event
            for event in result.trace
            if event.kind is kind and event.sequence > sequence
        )

    for injection in result.trace:
        if (
            injection.kind is not EventKind.FAULT_INJECTED
            or _event_value(injection, "fault_stage") != "investigation"
        ):
            continue
        fault_id = _event_value(injection, "fault_id")
        fault_name = _event_value(injection, "fault_name")
        target_task = _event_value(injection, "target_task_id")
        raw_attempt = _event_value(injection, "target_attempt")
        candidate_fingerprint = _event_value(injection, "candidate_fingerprint")
        faulty_fingerprint = _event_value(injection, "faulty_fingerprint")
        candidate_evidence = _csv(
            _event_value(injection, "candidate_evidence_ids")
        )
        try:
            target_attempt = int(raw_attempt or "")
        except ValueError:
            continue
        if (
            not fault_id
            or not fault_name
            or not target_task
            or injection.task_id != target_task
            or target_attempt < 2
            or _event_value(injection, "candidate_status") != "succeeded"
            or _event_value(injection, "faulty_status") != "failed"
            or not candidate_fingerprint
            or not faulty_fingerprint
            or candidate_fingerprint == faulty_fingerprint
            or not candidate_evidence
            or len(set(candidate_evidence)) != len(candidate_evidence)
        ):
            continue

        attempt = next(
            (
                event
                for event in result.trace
                if event.kind is EventKind.TOOL_ATTEMPT
                and event.task_id == target_task
                and event.sequence < injection.sequence
                and _event_value(event, "attempt") == str(target_attempt)
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "fault_injected") == "true"
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "fault_stage") == "investigation"
                and _event_value(event, "target_task_id") == target_task
                and _event_value(event, "target_attempt")
                == str(target_attempt)
                and _event_value(event, "candidate_status")
                == _event_value(injection, "candidate_status")
                and _event_value(event, "faulty_status")
                == _event_value(injection, "faulty_status")
                and _event_value(event, "candidate_fingerprint")
                == candidate_fingerprint
                and _event_value(event, "faulty_fingerprint")
                == faulty_fingerprint
                and _csv(_event_value(event, "candidate_evidence_ids"))
                == candidate_evidence
            ),
            None,
        )
        retry = next(
            (
                event
                for event in result.trace
                if event.kind is EventKind.TASK_RETRY_SCHEDULED
                and event.task_id == target_task
                and event.sequence < getattr(attempt, "sequence", -1)
                and _event_value(event, "next_attempt") == str(target_attempt)
            ),
            None,
        )
        if attempt is None or retry is None:
            continue
        injected.append(fault_id)
        names.append(fault_name)
        target_tasks.append(target_task)

        exhausted = next(
            (
                event
                for event in later(EventKind.RETRY_EXHAUSTED, injection.sequence)
                if event.task_id == target_task
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "target_task_id") == target_task
                and _event_value(event, "attempts") == str(target_attempt)
            ),
            None,
        )
        if exhausted is None:
            continue
        failed = next(
            (
                event
                for event in later(EventKind.TASK_FAILED, exhausted.sequence)
                if event.task_id == target_task
                and _event_value(event, "attempts") == str(target_attempt)
            ),
            None,
        )
        target_result = next(
            (task for task in result.task_results if task.task_id == target_task),
            None,
        )
        if (
            failed is None
            or target_result is None
            or target_result.status is not TaskStatus.FAILED
            or target_result.attempts != target_attempt
            or target_result.evidence_ids
            or any(
                item.task_id == target_task for item in result.evidence
            )
            or any(
                event.kind is EventKind.EVIDENCE_COMMITTED
                and event.task_id == target_task
                for event in result.trace
            )
            or not set(candidate_evidence).isdisjoint(
                item.evidence_id for item in result.evidence
            )
            or any(
                event.kind is EventKind.TASK_SUCCEEDED
                and event.task_id == target_task
                and event.sequence > injection.sequence
                for event in result.trace
            )
        ):
            continue
        terminal.append(fault_id)

        detection = next(
            (
                event
                for event in later(EventKind.FAULT_DETECTED, failed.sequence)
                if event.task_id == target_task
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "target_task_id") == target_task
            ),
            None,
        )
        if detection is None:
            continue
        detected_original_worker_id = _event_value(
            detection, "original_worker_id"
        )
        detected_original_assignment_id = _event_value(
            detection, "original_assignment_id"
        )
        if (
            not detected_original_worker_id
            or not detected_original_assignment_id
            or _event_value(detection, "attempts") != str(target_attempt)
        ):
            continue
        detected.append(fault_id)

        assignment = next(
            (
                event
                for event in later(EventKind.TASK_REASSIGNED, detection.sequence)
                if _event_value(event, "fault_id") == fault_id
                and _event_value(event, "target_task_id") == target_task
            ),
            None,
        )
        if assignment is None:
            continue
        fallback_task = _event_value(assignment, "fallback_task_id")
        reassignment_id = _event_value(assignment, "reassignment_id")
        original_worker_id = _event_value(assignment, "original_worker_id")
        fallback_worker_id = _event_value(assignment, "fallback_worker_id")
        original_assignment_id = _event_value(
            assignment, "original_assignment_id"
        )
        fallback_assignment_id = _event_value(
            assignment, "fallback_assignment_id"
        )
        if (
            not fallback_task
            or fallback_task == target_task
            or assignment.task_id != fallback_task
            or not reassignment_id
            or _event_value(assignment, "fault_name") != fault_name
            or _event_value(assignment, "logical_task_id") != target_task
            or not original_worker_id
            or original_worker_id != detected_original_worker_id
            or not fallback_worker_id
            or original_worker_id == fallback_worker_id
            or not original_assignment_id
            or original_assignment_id != detected_original_assignment_id
            or not fallback_assignment_id
            or original_assignment_id == fallback_assignment_id
        ):
            continue
        reassigned.append(fault_id)

        fallback = next(
            (
                event
                for event in later(EventKind.FALLBACK_COMPLETED, assignment.sequence)
                if event.task_id == fallback_task
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "target_task_id") == target_task
                and _event_value(event, "fallback_task_id") == fallback_task
                and _event_value(event, "reassignment_id") == reassignment_id
                and _event_value(event, "original_worker_id")
                == original_worker_id
                and _event_value(event, "fallback_worker_id")
                == fallback_worker_id
                and _event_value(event, "original_assignment_id")
                == original_assignment_id
                and _event_value(event, "fallback_assignment_id")
                == fallback_assignment_id
                and set(_csv(_event_value(
                    event, "restored_original_evidence_ids"
                )))
                == set(candidate_evidence)
            ),
            None,
        )
        if fallback is None:
            continue
        restored_evidence = _csv(_event_value(fallback, "evidence_ids"))
        fallback_result = next(
            (task for task in result.task_results if task.task_id == fallback_task),
            None,
        )
        evidence_by_id = {item.evidence_id: item for item in result.evidence}
        restored_items = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in restored_evidence
            if evidence_id in evidence_by_id
        )
        restored_original_ids = {
            _event_value(item, "original_evidence_id")
            for item in restored_items
        }
        fallback_attempt = next(
            (
                event
                for event in result.trace
                if event.kind is EventKind.TOOL_ATTEMPT
                and event.task_id == fallback_task
                and assignment.sequence < event.sequence < fallback.sequence
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "target_task_id") == target_task
                and _event_value(event, "fallback_task_id") == fallback_task
                and _event_value(event, "reassignment_id") == reassignment_id
                and _event_value(event, "original_worker_id")
                == original_worker_id
                and _event_value(event, "original_assignment_id")
                == original_assignment_id
                and _event_value(event, "fallback_worker_id")
                == fallback_worker_id
                and _event_value(event, "fallback_assignment_id")
                == fallback_assignment_id
            ),
            None,
        )
        committed_evidence = tuple(
            event
            for event in result.trace
            if event.kind is EventKind.EVIDENCE_COMMITTED
            and event.task_id == fallback_task
            and event.actor is Role.GENERALIST
            and assignment.sequence < event.sequence < fallback.sequence
        )
        committed_evidence_ids = tuple(
            _event_value(event, "evidence_id")
            for event in committed_evidence
        )
        if (
            not restored_evidence
            or len(set(restored_evidence)) != len(restored_evidence)
            or set(restored_evidence).intersection(candidate_evidence)
            or fallback_result is None
            or fallback_result.status is not TaskStatus.SUCCEEDED
            or fallback_result.role is not Role.GENERALIST
            or tuple(fallback_result.evidence_ids) != restored_evidence
            or fallback_attempt is None
            or committed_evidence_ids != restored_evidence
            or not set(candidate_evidence).issubset(restored_original_ids)
            or any(
                evidence_id not in evidence_by_id
                or not evidence_by_id[evidence_id].trusted
                or evidence_by_id[evidence_id].task_id != fallback_task
                or evidence_by_id[evidence_id].role is not Role.GENERALIST
                or _event_value(evidence_by_id[evidence_id], "fallback_for")
                != target_task
                or not _event_value(
                    evidence_by_id[evidence_id], "original_evidence_id"
                )
                or not _event_value(
                    evidence_by_id[evidence_id], "original_task_id"
                )
                or (
                    _event_value(
                        evidence_by_id[evidence_id],
                        "original_evidence_id",
                    )
                    in candidate_evidence
                    and _event_value(
                        evidence_by_id[evidence_id],
                        "original_task_id",
                    )
                    != target_task
                )
                for evidence_id in restored_evidence
            )
            or not any(
                event.kind is EventKind.TASK_STARTED
                and event.task_id == fallback_task
                and event.actor is Role.GENERALIST
                and assignment.sequence < event.sequence < fallback.sequence
                for event in result.trace
            )
            or not any(
                event.kind is EventKind.TASK_SUCCEEDED
                and event.task_id == fallback_task
                and event.actor is Role.GENERALIST
                and assignment.sequence < event.sequence < fallback.sequence
                for event in result.trace
            )
        ):
            continue
        fallback_completed.append(fault_id)
        fallback_tasks.append(fallback_task)

        last_action_sequence = max(
            (
                event.sequence
                for event in result.trace
                if event.kind is EventKind.ACTION_EXECUTED
            ),
            default=fallback.sequence,
        )
        first_pass = next(
            (
                event
                for event in later(
                    EventKind.VERIFICATION_COMPLETED,
                    max(fallback.sequence, last_action_sequence),
                )
                if _verification_passed(event)
                and _event_value(event, "pass") == "1"
                and _event_value(event, "fault_id") == fault_id
                and bool(_event_value(event, "plan_id"))
            ),
            None,
        )
        if first_pass is None:
            continue
        plan_id = _event_value(first_pass, "plan_id")
        assert plan_id is not None
        linked_actions = tuple(
            event
            for event in result.trace
            if event.kind is EventKind.ACTION_EXECUTED
            and fallback.sequence < event.sequence < first_pass.sequence
        )
        if not linked_actions or any(
            _event_value(event, "fault_id") != fault_id
            or _event_value(event, "plan_id") != plan_id
            for event in linked_actions
        ):
            continue
        second_pass = next(
            (
                event
                for event in later(EventKind.VERIFICATION_COMPLETED, first_pass.sequence)
                if _verification_passed(event)
                and _event_value(event, "pass") == "2"
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "plan_id") == plan_id
            ),
            None,
        )
        if second_pass is None:
            continue
        containment = next(
            (
                event
                for event in later(EventKind.FAULT_CONTAINED, second_pass.sequence)
                if event.task_id == fallback_task
                and _event_value(event, "fault_id") == fault_id
                and _event_value(event, "fault_name") == fault_name
                and _event_value(event, "target_task_id") == target_task
                and _event_value(event, "fallback_task_id") == fallback_task
                and _event_value(event, "reassignment_id") == reassignment_id
                and _event_value(event, "plan_id") == plan_id
                and _event_value(event, "verification_passes") == "2"
                and _csv(_event_value(event, "evidence_ids"))
                == restored_evidence
                and set(_csv(_event_value(
                    event, "restored_original_evidence_ids"
                )))
                == set(candidate_evidence)
            ),
            None,
        )
        falsely_repaired = any(
            event.kind is EventKind.FAULT_REPAIRED
            and _event_value(event, "fault_id") == fault_id
            for event in result.trace
        )
        if containment is not None and not falsely_repaired:
            contained.append(fault_id)

    return (
        tuple(injected),
        tuple(names),
        tuple(target_tasks),
        tuple(terminal),
        tuple(detected),
        tuple(reassigned),
        tuple(fallback_completed),
        tuple(contained),
        tuple(fallback_tasks),
    )


def _int_event_value(event: object, key: str) -> int | None:
    try:
        value = int(_event_value(event, key) or "")
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _fallback_budget_facts(
    result: SimulationResult,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[BudgetReservationFact, ...],
    tuple[str, ...],
]:
    """Validate atomic fallback admission independently of the worker fault."""

    study_injections = tuple(
        event
        for event in result.trace
        if event.kind is EventKind.FAULT_INJECTED
        and _event_value(event, "fault_role") == "study_intervention"
        and _event_value(event, "fault_name") == "fallback_budget_exhaustion"
    )
    facts: list[BudgetReservationFact] = []
    linked_injections: list[str] = []
    linked_detections: list[str] = []
    skipped_ids: list[str] = []

    vector_keys = (
        "attempts",
        "cost_units",
        "tool_calls",
        "duration_ms",
    )
    for request in result.trace:
        if (
            request.kind is not EventKind.BUDGET_RESERVATION_REQUESTED
            or _event_value(request, "scope") != "fallback"
            or _event_value(request, "study_fault_name")
            != "fallback_budget_exhaustion"
        ):
            continue
        reservation_id = _event_value(request, "reservation_id") or ""
        request_id = _event_value(request, "request_id") or ""
        target_task = _event_value(request, "target_task_id") or ""
        logical_task = _event_value(request, "logical_task_id") or ""
        worker_fault_id = _event_value(request, "worker_fault_id") or ""
        requested = tuple(
            _int_event_value(request, f"requested_{key}") for key in vector_keys
        )
        capacity = tuple(
            _int_event_value(request, f"capacity_{key}") for key in vector_keys
        )
        shared_worker_injection = next(
            (
                event
                for event in result.trace
                if event.sequence < request.sequence
                and event.kind is EventKind.FAULT_INJECTED
                and _event_value(event, "fault_role") == "shared_condition"
                and _event_value(event, "fault_name")
                == "permanent_worker_failure"
                and _event_value(event, "fault_id") == worker_fault_id
                and _event_value(event, "target_task_id") == logical_task
            ),
            None,
        )
        common_valid = bool(
            reservation_id
            and request_id
            and target_task
            and logical_task
            and worker_fault_id
            and target_task != logical_task
            and request.task_id == target_task
            and _event_value(request, "atomic") == "true"
            and _event_value(request, "fault_role") == "study_intervention"
            and shared_worker_injection is not None
            and all(value is not None for value in requested + capacity)
        )

        decision = next(
            (
                event
                for event in result.trace
                if event.sequence > request.sequence
                and event.kind
                in {
                    EventKind.BUDGET_RESERVATION_GRANTED,
                    EventKind.BUDGET_RESERVATION_DENIED,
                }
                and _event_value(event, "reservation_id") == reservation_id
                and _event_value(event, "request_id") == request_id
            ),
            None,
        )
        decision_name = (
            "granted"
            if decision is not None
            and decision.kind is EventKind.BUDGET_RESERVATION_GRANTED
            else "denied"
            if decision is not None
            and decision.kind is EventKind.BUDGET_RESERVATION_DENIED
            else "missing"
        )
        scoped_usage = tuple(
            _int_event_value(decision, f"scoped_usage_{key}")
            if decision is not None
            else None
            for key in vector_keys
        )
        vectors_present = all(
            value is not None for value in requested + capacity + scoped_usage
        )
        requested_values = tuple(value or 0 for value in requested)
        capacity_values = tuple(value or 0 for value in capacity)
        usage_values = tuple(value or 0 for value in scoped_usage)
        fits = all(
            requested_value <= capacity_value
            for requested_value, capacity_value in zip(
                requested_values,
                capacity_values,
            )
        )
        expected_deficits = tuple(
            max(requested_value - capacity_value, 0)
            for requested_value, capacity_value in zip(
                requested_values,
                capacity_values,
            )
        )
        expected_binding_dimensions = tuple(
            key
            for key, deficit in zip(vector_keys, expected_deficits)
            if deficit > 0
        )
        reported_deficits = tuple(
            _int_event_value(request, f"deficit_{key}") for key in vector_keys
        )
        binding_consistent = (
            all(value is not None for value in reported_deficits)
            and tuple(value or 0 for value in reported_deficits)
            == expected_deficits
            and _csv(_event_value(request, "binding_dimensions"))
            == expected_binding_dimensions
        )

        skip = (
            next(
                (
                    event
                    for event in result.trace
                    if decision is not None
                    and event.sequence > decision.sequence
                    and event.kind is EventKind.FALLBACK_SKIPPED
                    and event.task_id == target_task
                    and _event_value(event, "reservation_id") == reservation_id
                    and _event_value(event, "request_id") == request_id
                    and _event_value(event, "reason")
                    == "fallback_budget_denied"
                    and _event_value(event, "zero_spend") == "true"
                ),
                None,
            )
            if decision_name == "denied"
            else None
        )
        if skip is not None:
            skipped_ids.append(reservation_id)

        denied_result = next(
            (task for task in result.task_results if task.task_id == target_task),
            None,
        )
        cancellation = next(
            (
                event
                for event in result.trace
                if skip is not None
                and event.sequence > skip.sequence
                and event.kind is EventKind.TASK_CANCELLED
                and event.task_id == target_task
                and _event_value(event, "reservation_id") == reservation_id
                and _event_value(event, "request_id") == request_id
                and _event_value(event, "reason") == "fallback_budget_denied"
                and _event_value(event, "attempts") == "0"
                and _event_value(event, "cost_units") == "0"
                and _event_value(event, "tool_calls") == "0"
            ),
            None,
        )
        no_dispatch = not any(
            event.task_id == target_task
            and event.sequence > request.sequence
            and event.kind
            in {
                EventKind.TASK_REASSIGNED,
                EventKind.TASK_STARTED,
                EventKind.TOOL_ATTEMPT,
                EventKind.EVIDENCE_COMMITTED,
            }
            for event in result.trace
        )
        request_ledger_before = tuple(
            _int_event_value(request, f"ledger_before_{key}")
            for key in ("attempts", "cost_units", "tool_calls", "virtual_ms")
        )
        ledger_before = tuple(
            _int_event_value(decision, f"ledger_before_{key}")
            if decision is not None
            else None
            for key in ("attempts", "cost_units", "tool_calls", "virtual_ms")
        )
        ledger_after = tuple(
            _int_event_value(decision, f"ledger_after_{key}")
            if decision is not None
            else None
            for key in ("attempts", "cost_units", "tool_calls", "virtual_ms")
        )
        ledger_unchanged = (
            decision_name == "denied"
            and all(
                value is not None
                for value in request_ledger_before + ledger_before + ledger_after
            )
            and request_ledger_before == ledger_before
            and ledger_before == ledger_after
        )
        zero_spend = (
            decision_name == "denied"
            and usage_values == (0, 0, 0, 0)
            and skip is not None
            and _event_value(skip, "zero_spend") == "true"
        )
        denied_zero_spend = (
            decision_name != "denied"
            or (
                zero_spend
                and ledger_unchanged
                and denied_result is not None
                and denied_result.status is TaskStatus.CANCELLED
                and denied_result.attempts == 0
                and denied_result.started_at_ms is None
                and denied_result.cost_units == 0
                and denied_result.tool_calls == 0
                and cancellation is not None
                and no_dispatch
            )
        )
        grant_exact_spend = (
            decision_name != "granted"
            or usage_values == requested_values
        )
        decision_link_keys = (
            "atomic",
            "binding_dimensions",
            "fault_role",
            "logical_task_id",
            "request_id",
            "reservation_id",
            "scope",
            "study_fault_name",
            "target_task_id",
            "worker_fault_id",
        ) + tuple(
            f"{prefix}_{key}"
            for prefix in ("requested", "capacity", "deficit")
            for key in vector_keys
        )
        decision_links_match = (
            decision is not None
            and decision.task_id == target_task
            and all(
                _event_value(decision, key) == _event_value(request, key)
                for key in decision_link_keys
            )
        )
        unexpected_skip = any(
            event.sequence > getattr(decision, "sequence", request.sequence)
            and event.kind is EventKind.FALLBACK_SKIPPED
            and _event_value(event, "reservation_id") == reservation_id
            and _event_value(event, "request_id") == request_id
            for event in result.trace
        )
        decision_consistent = (
            decision is not None
            and decision_links_match
            and _event_value(decision, "decision") == decision_name
            and _event_value(decision, "atomic") == "true"
            and (
                (
                    decision_name == "granted"
                    and fits
                    and grant_exact_spend
                    and not unexpected_skip
                )
                or (decision_name == "denied" and not fits)
            )
        )
        no_overrun = all(
            usage_value <= capacity_value
            for usage_value, capacity_value in zip(
                usage_values,
                capacity_values,
            )
        )
        valid = (
            common_valid
            and vectors_present
            and binding_consistent
            and decision_consistent
        )
        budget_safe = valid and no_overrun and denied_zero_spend

        injection = next(
            (
                event
                for event in reversed(study_injections)
                if event.sequence < request.sequence
                and _event_value(event, "target_task_id") == target_task
            ),
            None,
        )
        fault_id = _event_value(injection, "fault_id") if injection else None
        if injection is not None and fault_id:
            linked_injections.append(fault_id)
            detection = next(
                (
                    event
                    for event in result.trace
                    if decision is not None
                    and event.sequence > decision.sequence
                    and event.kind is EventKind.FAULT_DETECTED
                    and _event_value(event, "fault_role")
                    == "study_intervention"
                    and _event_value(event, "fault_id") == fault_id
                    and _event_value(event, "reservation_id") == reservation_id
                ),
                None,
            )
            if detection is not None:
                linked_detections.append(fault_id)

        facts.append(
            BudgetReservationFact(
                reservation_id=reservation_id,
                request_id=request_id,
                study_fault_name=(
                    _event_value(request, "study_fault_name") or ""
                ),
                target_task_id=target_task,
                logical_task_id=logical_task,
                worker_fault_id=worker_fault_id,
                fault_id=fault_id,
                decision=decision_name,
                requested_attempts=requested_values[0],
                requested_cost_units=requested_values[1],
                requested_tool_calls=requested_values[2],
                requested_duration_ms=requested_values[3],
                capacity_attempts=capacity_values[0],
                capacity_cost_units=capacity_values[1],
                capacity_tool_calls=capacity_values[2],
                capacity_duration_ms=capacity_values[3],
                scoped_usage_attempts=usage_values[0],
                scoped_usage_cost_units=usage_values[1],
                scoped_usage_tool_calls=usage_values[2],
                scoped_usage_duration_ms=usage_values[3],
                binding_dimensions=_csv(
                    _event_value(request, "binding_dimensions")
                ),
                atomic=_event_value(request, "atomic") == "true",
                skipped=skip is not None,
                zero_spend=zero_spend,
                no_dispatch=no_dispatch,
                ledger_unchanged=ledger_unchanged,
                valid=valid,
                budget_safe=budget_safe,
            )
        )

    return (
        tuple(linked_injections),
        tuple(linked_detections),
        tuple(facts),
        tuple(skipped_ids),
    )


def derive_run_metrics(result: SimulationResult) -> RunMetrics:
    """Reconstruct measurable run facts without trusting summary metadata."""

    action_events = tuple(
        event for event in result.trace if event.kind is EventKind.ACTION_EXECUTED
    )
    action_times = [event.at_ms - result.started_at_ms for event in action_events]
    last_action_sequence = max(
        (event.sequence for event in action_events),
        default=-1,
    )
    valid_verifications = [
        event
        for event in result.trace
        if event.kind is EventKind.VERIFICATION_COMPLETED
        and event.sequence > last_action_sequence
        and _verification_passed(event)
    ]
    first_pass = next(
        (
            event
            for event in valid_verifications
            if _event_value(event, "pass") == "1"
        ),
        None,
    )
    second_pass = (
        next(
            (
                event
                for event in valid_verifications
                if first_pass is not None
                and event.sequence > first_pass.sequence
                and _event_value(event, "pass") == "2"
            ),
            None,
        )
        if first_pass is not None
        else None
    )
    passing_verifications = tuple(
        event for event in (first_pass, second_pass) if event is not None
    )
    first_slo = (
        passing_verifications[0].at_ms - result.started_at_ms
        if passing_verifications
        else None
    )
    confirmed = (
        passing_verifications[1].at_ms - result.started_at_ms
        if len(passing_verifications) >= 2
        else None
    )

    genuine_cancellations: list[str] = []
    for task in result.task_results:
        if (
            task.status is not TaskStatus.CANCELLED
            or task.attempts != 0
            or task.started_at_ms is not None
            or task.cost_units != 0
            or task.tool_calls != 0
        ):
            continue
        queued_events = tuple(
            event
            for event in result.trace
            if event.kind is EventKind.TASK_QUEUED
            and event.task_id == task.task_id
        )
        cancelled_events = tuple(
            event
            for event in result.trace
            if event.kind is EventKind.TASK_CANCELLED
            and event.task_id == task.task_id
        )
        work_events = tuple(
            event
            for event in result.trace
            if event.task_id == task.task_id
            and event.kind in {EventKind.TASK_STARTED, EventKind.TOOL_ATTEMPT}
        )
        if (
            len(queued_events) == 1
            and len(cancelled_events) == 1
            and queued_events[0].sequence < cancelled_events[0].sequence
            and _event_value(cancelled_events[0], "reason") == "redundant"
            and not work_events
            and not any(
                event.kind is EventKind.BUDGET_EXHAUSTED
                and event.task_id == task.task_id
                for event in result.trace
            )
            and task.ended_at_ms == cancelled_events[0].at_ms
        ):
            genuine_cancellations.append(task.task_id)
    cancelled = tuple(sorted(genuine_cancellations))

    retry_tasks = {
        event.task_id
        for event in result.trace
        if event.kind is EventKind.TASK_RETRY_SCHEDULED and event.task_id
    }
    retry_recovered = any(
        task.task_id in retry_tasks
        and task.attempts > 1
        and task.status is TaskStatus.SUCCEEDED
        for task in result.task_results
    )
    approval_violations, executed_actions = _approval_facts(result)
    diagnosis_causes, diagnosis_evidence_ids = _diagnosis_facts(result)
    maximum_concurrency = _concurrency(result)

    oversold: int | None = None
    for event in reversed(result.trace):
        if event.kind is not EventKind.VERIFICATION_COMPLETED:
            continue
        snapshot = _verification_snapshot(event)
        if snapshot is not None:
            oversold = snapshot[3]
            break

    (
        planning_injected_ids,
        planning_fault_names,
        fault_target_action_ids,
        planning_detected_ids,
        repaired_fault_ids,
    ) = _planning_fault_facts(result)
    (
        worker_injected_ids,
        worker_fault_names,
        fault_target_task_ids,
        terminal_fault_ids,
        worker_detected_ids,
        reassigned_fault_ids,
        fallback_completed_fault_ids,
        contained_fault_ids,
        fallback_task_ids,
    ) = _worker_fault_facts(result)
    injected_fault_ids = planning_injected_ids + worker_injected_ids
    injected_fault_names = planning_fault_names + worker_fault_names
    detected_fault_ids = planning_detected_ids + worker_detected_ids
    (
        study_intervention_fault_ids,
        study_intervention_detected_fault_ids,
        budget_reservations,
        fallback_skipped_reservation_ids,
    ) = _fallback_budget_facts(result)

    return RunMetrics(
        last_action_ms=max(action_times) if action_times else None,
        first_slo_pass_ms=first_slo,
        confirmed_recovery_ms=confirmed,
        verification_passes=len(passing_verifications),
        max_concurrency=maximum_concurrency,
        parallelism_used=maximum_concurrency > 1,
        cancelled_task_ids=cancelled,
        retry_recovered=retry_recovered,
        injection_denied=any(
            event.kind is EventKind.SECURITY_DENIED for event in result.trace
        ),
        critic_rejections=sum(
            event.kind is EventKind.CRITIQUE_REJECTED for event in result.trace
        ),
        replans=sum(event.kind is EventKind.REPLAN_CREATED for event in result.trace),
        approval_violations=approval_violations,
        executed_action_ids=executed_actions,
        diagnosis_causes=diagnosis_causes,
        diagnosis_evidence_ids=diagnosis_evidence_ids,
        oversold_seats=oversold,
        injected_fault_ids=injected_fault_ids,
        injected_fault_names=injected_fault_names,
        fault_target_action_ids=fault_target_action_ids,
        fault_target_task_ids=fault_target_task_ids,
        detected_fault_ids=detected_fault_ids,
        repaired_fault_ids=repaired_fault_ids,
        terminal_fault_ids=terminal_fault_ids,
        reassigned_fault_ids=reassigned_fault_ids,
        fallback_completed_fault_ids=fallback_completed_fault_ids,
        contained_fault_ids=contained_fault_ids,
        fallback_task_ids=fallback_task_ids,
        study_intervention_fault_ids=study_intervention_fault_ids,
        study_intervention_detected_fault_ids=(
            study_intervention_detected_fault_ids
        ),
        budget_reservations=budget_reservations,
        fallback_skipped_reservation_ids=fallback_skipped_reservation_ids,
    )
