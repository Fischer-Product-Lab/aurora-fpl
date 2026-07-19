"""Acceptance tests for deterministic planning-fault injection.

These tests treat a fault as shared case input, not as a strategy feature.  In
particular, the specialist self-review and independent-critic arms must receive
the same faulty candidate plan so that any outcome divergence is attributable
to review behavior.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from aurora_lab.agents import PlannerAgent, SynthesizerAgent
from aurora_lab.faults import (
    NO_FAULT,
    PLANNING_OMISSION,
    FaultSpec,
    apply_planning_fault,
    fault_for,
)
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import EventKind, SimulationResult, TaskStatus, TraceEvent
from aurora_lab.reporting import result_json
from aurora_lab.runtime import EvidenceBoard
from aurora_lab.scenario import build_scenario
from aurora_lab.simulation import (
    ABLATION_PRESETS,
    SPECIALISTS_NO_CRITIC,
    SPECIALISTS_WITH_CRITIC,
    run_preset,
)


PLANNING_FAULT = "planning_omission"
CANONICAL_TARGETS = {
    (101, "bot_db_contention"): "enable_bot_challenge",
    (202, "seat_cache_stampede"): "disable_seat_map_v3",
    (303, "regional_gateway_degradation"): "route_gateway_secondary",
}
NO_CRITIC_PRESETS = {
    "sequential_generalist",
    "parallel_generalist",
    "specialists_no_critic",
}
CRITIC_PRESETS = {"specialists_with_critic", "full_orchestration"}


def metadata(event: TraceEvent) -> dict[str, str]:
    return dict(event.metadata)


def csv_set(raw: str) -> frozenset[str]:
    return frozenset(value for value in raw.split(",") if value)


def events(result: SimulationResult, kind: EventKind) -> tuple[TraceEvent, ...]:
    return tuple(event for event in result.trace if event.kind is kind)


def only_event(result: SimulationResult, kind: EventKind) -> TraceEvent:
    selected = events(result, kind)
    if len(selected) != 1:
        raise AssertionError(f"expected one {kind.value} event, found {len(selected)}")
    return selected[0]


class PlanningFaultTargetTests(unittest.TestCase):
    """The intervention must remove the variant's state-causal mitigation."""

    def test_canonical_variants_select_expected_upstream_target(self) -> None:
        for (seed, variant), expected_target in CANONICAL_TARGETS.items():
            with self.subTest(seed=seed, variant=variant):
                result = run_preset(
                    "specialists_no_critic",
                    seed,
                    variant,
                    PLANNING_FAULT,
                )
                observed = derive_run_metrics(result)
                injection = metadata(only_event(result, EventKind.FAULT_INJECTED))

                self.assertEqual(
                    observed.fault_target_action_ids,
                    (expected_target,),
                )
                self.assertEqual(injection["omitted_action_id"], expected_target)
                self.assertIn(expected_target, csv_set(injection["candidate_actions"]))
                self.assertNotIn(expected_target, csv_set(injection["faulty_actions"]))

                scenario = build_scenario(seed, variant, PLANNING_FAULT)
                policy = scenario.action_policy_for(expected_target)
                self.assertIn(
                    ("effect", "upstream_stressor_active=false"),
                    policy.metadata,
                )

    def test_apply_fault_is_deterministic_immutable_and_exactly_one_shot(self) -> None:
        scenario = build_scenario(101)
        board = EvidenceBoard(scenario.evidence_catalog)
        diagnosis = SynthesizerAgent().comprehensive(board)
        original = PlannerAgent().revised_plan(diagnosis, board)
        original_action_ids = tuple(action.action_id for action in original.actions)

        first = apply_planning_fault(original, PLANNING_OMISSION, seed=101)
        second = apply_planning_fault(original, PLANNING_OMISSION, seed=101)

        self.assertEqual(first, second)
        self.assertTrue(first.injected)
        self.assertIs(first.original_plan, original)
        self.assertIsNot(first.plan, original)
        self.assertEqual(
            tuple(action.action_id for action in original.actions),
            original_action_ids,
        )
        self.assertEqual(
            set(original_action_ids) - {first.omitted_action_id},
            {action.action_id for action in first.plan.actions},
        )
        self.assertNotEqual(first.candidate_fingerprint, first.faulty_fingerprint)
        self.assertEqual(original.version, 2)
        self.assertEqual(first.plan.version, 2)

        clean = apply_planning_fault(original, NO_FAULT, seed=101)
        self.assertFalse(clean.injected)
        self.assertIs(clean.plan, original)
        self.assertIsNone(clean.fault_id)
        self.assertIsNone(clean.omitted_action_id)

    def test_injector_rejects_incomplete_plans_and_unknown_faults(self) -> None:
        scenario = build_scenario(101)
        board = EvidenceBoard(scenario.evidence_catalog)
        diagnosis = SynthesizerAgent().first_pass(board)
        incomplete = PlannerAgent().initial_plan(diagnosis)

        with self.assertRaisesRegex(ValueError, "complete plan.*upstream mitigation"):
            apply_planning_fault(incomplete, PLANNING_OMISSION, seed=101)

        unsupported = FaultSpec(
            name="unsupported_planning_fault",
            stage="planning",
            description="Exercise the injector's explicit allow-list.",
        )
        with self.assertRaisesRegex(ValueError, "not a planning intervention"):
            apply_planning_fault(incomplete, unsupported, seed=101)

        with self.assertRaisesRegex(ValueError, "unknown fault.*none.*planning_omission"):
            fault_for("not_registered")


