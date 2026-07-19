"""Deterministic agent behavior for synthesis, review, and replanning.

The specialist investigators are represented by scripted tool attempts in the
scenario.  This module contains the parts that reason over their shared
evidence.  None of these classes receives scenario truth or mutable production
state.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ActionProposal, EvidenceItem, RiskLevel
from .runtime import EvidenceBoard


UPSTREAM_CAUSES = frozenset(
    {
        "bot_database_contention",
        "seat_cache_stampede",
        "regional_gateway_degradation",
    }
)
CORE_CAUSES = frozenset({"retry_idempotency_regression"})
CAUSE_ORDER = (
    "bot_database_contention",
    "seat_cache_stampede",
    "regional_gateway_degradation",
    "retry_idempotency_regression",
)
ACTION_ORDER = (
    "pause_payment_retries",
    "enable_bot_challenge",
    "disable_seat_map_v3",
    "route_gateway_secondary",
    "rollback_checkout_v214",
    "reconcile_pending_payments",
)
CAUSE_ACTION = {
    "bot_database_contention": "enable_bot_challenge",
    "seat_cache_stampede": "disable_seat_map_v3",
    "regional_gateway_degradation": "route_gateway_secondary",
    "retry_idempotency_regression": "rollback_checkout_v214",
}


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """A causal claim whose supporting evidence is already committed."""

    causes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    summary: str
    confidence: float

    @property
    def is_complete(self) -> bool:
        causes = frozenset(self.causes)
        return CORE_CAUSES.issubset(causes) and bool(causes & UPSTREAM_CAUSES)


@dataclass(frozen=True, slots=True)
class ActionPlan:
    """An ordered, versioned set of proposed state transitions."""

    version: int
    diagnosis: Diagnosis
    actions: tuple[ActionProposal, ...]


@dataclass(frozen=True, slots=True)
class CritiqueReport:
    """Independent review result used to accept or reject a plan."""

    accepted: bool
    objections: tuple[str, ...]
    missing_causes: tuple[str, ...]
    missing_actions: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SelfReviewReport:
    """Structural review that deliberately does not reconstruct causal coverage."""

    accepted: bool
    objections: tuple[str, ...]
    action_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def _metadata_values(item: EvidenceItem, key: str) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item_key, raw in item.metadata:
        if item_key != key:
            continue
        for value in raw.split(","):
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                values.append(normalized)
    return tuple(values)


def _ordered(values: set[str], preferred: tuple[str, ...]) -> tuple[str, ...]:
    known = [value for value in preferred if value in values]
    unknown = sorted(values - set(known))
    return tuple(known + unknown)


class SynthesizerAgent:
    """Turns sourced board items into an initial or comprehensive diagnosis."""

    def first_pass(self, board: EvidenceBoard) -> Diagnosis:
        """Create the tempting release-only hypothesis used for teaching review."""

        selected = tuple(
            item
            for item in board.items
            if item.role.value in {"release", "payments"} and item.trusted
        )
        inferred: set[str] = set()
        for item in selected:
            inferred.update(_metadata_values(item, "causes"))
        inferred &= CORE_CAUSES
        causes = _ordered(inferred, CAUSE_ORDER)
        return Diagnosis(
            causes=causes,
            evidence_ids=tuple(item.evidence_id for item in selected),
            summary=(
                "Checkout v2.14 drops idempotency on payment retries; rolling it "
                "back should stop duplicate authorizations."
            ),
            confidence=0.72,
        )

    def comprehensive(self, board: EvidenceBoard) -> Diagnosis:
        """Reconcile every trusted finding without reading hidden truth."""

        causes: set[str] = set()
        evidence_ids: list[str] = []
        for item in board.items:
            if not item.trusted:
                continue
            item_causes = _metadata_values(item, "causes")
            if item_causes:
                causes.update(item_causes)
                evidence_ids.append(item.evidence_id)
        ordered_causes = _ordered(causes, CAUSE_ORDER)
        upstream = next(
            (cause for cause in ordered_causes if cause in UPSTREAM_CAUSES),
            "unresolved upstream stressor",
        )
        chain = " → ".join(
            (
                upstream.replace("_", " "),
                "payment timeouts",
                "retry drops idempotency key",
                "duplicate authorizations",
            )
        )
        return Diagnosis(
            causes=ordered_causes,
            evidence_ids=tuple(evidence_ids),
            summary=chain,
            confidence=0.94 if CORE_CAUSES.issubset(causes) else 0.65,
        )


class PlannerAgent:
    """Builds action plans from diagnoses and published runbook metadata."""

    def initial_plan(self, diagnosis: Diagnosis) -> ActionPlan:
        evidence = diagnosis.evidence_ids
        proposal = ActionProposal(
            action_id="rollback_checkout_v214",
            summary="Roll back checkout v2.14 to restore idempotency propagation.",
            risk=RiskLevel.MEDIUM,
            evidence_ids=evidence,
            rollback="Re-enable v2.14 after the retry fix passes replay.",
            idempotency_key="plan-v1:rollback-checkout-v214",
        )
        return ActionPlan(version=1, diagnosis=diagnosis, actions=(proposal,))

    def revised_plan(self, diagnosis: Diagnosis, board: EvidenceBoard) -> ActionPlan:
        action_ids: set[str] = {
            "pause_payment_retries",
            "rollback_checkout_v214",
            "reconcile_pending_payments",
        }
        for cause in diagnosis.causes:
            mapped = CAUSE_ACTION.get(cause)
            if mapped:
                action_ids.add(mapped)
        for item in board.items:
            if item.trusted:
                action_ids.update(_metadata_values(item, "actions"))

        evidence_ids = diagnosis.evidence_ids
        proposals: list[ActionProposal] = []
        summaries = {
            "pause_payment_retries": "Pause the unsafe retry worker before changing traffic.",
            "enable_bot_challenge": "Apply a scoped challenge to automated availability traffic.",
            "disable_seat_map_v3": "Disable seat-map-v3 to stop the cache stampede.",
            "route_gateway_secondary": "Route affected traffic to the validated secondary gateway.",
            "rollback_checkout_v214": "Roll back checkout v2.14 to restore idempotency propagation.",
            "reconcile_pending_payments": "Reconcile affected payment attempts using tokenized records.",
        }
        risks = {
            "pause_payment_retries": RiskLevel.HIGH,
            "enable_bot_challenge": RiskLevel.MEDIUM,
            "disable_seat_map_v3": RiskLevel.MEDIUM,
            "route_gateway_secondary": RiskLevel.HIGH,
            "rollback_checkout_v214": RiskLevel.MEDIUM,
            "reconcile_pending_payments": RiskLevel.HIGH,
        }
        for action_id in _ordered(action_ids, ACTION_ORDER):
            proposals.append(
                ActionProposal(
                    action_id=action_id,
                    summary=summaries.get(action_id, action_id.replace("_", " ").capitalize()),
                    risk=risks.get(action_id, RiskLevel.MEDIUM),
                    evidence_ids=evidence_ids,
                    rollback=f"Reverse {action_id.replace('_', ' ')} through the incident runbook.",
                    idempotency_key=f"plan-v2:{action_id}",
                    preconditions=("review_accepted",),
                )
            )
        return ActionPlan(version=2, diagnosis=diagnosis, actions=tuple(proposals))


class SelfReviewerAgent:
    """Validate plan structure, citations, and allow-list membership.

    This reviewer represents correlated self-review: it can catch malformed
    output but deliberately reuses the planner's frame and therefore does not
    independently reconstruct cause-to-action completeness.
    """

    def review(self, plan: ActionPlan, board: EvidenceBoard) -> SelfReviewReport:
        action_ids = tuple(action.action_id for action in plan.actions)
        trusted_ids = {
            item.evidence_id for item in board.items if item.trusted
        }
        objections: list[str] = []
        if not action_ids:
            objections.append("The plan contains no executable actions.")
        if len(set(action_ids)) != len(action_ids):
            objections.append("The plan contains duplicate action identifiers.")
        unknown = sorted(set(action_ids) - set(ACTION_ORDER))
        if unknown:
            objections.append(
                f"The plan contains actions outside the runbook: {','.join(unknown)}."
            )
        for action in plan.actions:
            if not action.evidence_ids:
                objections.append(f"{action.action_id} has no evidence citations.")
            elif not set(action.evidence_ids).issubset(trusted_ids):
                objections.append(
                    f"{action.action_id} cites evidence outside trusted shared state."
                )
            if "review_accepted" not in action.preconditions:
                objections.append(
                    f"{action.action_id} omits the configured review precondition."
                )
        return SelfReviewReport(
            accepted=not objections,
            objections=tuple(objections),
            action_ids=action_ids,
            evidence_ids=tuple(sorted(trusted_ids)),
        )


class CriticAgent:
    """Checks causal completeness and mitigation coverage independently."""

    def review(self, plan: ActionPlan, board: EvidenceBoard) -> CritiqueReport:
        diagnosed_causes = set(plan.diagnosis.causes)
        observed_causes: set[str] = set()
        trusted_items = tuple(item for item in board.items if item.trusted)
        for item in trusted_items:
            observed_causes.update(_metadata_values(item, "causes"))
        missing_causes = observed_causes - diagnosed_causes
        if not observed_causes:
            missing_causes.update(CORE_CAUSES - diagnosed_causes)
            if not diagnosed_causes.intersection(UPSTREAM_CAUSES):
                missing_causes.add("upstream_stressor")

        required_actions = {
            "pause_payment_retries",
            "rollback_checkout_v214",
            "reconcile_pending_payments",
        }
        for cause in observed_causes or diagnosed_causes:
            mapped = CAUSE_ACTION.get(cause)
            if mapped:
                required_actions.add(mapped)
        proposed = {action.action_id for action in plan.actions}
        missing_actions = required_actions - proposed

        committed = {item.evidence_id for item in trusted_items}
        dangling = tuple(sorted(set(plan.diagnosis.evidence_ids) - committed))
        objections: list[str] = []
        if missing_causes:
            objections.append("The diagnosis does not explain every independently observed symptom.")
        if missing_actions:
            objections.append("The plan leaves at least one causal link or safety precondition untreated.")
        if dangling:
            objections.append("The diagnosis cites evidence that is not committed to shared state.")

        return CritiqueReport(
            accepted=not objections,
            objections=tuple(objections),
            missing_causes=tuple(sorted(missing_causes)),
            missing_actions=_ordered(missing_actions, ACTION_ORDER),
            evidence_ids=tuple(item.evidence_id for item in trusted_items),
        )
