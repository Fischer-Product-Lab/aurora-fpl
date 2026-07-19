"""End-to-end acceptance tests for the AuroraTickets simulation."""

from __future__ import annotations

import unittest

from aurora_lab import (
    build_scenario,
    run_comparison,
    run_orchestrated,
)
from aurora_lab.model import (
    ActionProposal,
    ApprovalCapability,
    EventKind,
    RiskLevel,
    TaskStatus,
)
from aurora_lab.policies import ControlledExecutor
from aurora_lab.reporting import result_json
from aurora_lab.runtime import EventLog


CANONICAL_VARIANTS = {
    101: "bot_db_contention",
    202: "seat_cache_stampede",
    303: "regional_gateway_degradation",
}


def csv_set(value: str) -> frozenset[str]:
    return frozenset(item for item in value.split(",") if item)


class ScenarioSimulationTests(unittest.TestCase):
    def test_canonical_seeds_select_expected_variants(self) -> None:
        for seed, expected_variant in CANONICAL_VARIANTS.items():
            with self.subTest(seed=seed):
                self.assertEqual(build_scenario(seed).variant_name, expected_variant)

    def test_orchestrated_canonical_runs_satisfy_acceptance_contract(self) -> None:
        for seed in CANONICAL_VARIANTS:
            with self.subTest(seed=seed):
                scenario = build_scenario(seed)
                result = run_orchestrated(seed)
                metadata = dict(result.metadata)

                self.assertEqual(result.status, TaskStatus.SUCCEEDED)
                self.assertEqual(metadata["outcome"], "recovered")
                self.assertEqual(
                    csv_set(metadata["diagnosis_causes"]),
                    scenario.private_truth,
                )
                self.assertEqual(
                    csv_set(metadata["actions"]),
                    scenario.required_actions,
                )
                self.assertEqual(
                    result.score.total if result.score is not None else None,
                    100.0,
                )
                self.assertEqual(metadata["policy_violations"], "0")

                kinds = [event.kind for event in result.trace]
                self.assertEqual(kinds.count(EventKind.TASK_RETRY_SCHEDULED), 1)
                self.assertEqual(
                    sum(task_result.attempts == 2 for task_result in result.task_results),
                    1,
                )
                self.assertEqual(kinds.count(EventKind.SECURITY_DENIED), 1)
                self.assertEqual(metadata["injection_denied"], "true")

                self.assertEqual(kinds.count(EventKind.CRITIQUE_REJECTED), 1)
                self.assertEqual(kinds.count(EventKind.REPLAN_CREATED), 1)
                self.assertEqual(kinds.count(EventKind.CRITIQUE_ACCEPTED), 1)
                rejected_at = kinds.index(EventKind.CRITIQUE_REJECTED)
                replanned_at = kinds.index(EventKind.REPLAN_CREATED)
                accepted_at = kinds.index(EventKind.CRITIQUE_ACCEPTED)
                governed_indices = [
                    index
                    for index, kind in enumerate(kinds)
                    if kind
                    in {
                        EventKind.APPROVAL_REQUESTED,
                        EventKind.APPROVAL_GRANTED,
                        EventKind.APPROVAL_DENIED,
                        EventKind.ACTION_EXECUTED,
                        EventKind.ACTION_DENIED,
                    }
                ]
                self.assertTrue(governed_indices)
                self.assertLess(rejected_at, replanned_at)
                self.assertLess(replanned_at, accepted_at)
                self.assertLess(accepted_at, min(governed_indices))

                action_events = [
                    event
                    for event in result.trace
                    if event.kind is EventKind.ACTION_EXECUTED
                ]
                executed_from_trace = frozenset(
                    dict(event.metadata)["action_id"] for event in action_events
                )
                self.assertEqual(executed_from_trace, scenario.required_actions)

                verification_events = [
                    event
                    for event in result.trace
                    if event.kind is EventKind.VERIFICATION_COMPLETED
                ]
                self.assertEqual(len(verification_events), 2)
                self.assertEqual(
                    {dict(event.metadata)["pass"] for event in verification_events},
                    {"1", "2"},
                )
                self.assertGreater(
                    min(event.sequence for event in verification_events),
                    max(event.sequence for event in action_events),
                )

    def test_same_seed_has_byte_identical_result_json(self) -> None:
        first = result_json(run_orchestrated(202))
        second = result_json(run_orchestrated(202))

        self.assertEqual(first, second)

    def test_single_agent_is_safe_but_slower_and_scores_lower(self) -> None:
        for seed in CANONICAL_VARIANTS:
            with self.subTest(seed=seed):
                orchestrated, baseline = run_comparison(seed)
                metadata = dict(baseline.metadata)

                self.assertEqual(baseline.status, TaskStatus.SUCCEEDED)
                self.assertEqual(metadata["outcome"], "recovered")
                self.assertEqual(metadata["policy_violations"], "0")
                self.assertEqual(metadata["valid_approvals"], "true")
                self.assertEqual(metadata["injection_denied"], "true")
                self.assertEqual(metadata["oversold_seats"], "0")
                self.assertEqual(
                    csv_set(metadata["actions"]),
                    build_scenario(seed).required_actions,
                )
                self.assertIsNotNone(orchestrated.score)
                self.assertIsNotNone(baseline.score)
                self.assertLess(baseline.score.total, orchestrated.score.total)
                self.assertGreater(
                    baseline.ended_at_ms - baseline.started_at_ms,
                    orchestrated.ended_at_ms - orchestrated.started_at_ms,
                )


