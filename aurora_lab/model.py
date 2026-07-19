"""Immutable public contracts for the AuroraTickets orchestration lab.

The simulator deliberately keeps its boundary objects small, serializable, and
free of framework types.  Tuples are used for collections so that a completed
run is safe to retain as a deterministic teaching artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias


Metadata: TypeAlias = tuple[tuple[str, str], ...]


class Role(str, Enum):
    """Actors that can own work or emit trace events."""

    SYSTEM = "system"
    ORCHESTRATOR = "orchestrator"
    TELEMETRY = "telemetry"
    RELEASE = "release"
    PAYMENTS = "payments"
    SECURITY = "security"
    SYNTHESIZER = "synthesizer"
    CRITIC = "critic"
    APPROVER = "approver"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    COMMUNICATIONS = "communications"
    GENERALIST = "generalist"


class TaskStatus(str, Enum):
    """Lifecycle states shared by scripted attempts and final task results."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class EventKind(str, Enum):
    """Stable event vocabulary used by the human-readable execution trace."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    GOAL_CREATED = "goal_created"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TOOL_ATTEMPT = "tool_attempt"
    TASK_RETRY_SCHEDULED = "task_retry_scheduled"
    RETRY_EXHAUSTED = "retry_exhausted"
    TASK_SUCCEEDED = "task_succeeded"
    TASK_FAILED = "task_failed"
    TASK_TIMED_OUT = "task_timed_out"
    TASK_CANCELLED = "task_cancelled"
    EVIDENCE_COMMITTED = "evidence_committed"
    SECURITY_DENIED = "security_denied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUDGET_RESERVATION_REQUESTED = "budget_reservation_requested"
    BUDGET_RESERVATION_GRANTED = "budget_reservation_granted"
    BUDGET_RESERVATION_DENIED = "budget_reservation_denied"
    FALLBACK_SKIPPED = "fallback_skipped"
    MODEL_COMPLETED = "model_completed"
    MODEL_FAILED = "model_failed"
    SYNTHESIS_CREATED = "synthesis_created"
    PLAN_PROPOSED = "plan_proposed"
    SELF_REVIEW_COMPLETED = "self_review_completed"
    FAULT_INJECTED = "fault_injected"
    FAULT_DETECTED = "fault_detected"
    FAULT_REPAIRED = "fault_repaired"
    TASK_REASSIGNED = "task_reassigned"
    FALLBACK_COMPLETED = "fallback_completed"
    FAULT_CONTAINED = "fault_contained"
    CRITIQUE_REJECTED = "critique_rejected"
    REPLAN_CREATED = "replan_created"
    CRITIQUE_ACCEPTED = "critique_accepted"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    ACTION_EXECUTED = "action_executed"
    ACTION_DENIED = "action_denied"
    VERIFICATION_COMPLETED = "verification_completed"
    COMMUNICATION_PUBLISHED = "communication_published"
    SCORE_COMPUTED = "score_computed"


class RiskLevel(str, Enum):
    """Risk classification for proposed production actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy; ``max_attempts`` includes the first attempt."""

    max_attempts: int = 1
    backoff_ms: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if any(delay < 0 for delay in self.backoff_ms):
            raise ValueError("retry backoff values cannot be negative")

    def delay_after(self, failed_attempt: int) -> int:
        """Return the delay after a one-based failed attempt number."""

        if failed_attempt < 1:
            raise ValueError("failed_attempt must be one-based")
        if not self.backoff_ms:
            return 0
        index = min(failed_attempt - 1, len(self.backoff_ms) - 1)
        return self.backoff_ms[index]


@dataclass(frozen=True, slots=True)
class TaskContract:
    """A scheduler-facing unit of work with explicit operational bounds."""

    task_id: str
    kind: str
    role: Role
    timeout_ms: int
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    dependencies: tuple[str, ...] = ()
    priority: int = 0
    description: str = ""
    deadline_ms: int | None = None
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id cannot be empty")
        if not self.kind.strip():
            raise ValueError("task kind cannot be empty")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if self.deadline_ms is not None and self.deadline_ms <= 0:
            raise ValueError("deadline_ms must be positive when supplied")
        if self.task_id in self.dependencies:
            raise ValueError("a task cannot depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("task dependencies must be unique")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """An append-only observation with enough provenance to audit its use."""

    evidence_id: str
    claim: str
    source: str
    task_id: str
    role: Role
    observed_at_ms: int
    value: str = ""
    confidence: float = 1.0
    parent_ids: tuple[str, ...] = ()
    trusted: bool = False
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id cannot be empty")
        if not self.claim.strip():
            raise ValueError("evidence claim cannot be empty")
        if not self.source.strip():
            raise ValueError("evidence source cannot be empty")
        if not self.task_id.strip():
            raise ValueError("evidence task_id cannot be empty")
        if self.observed_at_ms < 0:
            raise ValueError("observed_at_ms cannot be negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("evidence parent_ids must be unique")


@dataclass(frozen=True, slots=True)
class ToolAttempt:
    """A deterministic scripted outcome for one task attempt.

    A duration longer than the owning task's timeout is treated as a timeout,
    regardless of ``status``.  ``security_denial`` is emitted into the trace
    when the response is observed, keeping untrusted instructions visible but
    inert.
    """

    duration_ms: int
    status: TaskStatus
    output: str | None = None
    error: str | None = None
    evidence: tuple[EvidenceItem, ...] = ()
    retryable: bool = False
    cost_units: int = 1
    tool_calls: int = 1
    security_denial: Metadata = ()
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")
        if self.status not in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
        }:
            raise ValueError("a scripted attempt must have a terminal status")
        if self.cost_units < 0 or self.tool_calls < 0:
            raise ValueError("attempt usage cannot be negative")


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Final, immutable outcome of a scheduled task."""

    task_id: str
    role: Role
    status: TaskStatus
    attempts: int
    started_at_ms: int | None
    ended_at_ms: int
    output: str | None = None
    error: str | None = None
    evidence_ids: tuple[str, ...] = ()
    cost_units: int = 0
    tool_calls: int = 0
    security_denials: tuple[Metadata, ...] = ()

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts cannot be negative")
        if self.started_at_ms is not None and self.started_at_ms < 0:
            raise ValueError("started_at_ms cannot be negative")
        if self.ended_at_ms < 0:
            raise ValueError("ended_at_ms cannot be negative")
        if self.started_at_ms is not None and self.ended_at_ms < self.started_at_ms:
            raise ValueError("task cannot end before it starts")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """A reviewable request to mutate simulated production state."""

    action_id: str
    summary: str
    risk: RiskLevel
    evidence_ids: tuple[str, ...]
    rollback: str
    idempotency_key: str
    requires_approval: bool = True
    preconditions: tuple[str, ...] = ()
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("action_id", self.action_id),
            ("summary", self.summary),
            ("rollback", self.rollback),
            ("idempotency_key", self.idempotency_key),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")