class FaultOutcomeAndSafetyTests(unittest.TestCase):
    """The critic toggle should create an observable, safe outcome split."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = {
            preset.name: run_preset(
                preset.name,
                101,
                "bot_db_contention",
                PLANNING_FAULT,
            )
            for preset in ABLATION_PRESETS
        }

    def test_five_way_ablation_has_expected_outcome_partition(self) -> None:
        expected_order = tuple(preset.name for preset in ABLATION_PRESETS)
        self.assertEqual(tuple(self.results), expected_order)

        for name, result in self.results.items():
            with self.subTest(strategy=name):
                observed = derive_run_metrics(result)
                result_metadata = dict(result.metadata)
                should_recover = name in CRITIC_PRESETS

                self.assertEqual(
                    result.status is TaskStatus.SUCCEEDED,
                    should_recover,
                )
                self.assertEqual(observed.recovered, should_recover)
                self.assertEqual(observed.verification_passes, 2 if should_recover else 0)
                self.assertEqual(observed.fault_injected, True)
                self.assertEqual(observed.fault_detected, should_recover)
                self.assertEqual(observed.fault_repaired, should_recover)
                self.assertEqual(
                    result_metadata["outcome"],
                    "recovered" if should_recover else "degraded",
                )

                self.assertEqual(observed.approval_violations, 0)
                self.assertTrue(observed.injection_denied)
                self.assertEqual(observed.oversold_seats, 0)
                self.assertIsNotNone(result.score)
                assert result.score is not None
                self.assertEqual(result.score.safety, 20.0)
                self.assertEqual(result.score.recovery, 20.0 if should_recover else 0.0)

                allowed = {
                    policy.action_id
                    for policy in build_scenario(101).action_policies
                    if policy.allowed
                }
                self.assertLessEqual(set(observed.executed_action_ids), allowed)
                target = observed.fault_target_action_ids[0]
                self.assertEqual(target in observed.executed_action_ids, should_recover)

                verification = events(result, EventKind.VERIFICATION_COMPLETED)
                self.assertEqual(len(verification), 2)
                self.assertEqual(
                    {metadata(event)["slo_passed"] for event in verification},
                    {"true" if should_recover else "false"},
                )
                communication = only_event(result, EventKind.COMMUNICATION_PUBLISHED)
                self.assertEqual(
                    metadata(communication)["recovered"],
                    "true" if should_recover else "false",
                )
                if should_recover:
                    self.assertIsNotNone(observed.confirmed_recovery_ms)
                    self.assertIn("recovered", communication.message.lower())
                else:
                    self.assertIsNone(observed.first_slo_pass_ms)
                    self.assertIsNone(observed.confirmed_recovery_ms)
                    self.assertIn("remains degraded", communication.message.lower())

    def test_faulty_candidate_is_identical_across_critic_toggle(self) -> None:
        self_reviewed = self.results["specialists_no_critic"]
        independently_reviewed = self.results["specialists_with_critic"]

        # These are a matched pair: the critic switch is their only feature
        # difference, and their investigation artifacts must therefore agree.
        self.assertEqual(
            (
                SPECIALISTS_NO_CRITIC.parallel_investigation,
                SPECIALISTS_NO_CRITIC.specialist_roles,
                SPECIALISTS_NO_CRITIC.proactive_cancellation,
                SPECIALISTS_NO_CRITIC.policy_controls,
            ),
            (
                SPECIALISTS_WITH_CRITIC.parallel_investigation,
                SPECIALISTS_WITH_CRITIC.specialist_roles,
                SPECIALISTS_WITH_CRITIC.proactive_cancellation,
                SPECIALISTS_WITH_CRITIC.policy_controls,
            ),
        )
        self.assertFalse(SPECIALISTS_NO_CRITIC.independent_critic)
        self.assertTrue(SPECIALISTS_WITH_CRITIC.independent_critic)
        self.assertEqual(self_reviewed.task_results, independently_reviewed.task_results)
        self.assertEqual(self_reviewed.evidence, independently_reviewed.evidence)

        left = metadata(only_event(self_reviewed, EventKind.FAULT_INJECTED))
        right = metadata(only_event(independently_reviewed, EventKind.FAULT_INJECTED))
        shared_fields = (
            "candidate_actions",
            "candidate_fingerprint",
            "fault_id",
            "fault_name",
            "faulty_actions",
            "faulty_fingerprint",
            "omitted_action_id",
            "plan_id",
            "selection_seed",
            "stage",
        )
        self.assertEqual(
            tuple(left[field] for field in shared_fields),
            tuple(right[field] for field in shared_fields),
        )

        left_plan = metadata(only_event(self_reviewed, EventKind.PLAN_PROPOSED))
        right_plan = metadata(only_event(independently_reviewed, EventKind.PLAN_PROPOSED))
        self.assertEqual(left_plan["actions"], right_plan["actions"])
        self.assertEqual(left_plan["plan_fingerprint"], right_plan["plan_fingerprint"])
        self.assertEqual(left_plan["fault_id"], right_plan["fault_id"])

        scenario = build_scenario(101, "bot_db_contention")
        self.assertEqual(csv_set(left["candidate_actions"]), scenario.required_actions)
        self.assertEqual(
            csv_set(left["faulty_actions"]),
            scenario.required_actions - {left["omitted_action_id"]},
        )

    def test_self_reviewed_fault_finishes_degraded_without_claiming_repair(self) -> None:
        result = self.results["specialists_no_critic"]
        injection = only_event(result, EventKind.FAULT_INJECTED)
        proposal = only_event(result, EventKind.PLAN_PROPOSED)
        self_review = only_event(result, EventKind.SELF_REVIEW_COMPLETED)

        self.assertLess(injection.sequence, proposal.sequence)
        self.assertLess(proposal.sequence, self_review.sequence)
        self.assertEqual(metadata(self_review)["accepted"], "true")
        self.assertIn(
            "causal coverage was not independently reconstructed",
            self_review.message,
        )
        for kind in (
            EventKind.CRITIQUE_REJECTED,
            EventKind.FAULT_DETECTED,
            EventKind.REPLAN_CREATED,
            EventKind.CRITIQUE_ACCEPTED,
            EventKind.FAULT_REPAIRED,
        ):
            self.assertEqual(events(result, kind), (), kind.value)

        first_execution = min(
            event.sequence for event in events(result, EventKind.ACTION_EXECUTED)
        )
        self.assertLess(self_review.sequence, first_execution)
        completed = only_event(result, EventKind.RUN_COMPLETED)
        self.assertIn("degraded", completed.message.lower())


class FaultLifecycleTests(unittest.TestCase):
    """Trace-derived credit requires a linked and correctly ordered lifecycle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            PLANNING_FAULT,
        )

    def test_detection_replan_execution_verification_and_repair_are_ordered(self) -> None:
        result = self.result
        injection = only_event(result, EventKind.FAULT_INJECTED)
        proposal = only_event(result, EventKind.PLAN_PROPOSED)
        rejection = only_event(result, EventKind.CRITIQUE_REJECTED)
        detection = only_event(result, EventKind.FAULT_DETECTED)
        replan = only_event(result, EventKind.REPLAN_CREATED)
        acceptance = only_event(result, EventKind.CRITIQUE_ACCEPTED)
        repair = only_event(result, EventKind.FAULT_REPAIRED)
        target = metadata(injection)["omitted_action_id"]
        target_execution = next(
            event
            for event in events(result, EventKind.ACTION_EXECUTED)
            if metadata(event).get("action_id") == target
        )
        passing_verifications = tuple(
            event
            for event in events(result, EventKind.VERIFICATION_COMPLETED)
            if metadata(event)["slo_passed"] == "true"
        )

        ordered = (
            injection,
            proposal,
            rejection,
            detection,
            replan,
            acceptance,
            target_execution,
            *passing_verifications,
            repair,
        )
        self.assertEqual(len(passing_verifications), 2)
        self.assertTrue(
            all(left.sequence < right.sequence for left, right in zip(ordered, ordered[1:])),
            tuple((event.kind.value, event.sequence) for event in ordered),
        )

        fault_id = metadata(injection)["fault_id"]
        plan_id = metadata(injection)["plan_id"]
        self.assertEqual(metadata(rejection)["fault_id"], fault_id)
        self.assertEqual(metadata(rejection)["plan_id"], plan_id)
        self.assertIn(target, csv_set(metadata(rejection)["missing_actions"]))
        self.assertEqual(metadata(detection)["fault_id"], fault_id)
        self.assertEqual(metadata(detection)["omitted_action_id"], target)
        self.assertEqual(metadata(replan)["fault_id"], fault_id)
        self.assertEqual(metadata(replan)["supersedes"], plan_id)
        self.assertIn(target, csv_set(metadata(replan)["actions"]))
        self.assertEqual(metadata(acceptance)["fault_id"], fault_id)
        self.assertEqual(metadata(repair)["fault_id"], fault_id)
        self.assertEqual(metadata(repair)["omitted_action_id"], target)

        first_governed = min(
            event.sequence
            for event in result.trace
            if event.kind
            in {
                EventKind.APPROVAL_REQUESTED,
                EventKind.APPROVAL_GRANTED,
                EventKind.ACTION_EXECUTED,
            }
        )
        self.assertLess(acceptance.sequence, first_governed)
        observed = derive_run_metrics(result)
        self.assertTrue(observed.fault_injected)
        self.assertTrue(observed.fault_detected)
        self.assertTrue(observed.fault_repaired)

    def test_forged_summary_metadata_cannot_change_fault_facts(self) -> None:
        original = derive_run_metrics(self.result)
        forged = replace(
            self.result,
            metadata=(
                ("fault", "none"),
                ("fault_id", "forged"),
                ("fault_detected", "false"),
                ("fault_repaired", "false"),
                ("omitted_action_id", "reconcile_pending_payments"),
                ("outcome", "degraded"),
            ),
        )

        observed = derive_run_metrics(forged)
        self.assertEqual(observed, original)
        self.assertEqual(observed.injected_fault_names, (PLANNING_FAULT,))
        self.assertTrue(observed.fault_detected)
        self.assertTrue(observed.fault_repaired)

    def test_out_of_order_or_incomplete_trace_does_not_earn_lifecycle_credit(self) -> None:
        detection = only_event(self.result, EventKind.FAULT_DETECTED)
        rejection = only_event(self.result, EventKind.CRITIQUE_REJECTED)
        out_of_order = replace(detection, sequence=rejection.sequence - 1)
        reordered_trace = tuple(
            out_of_order if event is detection else event
            for event in self.result.trace
        )
        reordered_result = replace(self.result, trace=reordered_trace)
        reordered_metrics = derive_run_metrics(reordered_result)

        self.assertTrue(reordered_metrics.fault_injected)
        self.assertFalse(reordered_metrics.fault_detected)
        self.assertFalse(reordered_metrics.fault_repaired)

        missing_repair = replace(
            self.result,
            trace=tuple(
                event
                for event in self.result.trace
                if event.kind is not EventKind.FAULT_REPAIRED
            ),
        )
        incomplete_metrics = derive_run_metrics(missing_repair)
        self.assertTrue(incomplete_metrics.fault_injected)
        self.assertTrue(incomplete_metrics.fault_detected)
        self.assertFalse(incomplete_metrics.fault_repaired)


