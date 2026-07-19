"""Regression tests for fault-study measurement and review hardening."""

from __future__ import annotations

from dataclasses import replace
import inspect
import unittest

from aurora_lab import PLANNING_OMISSION, build_scenario
from aurora_lab.agents import ActionPlan, CriticAgent, Diagnosis
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import (
    ActionProposal,
    EvidenceItem,
    EventKind,
    RiskLevel,
    Role,
)
from aurora_lab.runtime import EvidenceBoard
from aurora_lab.simulation import run_preset


def _replace_event_metadata(event: object, **changes: str) -> object:
    """Return an event with selected metadata values replaced in-place."""

    metadata = tuple(
        (key, changes.get(key, value))
        for key, value in getattr(event, "metadata")
    )
    existing = {key for key, _ in metadata}
    metadata += tuple(
        (key, value) for key, value in changes.items() if key not in existing
    )
    return replace(event, metadata=metadata)


def _rewrite_verifications(result: object, **changes: str) -> object:
    return replace(
        result,
        trace=tuple(
            _replace_event_metadata(event, **changes)
            if event.kind is EventKind.VERIFICATION_COMPLETED
            else event
            for event in result.trace
        ),
    )


class VerificationIntegrityTests(unittest.TestCase):
    """Recovery credit requires finite, threshold-passing measurements."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_preset("full_orchestration", seed=101)

    def test_true_slo_boolean_cannot_override_failing_numeric_snapshot(self) -> None:
        forged = _rewrite_verifications(
            self.result,
            slo_passed="true",
            checkout_success="0.61",
            payment_timeout_rate="0.24",
            duplicate_authorization_rate="0.038",
            oversold_seats="0",
        )

        observed = derive_run_metrics(forged)

        self.assertEqual(observed.verification_passes, 0)
        self.assertIsNone(observed.first_slo_pass_ms)
        self.assertIsNone(observed.confirmed_recovery_ms)
        self.assertFalse(observed.recovered)

    def test_malformed_verification_values_do_not_crash_or_recover(self) -> None:
        cases = (
            ("checkout_success", "not-a-number"),
            ("payment_timeout_rate", "not-a-number"),
            ("duplicate_authorization_rate", "not-a-number"),
            ("oversold_seats", "not-an-integer"),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                observed = derive_run_metrics(
                    _rewrite_verifications(self.result, **{key: value})
                )
                self.assertEqual(observed.verification_passes, 0)
                self.assertIsNone(observed.confirmed_recovery_ms)
                self.assertFalse(observed.recovered)

    def test_non_finite_verification_values_do_not_crash_or_recover(self) -> None:
        cases = (
            ("checkout_success", "nan"),
            ("checkout_success", "inf"),
            ("payment_timeout_rate", "-inf"),
            ("duplicate_authorization_rate", "-inf"),
            ("oversold_seats", "nan"),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                observed = derive_run_metrics(
                    _rewrite_verifications(self.result, **{key: value})
                )
                self.assertEqual(observed.verification_passes, 0)
                self.assertIsNone(observed.confirmed_recovery_ms)
                self.assertFalse(observed.recovered)


class CancellationIntegrityTests(unittest.TestCase):
    """A cancellation earns credit only when no work or usage occurred."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_preset("full_orchestration", seed=101)
        cls.cancelled = next(
            task
            for task in cls.result.task_results
            if task.task_id == "broad_log_scan"
        )

    def test_nonzero_cancellation_work_or_usage_is_not_genuine(self) -> None:
        cases = (
            {"attempts": 1},
            {"started_at_ms": self.cancelled.ended_at_ms - 1},
            {"cost_units": 1},
            {"tool_calls": 1},
            {
                "attempts": 1,
                "started_at_ms": self.cancelled.ended_at_ms - 1,
                "cost_units": 1,
                "tool_calls": 1,
            },
        )
        for changes in cases:
            with self.subTest(changes=changes):
                malformed = replace(self.cancelled, **changes)
                result = replace(
                    self.result,
                    task_results=tuple(
                        malformed if task is self.cancelled else task
                        for task in self.result.task_results
                    ),
                )

                self.assertEqual(derive_run_metrics(result).cancelled_task_ids, ())


