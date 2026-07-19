"""Approval and execution boundaries for simulated production changes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .model import (
    ActionProposal,
    ApprovalCapability,
    EventKind,
    Role,
)
from .runtime import EvidenceBoard, EventLog
from .scenario import ActionPolicy, ProductionState


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The auditable output of the simulated human approval gate."""

    action_id: str
    approved: bool
    reason: str
    capability: ApprovalCapability | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result returned by the only production-state writer."""

    action_id: str
    executed: bool
    reason: str
    state_version: int
    replayed: bool = False


class HumanApprovalAuthority:
    """Evaluate typed proposals and mint deterministic scoped capabilities."""

    def __init__(self, *, seed: int, ttl_ms: int = 90_000) -> None:
        if ttl_ms <= 0:
            raise ValueError("approval ttl must be positive")
        self.seed = seed
        self.ttl_ms = ttl_ms

    @staticmethod
    def _available_tags(
        board: EvidenceBoard,
        evidence_ids: tuple[str, ...],
    ) -> frozenset[str]:
        """Return tags from trusted evidence explicitly cited by a proposal."""

        tags: set[str] = set()
        for evidence_id in evidence_ids:
            item = board.get(evidence_id)
            if item.trusted:
                tags.update(board.tags(item))
        return frozenset(tags)

    def decide(
        self,
        proposal: ActionProposal,
        policy: ActionPolicy | None,
        board: EvidenceBoard,
        *,
        at_ms: int,
        state_version: int,
        review_accepted: bool,
        log: EventLog,
    ) -> ApprovalDecision:
        """Approve only a supported, allowed, reviewed, evidence-bound action."""

        reason: str | None = None
        committed = {item.evidence_id for item in board.items}
        if policy is None:
            reason = "action is not allow-listed"
        elif not policy.allowed:
            reason = "action is explicitly prohibited by policy"
        elif policy.action_id != proposal.action_id:
            reason = "proposal and policy scopes do not match"
        elif policy.review_acceptance_required and not review_accepted:
            reason = "the plan has not completed its configured review"
        elif not set(proposal.evidence_ids).issubset(committed):
            reason = "proposal cites evidence outside shared state"
        else:
            cited_tags = self._available_tags(board, proposal.evidence_ids)
            if not policy.required_evidence_tags.issubset(cited_tags):
                missing = sorted(policy.required_evidence_tags - cited_tags)
                reason = (
                    "required tags are missing from cited trusted evidence: "
                    f"{','.join(missing)}"
                )

        if reason is not None:
            log.emit(
                at_ms,
                EventKind.APPROVAL_DENIED,
                actor=Role.APPROVER,
                message=reason,
                metadata=(("action_id", proposal.action_id),),
            )
            return ApprovalDecision(proposal.action_id, False, reason)

        token_material = (
            f"{self.seed}:{proposal.action_id}:{proposal.idempotency_key}:"
            f"{state_version}:{at_ms}"
        ).encode("utf-8")
        token = sha256(token_material).hexdigest()[:24]
        capability = ApprovalCapability(
            token=token,
            action_id=proposal.action_id,
            issued_at_ms=at_ms,
            expires_at_ms=at_ms + self.ttl_ms,
            conditions=(
                f"state_version={state_version}",
                f"idempotency_key={proposal.idempotency_key}",
            ),
            metadata=(("approvers", ",".join(policy.approvers)),),
        )
        log.emit(
            at_ms,
            EventKind.APPROVAL_GRANTED,
            actor=Role.APPROVER,
            message="scoped, expiring, single-use capability issued",
            metadata=(
                ("action_id", proposal.action_id),
                ("approvers", ",".join(policy.approvers)),
                ("state_version", str(state_version)),
            ),
        )
        return ApprovalDecision(proposal.action_id, True, "approved", capability)


class ControlledExecutor:
    """Validate capabilities and perform all simulated state mutation."""

    UPSTREAM_ACTIONS = frozenset(
        {"enable_bot_challenge", "disable_seat_map_v3", "route_gateway_secondary"}
    )

    def __init__(self, policies: tuple[ActionPolicy, ...]) -> None:
        self._policies = {policy.action_id: policy for policy in policies}
        self._used_tokens: set[str] = set()
        self._idempotent_results: dict[str, ExecutionResult] = {}

    @property
    def used_tokens(self) -> frozenset[str]:
        return frozenset(self._used_tokens)

    def execute(
        self,
        proposal: ActionProposal,
        capability: ApprovalCapability | None,
        state: ProductionState,
        *,
        at_ms: int,
        log: EventLog,
    ) -> ExecutionResult:
        """Execute once or deny without changing ``state``."""

        existing = self._idempotent_results.get(proposal.idempotency_key)
        if existing is not None:
            return ExecutionResult(
                action_id=existing.action_id,
                executed=True,
                reason="idempotent replay returned the prior result",
                state_version=existing.state_version,
                replayed=True,
            )

        reason: str | None = None
        policy = self._policies.get(proposal.action_id)
        if policy is None or not policy.allowed:
            reason = "action is not executable under policy"
        elif capability is None:
            reason = "approval capability is missing"
        elif capability.token in self._used_tokens:
            reason = "approval capability has already been consumed"
        elif not capability.permits(proposal.action_id, at_ms):
            reason = "approval capability is mismatched or expired"
        elif f"state_version={state.version}" not in capability.conditions:
            reason = "approval capability targets a stale state version"
        elif f"idempotency_key={proposal.idempotency_key}" not in capability.conditions:
            reason = "approval capability targets a different operation"

        if reason is not None:
            log.emit(
                at_ms,
                EventKind.ACTION_DENIED,
                actor=Role.EXECUTOR,
                message=reason,
                metadata=(("action_id", proposal.action_id),),
            )
            return ExecutionResult(proposal.action_id, False, reason, state.version)

        assert capability is not None
        self._used_tokens.add(capability.token)
        self._apply(proposal.action_id, state)
        state.version += 1
        state.executed_actions.append(proposal.action_id)
        result = ExecutionResult(
            proposal.action_id,
            True,
            "executed",
            state.version,
        )
        self._idempotent_results[proposal.idempotency_key] = result
        log.emit(
            at_ms,
            EventKind.ACTION_EXECUTED,
            actor=Role.EXECUTOR,
            message=proposal.summary,
            metadata=(
                ("action_id", proposal.action_id),
                ("state_version", str(state.version)),
                ("idempotency_key", proposal.idempotency_key),
            ) + proposal.metadata,
        )
        return result

    def _apply(self, action_id: str, state: ProductionState) -> None:
        if action_id == "rollback_checkout_v214":
            state.retry_fault_active = False
        elif action_id in self.UPSTREAM_ACTIONS:
            state.upstream_stressor_active = False

        if state.retry_fault_active and state.upstream_stressor_active:
            state.checkout_success = 0.61
            state.payment_timeout_rate = 0.24
            state.duplicate_authorization_rate = 0.038
        elif not state.retry_fault_active and state.upstream_stressor_active:
            state.checkout_success = 0.74
            state.payment_timeout_rate = 0.20
            state.duplicate_authorization_rate = 0.0002
        elif state.retry_fault_active and not state.upstream_stressor_active:
            state.checkout_success = 0.92
            state.payment_timeout_rate = 0.04
            state.duplicate_authorization_rate = 0.002
            state.database_connection_utilization = min(
                state.database_connection_utilization, 0.68
            )
        else:
            state.checkout_success = 0.96
            state.payment_timeout_rate = 0.02
            state.duplicate_authorization_rate = 0.0002
            state.database_connection_utilization = min(
                state.database_connection_utilization, 0.65
            )


def state_is_recovered(state: ProductionState) -> bool:
    """Return whether the incident's measurable service objectives are met."""

    return (
        state.checkout_success >= 0.90
        and state.payment_timeout_rate < 0.05
        and state.duplicate_authorization_rate < 0.001
        and state.oversold_seats == 0
    )