@dataclass(frozen=True, slots=True)
class ApprovalCapability:
    """A scoped, expiring capability minted by the human approval gate."""

    token: str
    action_id: str
    issued_at_ms: int
    expires_at_ms: int
    conditions: tuple[str, ...] = ()
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if not self.token.strip() or not self.action_id.strip():
            raise ValueError("approval token and action_id cannot be empty")
        if self.issued_at_ms < 0 or self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("approval capability must have a positive lifetime")

    def permits(self, action_id: str, at_ms: int) -> bool:
        """Return whether this capability is scoped and live at ``at_ms``."""

        return self.action_id == action_id and self.issued_at_ms <= at_ms < self.expires_at_ms


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One event in a contiguous, virtual-time-ordered execution trace."""

    sequence: int
    at_ms: int
    kind: EventKind
    actor: Role = Role.SYSTEM
    task_id: str | None = None
    message: str = ""
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.at_ms < 0:
            raise ValueError("trace sequence and timestamp cannot be negative")


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Transparent score components; the maximum intended total is 100."""

    root_cause: float = 0.0
    recovery: float = 0.0
    safety: float = 0.0
    evidence: float = 0.0
    resilience: float = 0.0
    efficiency: float = 0.0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.components):
            raise ValueError("score components cannot be negative")

    @property
    def components(self) -> tuple[float, ...]:
        return (
            self.root_cause,
            self.recovery,
            self.safety,
            self.evidence,
            self.resilience,
            self.efficiency,
        )

    @property
    def total(self) -> float:
        """Return the rounded sum of all score components."""

        return round(sum(self.components), 2)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Complete immutable artifact returned by a simulation strategy."""

    run_id: str
    strategy: str
    status: TaskStatus
    started_at_ms: int
    ended_at_ms: int
    task_results: tuple[TaskResult, ...]
    evidence: tuple[EvidenceItem, ...]
    trace: tuple[TraceEvent, ...]
    attempts_used: int
    cost_units_used: int
    tool_calls_used: int
    score: ScoreBreakdown | None = None
    metadata: Metadata = ()

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.strategy.strip():
            raise ValueError("run_id and strategy cannot be empty")
        if self.started_at_ms < 0 or self.ended_at_ms < self.started_at_ms:
            raise ValueError("invalid simulation time range")
        if min(self.attempts_used, self.cost_units_used, self.tool_calls_used) < 0:
            raise ValueError("simulation usage cannot be negative")