class CriticReconstructionTests(unittest.TestCase):
    """The critic reconstructs causal coverage from trusted shared state."""

    @staticmethod
    def _proposal(action_id: str) -> ActionProposal:
        return ActionProposal(
            action_id=action_id,
            summary=f"Execute {action_id}.",
            risk=RiskLevel.MEDIUM,
            evidence_ids=("E-upstream",),
            rollback=f"Reverse {action_id}.",
            idempotency_key=f"hardening:{action_id}",
            preconditions=("review_accepted",),
        )

    def test_missing_upstream_cause_and_action_are_reconstructed_from_board(self) -> None:
        board = EvidenceBoard(
            (
                EvidenceItem(
                    evidence_id="E-upstream",
                    claim="Bot traffic is saturating database connections.",
                    source="telemetry",
                    task_id="investigate_telemetry",
                    role=Role.TELEMETRY,
                    observed_at_ms=100,
                    trusted=True,
                    metadata=(
                        (
                            "causes",
                            "bot_database_contention,retry_idempotency_regression",
                        ),
                    ),
                ),
            )
        )
        diagnosis = Diagnosis(
            causes=("retry_idempotency_regression",),
            evidence_ids=("E-upstream",),
            summary="Retry idempotency regressed.",
            confidence=0.8,
        )
        plan = ActionPlan(
            version=2,
            diagnosis=diagnosis,
            actions=tuple(
                self._proposal(action_id)
                for action_id in (
                    "pause_payment_retries",
                    "rollback_checkout_v214",
                    "reconcile_pending_payments",
                )
            ),
        )

        report = CriticAgent().review(plan, board)

        self.assertFalse(report.accepted)
        self.assertIn("bot_database_contention", report.missing_causes)
        self.assertEqual(report.missing_actions, ("enable_bot_challenge",))
        self.assertEqual(report.evidence_ids, ("E-upstream",))


class MatchedControlIdentityTests(unittest.TestCase):
    """The internal control is distinct from both ordinary and faulted runs."""

    def test_scenario_marks_matched_control_without_activating_fault(self) -> None:
        name = PLANNING_OMISSION.name
        clean = build_scenario(101, "bot_db_contention")
        control = build_scenario(
            101,
            "bot_db_contention",
            control_for_fault=name,
        )
        faulted = build_scenario(101, "bot_db_contention", name)

        self.assertNotEqual(control, clean)
        self.assertFalse(control.fault_active)
        self.assertEqual(control.fault_name, "none")
        self.assertEqual(control.fault_study_name, name)
        self.assertEqual(control.fault_arm, "control")
        self.assertTrue(faulted.fault_active)
        self.assertEqual(faulted.fault_study_name, name)
        self.assertEqual(faulted.fault_arm, "fault")
        self.assertEqual(control.variant, faulted.variant)

    @unittest.skipUnless(
        "control_for_fault" in inspect.signature(run_preset).parameters,
        "run_preset does not yet expose the matched-control selector",
    )
    def test_run_helper_preserves_distinct_matched_control_identity(self) -> None:
        name = PLANNING_OMISSION.name
        clean = run_preset("specialists_with_critic", 101, "bot_db_contention")
        control = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            control_for_fault=name,
        )
        faulted = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            name,
        )

        self.assertNotEqual(control.run_id, clean.run_id)
        self.assertNotEqual(control.run_id, faulted.run_id)
        self.assertTrue(control.run_id.endswith(f"study={name}:arm=control"))

        control_summary = dict(control.metadata)
        faulted_summary = dict(faulted.metadata)
        self.assertEqual(control_summary["fault_study"], name)
        self.assertEqual(control_summary["fault_arm"], "control")
        self.assertNotIn("fault", control_summary)
        self.assertEqual(faulted_summary["fault_study"], name)
        self.assertEqual(faulted_summary["fault_arm"], "fault")

        control_events = {
            kind: tuple(event for event in control.trace if event.kind is kind)
            for kind in EventKind
        }
        faulted_events = {
            kind: tuple(event for event in faulted.trace if event.kind is kind)
            for kind in EventKind
        }
        control_proposal = control_events[EventKind.PLAN_PROPOSED][0]
        injection = faulted_events[EventKind.FAULT_INJECTED][0]
        faulted_proposal = faulted_events[EventKind.PLAN_PROPOSED][0]
        control_plan = dict(control_proposal.metadata)
        injected = dict(injection.metadata)
        proposed = dict(faulted_proposal.metadata)

        # The two arms share one comprehensive candidate.  The fault arm's
        # only input intervention is removal of the recorded target action.
        self.assertEqual(
            dict(control_events[EventKind.SYNTHESIS_CREATED][0].metadata),
            dict(faulted_events[EventKind.SYNTHESIS_CREATED][0].metadata),
        )
        self.assertEqual(control_plan["actions"], injected["candidate_actions"])
        self.assertEqual(
            control_plan["plan_fingerprint"],
            injected["candidate_fingerprint"],
        )
        self.assertEqual(control_plan["plan_id"], injected["plan_id"])
        self.assertEqual(proposed["actions"], injected["faulty_actions"])
        self.assertEqual(proposed["plan_fingerprint"], injected["faulty_fingerprint"])
        self.assertNotIn(
            injected["omitted_action_id"],
            proposed["actions"].split(","),
        )

        self.assertEqual(len(control_events[EventKind.CRITIQUE_ACCEPTED]), 1)
        for kind in (
            EventKind.FAULT_INJECTED,
            EventKind.CRITIQUE_REJECTED,
            EventKind.FAULT_DETECTED,
            EventKind.REPLAN_CREATED,
            EventKind.FAULT_REPAIRED,
        ):
            self.assertEqual(control_events[kind], (), kind.value)

        control_metrics = derive_run_metrics(control)
        faulted_metrics = derive_run_metrics(faulted)
        self.assertFalse(control_metrics.fault_injected)
        self.assertTrue(control_metrics.recovered)
        self.assertTrue(faulted_metrics.fault_injected)
        self.assertTrue(faulted_metrics.fault_detected)
        self.assertTrue(faulted_metrics.fault_repaired)
        self.assertTrue(faulted_metrics.recovered)