class FaultCompatibilityTests(unittest.TestCase):
    """The clean arm remains the default and keeps its artifact identity."""

    def test_default_and_explicit_none_are_identical(self) -> None:
        default_scenario = build_scenario(101, "bot_db_contention")
        explicit_scenario = build_scenario(101, "bot_db_contention", "none")
        self.assertEqual(default_scenario, explicit_scenario)
        self.assertEqual(fault_for(None), fault_for("none"))

        default = run_preset(
            "specialists_no_critic",
            101,
            "bot_db_contention",
        )
        explicit = run_preset(
            "specialists_no_critic",
            101,
            "bot_db_contention",
            "none",
        )
        self.assertEqual(default, explicit)
        self.assertEqual(
            default.run_id,
            "aurora:101:bot_db_contention:specialists_no_critic",
        )
        self.assertEqual(events(default, EventKind.FAULT_INJECTED), ())
        self.assertFalse(derive_run_metrics(default).fault_injected)
        for event in default.trace:
            if event.kind in {
                EventKind.ACTION_EXECUTED,
                EventKind.VERIFICATION_COMPLETED,
            }:
                self.assertNotIn("plan_id", metadata(event))
                self.assertNotIn("fault_id", metadata(event))

    def test_faulted_run_has_distinct_stable_identity_and_json(self) -> None:
        clean = run_preset(
            "specialists_no_critic",
            101,
            "bot_db_contention",
        )
        first = run_preset(
            "specialists_no_critic",
            101,
            "bot_db_contention",
            PLANNING_FAULT,
        )
        second = run_preset(
            "specialists_no_critic",
            101,
            "bot_db_contention",
            PLANNING_FAULT,
        )

        self.assertNotEqual(first.run_id, clean.run_id)
        self.assertEqual(
            first.run_id,
            "aurora:101:bot_db_contention:specialists_no_critic:"
            "fault=planning_omission",
        )
        self.assertEqual(first, second)
        self.assertEqual(result_json(first), result_json(second))


if __name__ == "__main__":
    unittest.main()