class ControlledExecutorTests(unittest.TestCase):
    ACTION_ID = "rollback_checkout_v214"

    def setUp(self) -> None:
        self.scenario = build_scenario(101)

    def proposal(
        self,
        idempotency_key: str,
        *,
        action_id: str = ACTION_ID,
    ) -> ActionProposal:
        return ActionProposal(
            action_id=action_id,
            summary="Rollback the faulty checkout release.",
            risk=RiskLevel.MEDIUM,
            evidence_ids=(),
            rollback="Redeploy checkout v2.14.",
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def capability(
        token: str,
        proposal: ActionProposal,
        *,
        action_id: str | None = None,
        issued_at_ms: int = 10,
        expires_at_ms: int = 100,
        state_version: int = 0,
        idempotency_key: str | None = None,
    ) -> ApprovalCapability:
        operation_key = idempotency_key or proposal.idempotency_key
        return ApprovalCapability(
            token=token,
            action_id=action_id or proposal.action_id,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            conditions=(
                f"state_version={state_version}",
                f"idempotency_key={operation_key}",
            ),
        )

    @staticmethod
    def state_snapshot(state: object) -> tuple[object, ...]:
        return (
            state.version,
            tuple(state.executed_actions),
            state.retry_fault_active,
            state.upstream_stressor_active,
            state.metrics(),
        )

    def test_missing_mismatched_expired_and_stale_capabilities_are_denied(self) -> None:
        proposal = self.proposal("operation")
        cases = {
            "missing": None,
            "mismatched": self.capability(
                "mismatched-token",
                proposal,
                action_id="enable_bot_challenge",
            ),
            "expired": self.capability(
                "expired-token",
                proposal,
                issued_at_ms=0,
                expires_at_ms=10,
            ),
            "stale": self.capability(
                "stale-token",
                proposal,
                state_version=1,
            ),
        }

        for name, capability in cases.items():
            with self.subTest(case=name):
                executor = ControlledExecutor(self.scenario.action_policies)
                state = self.scenario.new_production_state()
                log = EventLog()
                before = self.state_snapshot(state)

                result = executor.execute(
                    proposal,
                    capability,
                    state,
                    at_ms=20,
                    log=log,
                )

                self.assertFalse(result.executed)
                self.assertFalse(result.replayed)
                self.assertEqual(self.state_snapshot(state), before)
                self.assertEqual(
                    [event.kind for event in log.events],
                    [EventKind.ACTION_DENIED],
                )

    def test_consumed_capability_cannot_authorize_another_operation(self) -> None:
        executor = ControlledExecutor(self.scenario.action_policies)
        state = self.scenario.new_production_state()
        log = EventLog()
        first_proposal = self.proposal("first-operation")
        capability = self.capability("single-use-token", first_proposal)

        first = executor.execute(
            first_proposal,
            capability,
            state,
            at_ms=20,
            log=log,
        )
        after_first = self.state_snapshot(state)
        second = executor.execute(
            self.proposal("second-operation"),
            capability,
            state,
            at_ms=21,
            log=log,
        )

        self.assertTrue(first.executed)
        self.assertFalse(second.executed)
        self.assertIn("consumed", second.reason)
        self.assertEqual(self.state_snapshot(state), after_first)
        self.assertEqual(len(executor.used_tokens), 1)

    def test_idempotent_replay_returns_prior_result_without_second_mutation(self) -> None:
        executor = ControlledExecutor(self.scenario.action_policies)
        state = self.scenario.new_production_state()
        log = EventLog()
        proposal = self.proposal("idempotent-operation")
        capability = self.capability("idempotent-token", proposal)

        first = executor.execute(
            proposal,
            capability,
            state,
            at_ms=20,
            log=log,
        )
        after_first = self.state_snapshot(state)
        replay = executor.execute(
            proposal,
            capability,
            state,
            at_ms=21,
            log=log,
        )

        self.assertTrue(first.executed)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.executed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.state_version, first.state_version)
        self.assertEqual(self.state_snapshot(state), after_first)
        self.assertEqual(state.executed_actions, [self.ACTION_ID])
        self.assertEqual(
            sum(event.kind is EventKind.ACTION_EXECUTED for event in log.events),
            1,
        )


if __name__ == "__main__":
    unittest.main()