class FaultProvenanceIntegrityTests(unittest.TestCase):
    """Repair credit requires one linked plan/fault chain end to end."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            PLANNING_OMISSION.name,
        )
        injection = next(
            event
            for event in cls.result.trace
            if event.kind is EventKind.FAULT_INJECTED
        )
        cls.target = dict(injection.metadata)["omitted_action_id"]

    def _tamper_first(self, kind: EventKind, **changes: str) -> object:
        changed = False
        trace = []
        for event in self.result.trace:
            matches_target = (
                kind is not EventKind.ACTION_EXECUTED
                or dict(event.metadata).get("action_id") == self.target
            )
            if not changed and event.kind is kind and matches_target:
                event = _replace_event_metadata(event, **changes)
                changed = True
            trace.append(event)
        self.assertTrue(changed, kind.value)
        return replace(self.result, trace=tuple(trace))

    def test_mismatched_plan_or_fault_ids_break_lifecycle_credit(self) -> None:
        cases = (
            (EventKind.FAULT_DETECTED, {"plan_id": "unrelated-plan"}, False),
            (EventKind.CRITIQUE_ACCEPTED, {"plan_id": "unrelated-plan"}, True),
            (EventKind.ACTION_EXECUTED, {"fault_id": "unrelated-fault"}, True),
            (
                EventKind.VERIFICATION_COMPLETED,
                {"plan_id": "unrelated-plan"},
                True,
            ),
            (EventKind.FAULT_REPAIRED, {"plan_id": "unrelated-plan"}, True),
        )
        for kind, changes, detection_survives in cases:
            with self.subTest(kind=kind.value, changes=changes):
                observed = derive_run_metrics(self._tamper_first(kind, **changes))
                self.assertTrue(observed.fault_injected)
                self.assertEqual(observed.fault_detected, detection_survives)
                self.assertFalse(observed.fault_repaired)

    def test_verification_before_the_final_action_cannot_claim_repair(self) -> None:
        repair = next(
            event
            for event in self.result.trace
            if event.kind is EventKind.FAULT_REPAIRED
        )
        moved = False
        trace = []
        for event in self.result.trace:
            if (
                not moved
                and event.kind is EventKind.ACTION_EXECUTED
                and dict(event.metadata).get("action_id") != self.target
            ):
                event = replace(event, sequence=repair.sequence - 1)
                moved = True
            trace.append(event)
        self.assertTrue(moved)

        observed = derive_run_metrics(replace(self.result, trace=tuple(trace)))

        self.assertTrue(observed.fault_detected)
        self.assertFalse(observed.recovered)
        self.assertFalse(observed.fault_repaired)

    def test_more_than_one_plan_change_invalidates_injection_credit(self) -> None:
        injection = next(
            event
            for event in self.result.trace
            if event.kind is EventKind.FAULT_INJECTED
        )
        metadata = dict(injection.metadata)
        faulty_actions = metadata["faulty_actions"].split(",")
        self.assertGreaterEqual(len(faulty_actions), 2)
        malformed_cases = (
            faulty_actions[1:],
            faulty_actions + ["unrelated_action"],
            faulty_actions + [faulty_actions[0]],
            ["unrelated_action", *faulty_actions[1:]],
        )
        for actions in malformed_cases:
            with self.subTest(actions=actions):
                malformed_injection = _replace_event_metadata(
                    injection,
                    faulty_actions=",".join(actions),
                )
                malformed = replace(
                    self.result,
                    trace=tuple(
                        malformed_injection if event is injection else event
                        for event in self.result.trace
                    ),
                )

                observed = derive_run_metrics(malformed)

                self.assertFalse(observed.fault_injected)
                self.assertFalse(observed.fault_detected)
                self.assertFalse(observed.fault_repaired)


if __name__ == "__main__":
    unittest.main()
