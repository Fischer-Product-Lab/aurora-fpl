"""Deterministic runtime primitives for the AuroraTickets simulation.

There are no threads and no wall-clock sleeps here.  ``VirtualScheduler``
starts a ready batch at one virtual timestamp, then resolves scripted outcomes
from a stable priority queue.  The resulting overlap is parallel in simulated
time while remaining byte-for-byte reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from itertools import count
from typing import Iterable, Mapping

from .model import (
    EventKind,
    EvidenceItem,
    Metadata,
    Role,
    TaskContract,
    TaskResult,
    TaskStatus,
    ToolAttempt,
    TraceEvent,
)


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Hard run-wide limits.  Zero is a valid limit for denial demonstrations."""

    max_attempts: int = 100
    max_cost_units: int = 100
    max_tool_calls: int = 100
    max_virtual_ms: int = 60_000

    def __post_init__(self) -> None:
        if min(
            self.max_attempts,
            self.max_cost_units,
            self.max_tool_calls,
            self.max_virtual_ms,
        ) < 0:
            raise ValueError("budget limits cannot be negative")


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Immutable usage snapshot returned by ``BudgetLedger.usage``."""

    attempts: int
    cost_units: int
    tool_calls: int
    virtual_ms: int


class BudgetExceeded(RuntimeError):
    """Raised when an atomic budget reservation would exceed a hard limit."""


@dataclass(frozen=True, slots=True)
class ResourceVector:
    """One scoped request or capacity across independently bounded resources."""

    attempts: int = 0
    cost_units: int = 0
    tool_calls: int = 0
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if min(
            self.attempts,
            self.cost_units,
            self.tool_calls,
            self.duration_ms,
        ) < 0:
            raise ValueError("resource values cannot be negative")

    def fits_within(self, capacity: ResourceVector) -> bool:
        """Return whether every requested dimension fits its capacity."""

        return (
            self.attempts <= capacity.attempts
            and self.cost_units <= capacity.cost_units
            and self.tool_calls <= capacity.tool_calls
            and self.duration_ms <= capacity.duration_ms
        )

    def deficits_against(self, capacity: ResourceVector) -> ResourceVector:
        """Return non-negative deficits for each dimension."""

        return ResourceVector(
            attempts=max(0, self.attempts - capacity.attempts),
            cost_units=max(0, self.cost_units - capacity.cost_units),
            tool_calls=max(0, self.tool_calls - capacity.tool_calls),
            duration_ms=max(0, self.duration_ms - capacity.duration_ms),
        )

    def plus(self, other: ResourceVector) -> ResourceVector:
        """Return a component-wise sum without mutating either operand."""

        return ResourceVector(
            attempts=self.attempts + other.attempts,
            cost_units=self.cost_units + other.cost_units,
            tool_calls=self.tool_calls + other.tool_calls,
            duration_ms=self.duration_ms + other.duration_ms,
        )


class ScopedBudgetLedger:
    """Atomic accounting for one pre-committed operational reservation.

    This ledger is deliberately separate from :class:`BudgetLedger`.  A scoped
    admission decision constrains optional fallback work while the run-wide
    ledger continues to fund mandatory synthesis, governance, and
    communication.  A rejected reservation never changes ``usage``.
    """

    def __init__(self, capacity: ResourceVector) -> None:
        self.capacity = capacity
        self._usage = ResourceVector()

    @property
    def usage(self) -> ResourceVector:
        return self._usage

    def can_reserve(self, request: ResourceVector) -> bool:
        return self._usage.plus(request).fits_within(self.capacity)

    def reserve(self, request: ResourceVector) -> ResourceVector:
        """Reserve the complete vector or raise without partial consumption."""

        if not self.can_reserve(request):
            raise BudgetExceeded(
                "scoped resource reservation exceeds its capacity"
            )
        self._usage = self._usage.plus(request)
        return self._usage


class BudgetLedger:
    """Atomic accounting for attempts, cost, tool calls, and virtual time."""

    def __init__(self, limits: BudgetLimits) -> None:
        self.limits = limits
        self._attempts = 0
        self._cost_units = 0
        self._tool_calls = 0
        self._virtual_ms = 0

    @property
    def usage(self) -> BudgetUsage:
        """Return the current immutable usage snapshot."""

        return BudgetUsage(
            attempts=self._attempts,
            cost_units=self._cost_units,
            tool_calls=self._tool_calls,
            virtual_ms=self._virtual_ms,
        )

    def can_reserve(
        self,
        *,
        attempts: int = 1,
        cost_units: int = 0,
        tool_calls: int = 0,
    ) -> bool:
        """Return whether all requested usage can be reserved atomically."""

        self._validate_delta(attempts, cost_units, tool_calls)
        return (
            self._attempts + attempts <= self.limits.max_attempts
            and self._cost_units + cost_units <= self.limits.max_cost_units
            and self._tool_calls + tool_calls <= self.limits.max_tool_calls
        )

    def reserve(
        self,
        *,
        attempts: int = 1,
        cost_units: int = 0,
        tool_calls: int = 0,
    ) -> BudgetUsage:
        """Reserve usage atomically and return the new snapshot.

        No counters change when the reservation is rejected.
        """

        if not self.can_reserve(
            attempts=attempts,
            cost_units=cost_units,
            tool_calls=tool_calls,
        ):
            raise BudgetExceeded("usage reservation exceeds a hard budget limit")
        self._attempts += attempts
        self._cost_units += cost_units
        self._tool_calls += tool_calls
        return self.usage

    def observe_virtual_time(self, at_ms: int) -> BudgetUsage:
        """Advance run-wide virtual time without allowing it to exceed its cap."""

        if at_ms < self._virtual_ms:
            raise ValueError("virtual time cannot move backwards")
        if at_ms > self.limits.max_virtual_ms:
            raise BudgetExceeded("virtual time exceeds its hard budget limit")
        self._virtual_ms = at_ms
        return self.usage

    @staticmethod
    def _validate_delta(attempts: int, cost_units: int, tool_calls: int) -> None:
        if min(attempts, cost_units, tool_calls) < 0:
            raise ValueError("budget reservations cannot be negative")


class EvidenceBoard:
    """Append-only evidence storage with parent-provenance validation."""

    def __init__(self, initial: Iterable[EvidenceItem] = ()) -> None:
        self._items: list[EvidenceItem] = []
        self._by_id: dict[str, EvidenceItem] = {}
        for item in initial:
            self.commit(item)

    @property
    def items(self) -> tuple[EvidenceItem, ...]:
        """Return evidence in commit order."""

        return tuple(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._by_id

    def get(self, evidence_id: str) -> EvidenceItem:
        """Look up committed evidence by ID."""

        return self._by_id[evidence_id]

    def commit(self, item: EvidenceItem) -> EvidenceItem:
        """Commit one new item, rejecting duplicate IDs and dangling parents."""

        if item.evidence_id in self._by_id:
            raise ValueError(f"duplicate evidence_id: {item.evidence_id}")
        missing = tuple(parent for parent in item.parent_ids if parent not in self._by_id)
        if missing:
            raise ValueError(
                f"evidence {item.evidence_id} has uncommitted parents: {missing!r}"
            )
        self._items.append(item)
        self._by_id[item.evidence_id] = item
        return item

    def tags(self, item_or_id: EvidenceItem | str) -> tuple[str, ...]:
        """Read normalized tags from comma-separated ``metadata['tags']`` values."""

        item = self.get(item_or_id) if isinstance(item_or_id, str) else item_or_id
        seen: set[str] = set()
        tags: list[str] = []
        for key, value in item.metadata:
            if key != "tags":
                continue
            for raw_tag in value.split(","):
                tag = raw_tag.strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
        return tuple(tags)


class EventLog:
    """Append-only event log with contiguous sequence and monotonic timestamps."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        """Return events in append order."""

        return tuple(self._events)

    @property
    def last_at_ms(self) -> int | None:
        """Return the latest timestamp, or ``None`` for an empty log."""

        return self._events[-1].at_ms if self._events else None

    def __len__(self) -> int:
        return len(self._events)

    def emit(
        self,
        at_ms: int,
        kind: EventKind,
        *,
        actor: Role = Role.SYSTEM,
        task_id: str | None = None,
        message: str = "",
        metadata: Metadata = (),
    ) -> TraceEvent:
        """Append and return an event, assigning the next contiguous sequence."""

        if at_ms < 0:
            raise ValueError("event timestamp cannot be negative")
        if self._events and at_ms < self._events[-1].at_ms:
            raise ValueError("event timestamps must be nondecreasing")
        event = TraceEvent(
            sequence=len(self._events),
            at_ms=at_ms,
            kind=kind,
            actor=actor,
            task_id=task_id,
            message=message,
            metadata=tuple(sorted(metadata)),
        )
        self._events.append(event)
        return event


@dataclass(slots=True)
class _TaskState:
    contract: TaskContract
    script: tuple[ToolAttempt, ...]
    attempts: int = 0
    first_started_at_ms: int | None = None
    cost_units: int = 0
    tool_calls: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    security_denials: list[Metadata] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Completion:
    attempt: ToolAttempt
    status: TaskStatus
    started_at_ms: int
    error: str | None
    budget_cutoff: bool = False


class VirtualScheduler:
    """Run independent scripted tasks concurrently on a deterministic clock.

    ``run_parallel`` expects tasks that are ready for the same orchestration
    phase; dependency resolution remains the orchestrator's responsibility.
    ``deadline_ms`` and ``max_virtual_ms`` are absolute simulation timestamps.
    Retries are bounded by both the task policy and available scripted attempts.
    """

    _COMPLETE = 0
    _RETRY_READY = 1

    def __init__(
        self,
        limits: BudgetLimits,
        ledger: BudgetLedger | None = None,
    ) -> None:
        self.limits = limits
        self.ledger = ledger if ledger is not None else BudgetLedger(limits)
        if self.ledger.limits != limits:
            raise ValueError("scheduler and ledger must use identical limits")

    def run_parallel(
        self,
        tasks: Iterable[TaskContract],
        scripts: Mapping[str, tuple[ToolAttempt, ...]],
        *,
        start_ms: int = 0,
        board: EvidenceBoard | None = None,
        log: EventLog | None = None,
        emit_queued: bool = True,
    ) -> tuple[tuple[TaskResult, ...], int]:
        """Run a ready batch and return task-ID-sorted results plus end time."""

        evidence_board = board if board is not None else EvidenceBoard()
        event_log = log if log is not None else EventLog()
        ordered_tasks = tuple(sorted(tasks, key=lambda task: task.task_id))
        self._validate_batch(ordered_tasks, scripts, start_ms, event_log)
        self.ledger.observe_virtual_time(start_ms)

        states = {
            task.task_id: _TaskState(task, tuple(scripts[task.task_id]))
            for task in ordered_tasks
        }
        results: dict[str, TaskResult] = {}
        queue: list[tuple[int, int, str, int, object]] = []
        serial = count()

        if emit_queued:
            for task in ordered_tasks:
                event_log.emit(
                    start_ms,
                    EventKind.TASK_QUEUED,
                    actor=task.role,
                    task_id=task.task_id,
                    message=task.description,
                    metadata=(("kind", task.kind),),
                )

        def finish(
            state: _TaskState,
            status: TaskStatus,
            at_ms: int,
            *,
            output: str | None = None,
            error: str | None = None,
        ) -> None:
            if state.contract.task_id in results:
                return
            results[state.contract.task_id] = TaskResult(
                task_id=state.contract.task_id,
                role=state.contract.role,
                status=status,
                attempts=state.attempts,
                started_at_ms=state.first_started_at_ms,
                ended_at_ms=at_ms,
                output=output,
                error=error,
                evidence_ids=tuple(state.evidence_ids),
                cost_units=state.cost_units,
                tool_calls=state.tool_calls,
                security_denials=tuple(state.security_denials),
            )
            event_kind = {
                TaskStatus.SUCCEEDED: EventKind.TASK_SUCCEEDED,
                TaskStatus.FAILED: EventKind.TASK_FAILED,
                TaskStatus.TIMED_OUT: EventKind.TASK_TIMED_OUT,
                TaskStatus.CANCELLED: EventKind.TASK_CANCELLED,
            }[status]
            event_log.emit(
                at_ms,
                event_kind,
                actor=state.contract.role,
                task_id=state.contract.task_id,
                message=error or output or "",
                metadata=(("attempts", str(state.attempts)),),
            )

        def cancel_for_budget(state: _TaskState, at_ms: int, reason: str) -> None:
            event_log.emit(
                at_ms,
                EventKind.BUDGET_EXHAUSTED,
                task_id=state.contract.task_id,
                message=reason,
            )
            finish(state, TaskStatus.CANCELLED, at_ms, error=reason)

        def start_attempt(state: _TaskState, at_ms: int) -> None:
            if state.contract.task_id in results:
                return
            attempt = state.script[state.attempts]
            if at_ms > self.limits.max_virtual_ms:
                cancel_for_budget(state, self.limits.max_virtual_ms, "virtual-time budget exhausted")
                return
            if state.contract.deadline_ms is not None and at_ms > state.contract.deadline_ms:
                finish(state, TaskStatus.TIMED_OUT, at_ms, error="task deadline elapsed")
                return
            if not self.ledger.can_reserve(
                attempts=1,
                cost_units=attempt.cost_units,
                tool_calls=attempt.tool_calls,
            ):
                cancel_for_budget(state, at_ms, "attempt usage would exceed budget")
                return

            self.ledger.reserve(
                attempts=1,
                cost_units=attempt.cost_units,
                tool_calls=attempt.tool_calls,
            )
            state.attempts += 1
            state.cost_units += attempt.cost_units
            state.tool_calls += attempt.tool_calls
            if state.first_started_at_ms is None:
                state.first_started_at_ms = at_ms

            attempt_number = state.attempts
            common_metadata = (
                ("attempt", str(attempt_number)),
                ("cost_units", str(attempt.cost_units)),
                ("tool_calls", str(attempt.tool_calls)),
            )
            event_log.emit(
                at_ms,
                EventKind.TASK_STARTED,
                actor=state.contract.role,
                task_id=state.contract.task_id,
                metadata=common_metadata,
            )
            event_log.emit(
                at_ms,
                EventKind.TOOL_ATTEMPT,
                actor=state.contract.role,
                task_id=state.contract.task_id,
                metadata=common_metadata + attempt.metadata,
            )

            status = attempt.status
            error = attempt.error
            completes_at = at_ms + attempt.duration_ms
            if attempt.duration_ms > state.contract.timeout_ms:
                completes_at = at_ms + state.contract.timeout_ms
                status = TaskStatus.TIMED_OUT
                error = error or "attempt timed out"
            if (
                state.contract.deadline_ms is not None
                and completes_at > state.contract.deadline_ms
            ):
                completes_at = state.contract.deadline_ms
                status = TaskStatus.TIMED_OUT
                error = "task deadline elapsed"

            budget_cutoff = completes_at > self.limits.max_virtual_ms
            if budget_cutoff:
                completes_at = self.limits.max_virtual_ms
                status = TaskStatus.CANCELLED
                error = "virtual-time budget exhausted"

            completion = _Completion(
                attempt=attempt,
                status=status,
                started_at_ms=at_ms,
                error=error,
                budget_cutoff=budget_cutoff,
            )
            heapq.heappush(
                queue,
                (
                    completes_at,
                    self._COMPLETE,
                    state.contract.task_id,
                    next(serial),
                    completion,
                ),
            )

        for task in ordered_tasks:
            start_attempt(states[task.task_id], start_ms)

        end_ms = start_ms
        while queue:
            at_ms, phase, task_id, _, payload = heapq.heappop(queue)
            end_ms = at_ms
            self.ledger.observe_virtual_time(at_ms)
            state = states[task_id]
            if task_id in results:
                continue

            if phase == self._RETRY_READY:
                start_attempt(state, at_ms)
                continue

            completion = payload
            if not isinstance(completion, _Completion):
                raise TypeError("invalid internal completion event")
            attempt = completion.attempt

            if completion.budget_cutoff:
                cancel_for_budget(state, at_ms, completion.error or "budget exhausted")
                continue

            if attempt.security_denial and completion.status is not TaskStatus.TIMED_OUT:
                denial = tuple(sorted(attempt.security_denial))
                state.security_denials.append(denial)
                event_log.emit(
                    at_ms,
                    EventKind.SECURITY_DENIED,
                    actor=Role.SECURITY,
                    task_id=task_id,
                    message="untrusted instruction denied",
                    metadata=denial,
                )

            retryable = (
                attempt.retryable
                or completion.status is TaskStatus.TIMED_OUT
            )
            attempts_available = state.attempts < min(
                state.contract.retry.max_attempts,
                len(state.script),
            )
            attempt_metadata = dict(attempt.metadata)
            candidate_fingerprint = attempt_metadata.get(
                "candidate_fingerprint", ""
            )
            faulty_fingerprint = attempt_metadata.get("faulty_fingerprint", "")
            valid_worker_fault = (
                attempt_metadata.get("fault_injected") == "true"
                and bool(attempt_metadata.get("fault_id"))
                and bool(attempt_metadata.get("fault_name"))
                and attempt_metadata.get("fault_stage") == "investigation"
                and attempt_metadata.get("target_task_id") == task_id
                and attempt_metadata.get("target_attempt") == str(state.attempts)
                and attempt_metadata.get("candidate_status")
                == TaskStatus.SUCCEEDED.value
                and attempt_metadata.get("faulty_status")
                == completion.status.value
                and completion.status is TaskStatus.FAILED
                and bool(attempt_metadata.get("candidate_evidence_ids"))
                and bool(candidate_fingerprint)
                and bool(faulty_fingerprint)
                and candidate_fingerprint != faulty_fingerprint
            )
            if valid_worker_fault:
                lifecycle_keys = (
                    "candidate_evidence_ids",
                    "candidate_fingerprint",
                    "candidate_status",
                    "fault_id",
                    "fault_name",
                    "fault_role",
                    "fault_stage",
                    "faulty_fingerprint",
                    "faulty_status",
                    "target_attempt",
                    "target_task_id",
                    "study_fault_name",
                )
                lifecycle_metadata = tuple(
                    (key, attempt_metadata[key])
                    for key in lifecycle_keys
                    if key in attempt_metadata
                )
                event_log.emit(
                    at_ms,
                    EventKind.FAULT_INJECTED,
                    actor=Role.SYSTEM,
                    task_id=task_id,
                    message="the assigned worker remained unavailable on its final attempt",
                    metadata=lifecycle_metadata,
                )
                if retryable and not attempts_available:
                    event_log.emit(
                        at_ms,
                        EventKind.RETRY_EXHAUSTED,
                        actor=state.contract.role,
                        task_id=task_id,
                        message=completion.error or "bounded retry exhausted",
                        metadata=(
                            ("attempts", str(state.attempts)),
                            (
                                "fault_id",
                                attempt_metadata.get("fault_id", ""),
                            ),
                            (
                                "fault_name",
                                attempt_metadata.get("fault_name", ""),
                            ),
                            (
                                "fault_role",
                                attempt_metadata.get(
                                    "fault_role", "study_intervention"
                                ),
                            ),
                            (
                                "study_fault_name",
                                attempt_metadata.get("study_fault_name", ""),
                            ),
                            ("target_task_id", task_id),
                        ),
                    )

            if completion.status is TaskStatus.SUCCEEDED:
                for item in attempt.evidence:
                    if item.task_id != task_id:
                        raise ValueError(
                            f"evidence {item.evidence_id} belongs to {item.task_id}, not {task_id}"
                        )
                    evidence_board.commit(item)
                    state.evidence_ids.append(item.evidence_id)
                    event_log.emit(
                        at_ms,
                        EventKind.EVIDENCE_COMMITTED,
                        actor=state.contract.role,
                        task_id=task_id,
                        message=item.claim,
                        metadata=(("evidence_id", item.evidence_id),),
                    )
                finish(
                    state,
                    TaskStatus.SUCCEEDED,
                    at_ms,
                    output=attempt.output,
                )
                continue

            if retryable and attempts_available:
                retry_at = at_ms + state.contract.retry.delay_after(state.attempts)
                if retry_at > self.limits.max_virtual_ms:
                    cancel_for_budget(state, at_ms, "retry would exceed virtual-time budget")
                    continue
                if (
                    state.contract.deadline_ms is not None
                    and retry_at > state.contract.deadline_ms
                ):
                    finish(
                        state,
                        TaskStatus.TIMED_OUT,
                        at_ms,
                        error="retry would exceed task deadline",
                    )
                    continue
                event_log.emit(
                    at_ms,
                    EventKind.TASK_RETRY_SCHEDULED,
                    actor=state.contract.role,
                    task_id=task_id,
                    message=completion.error or "retryable attempt failed",
                    metadata=(
                        ("next_attempt", str(state.attempts + 1)),
                        ("retry_at_ms", str(retry_at)),
                    ),
                )
                heapq.heappush(
                    queue,
                    (
                        retry_at,
                        self._RETRY_READY,
                        task_id,
                        next(serial),
                        None,
                    ),
                )
                continue

            finish(
                state,
                completion.status,
                at_ms,
                output=attempt.output if completion.status is TaskStatus.SUCCEEDED else None,
                error=completion.error or attempt.error or completion.status.value,
            )

        if results:
            end_ms = max(result.ended_at_ms for result in results.values())
        self.ledger.observe_virtual_time(end_ms)
        return tuple(results[task.task_id] for task in ordered_tasks), end_ms

    @staticmethod
    def _validate_batch(
        tasks: tuple[TaskContract, ...],
        scripts: Mapping[str, tuple[ToolAttempt, ...]],
        start_ms: int,
        log: EventLog,
    ) -> None:
        if start_ms < 0:
            raise ValueError("start_ms cannot be negative")
        task_ids = tuple(task.task_id for task in tasks)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task IDs must be unique within a batch")
        missing = tuple(task_id for task_id in task_ids if not scripts.get(task_id))
        if missing:
            raise ValueError(f"every task needs at least one scripted attempt: {missing!r}")
        extra = tuple(sorted(set(scripts) - set(task_ids)))
        if extra:
            raise ValueError(f"scripts supplied for unknown tasks: {extra!r}")
        if log.last_at_ms is not None and start_ms < log.last_at_ms:
            raise ValueError("batch start cannot precede the existing event log")
