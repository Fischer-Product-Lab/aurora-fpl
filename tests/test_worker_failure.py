"""Acceptance tests for permanent investigator-worker failure.

The worker intervention is deliberately narrower than a generic tool outage:
the seeded investigator's normal second-attempt success becomes a terminal
worker loss.  These tests keep the dead worker, its bounded retry, the relief
assignment, and the restored evidence as separate auditable artifacts.
"""

from __future__ import annotations

from dataclasses import fields
import unittest

from aurora_lab.faults import (
    NO_FAULT,
    PERMANENT_WORKER_FAILURE,
    PLANNING_OMISSION,
    WorkerFaultApplication,
    apply_worker_fault,
)
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import EventKind, Role, TaskStatus, TraceEvent
from aurora_lab.reporting import result_json
from aurora_lab.scenario import build_scenario
from aurora_lab.simulation import (
    SPECIALISTS_WITH_CRITIC,
    SPECIALISTS_WITH_FALLBACK,
    run_preset,
)


WORKER_FAULT = PERMANENT_WORKER_FAILURE.name
CANONICAL_CASES = (
    (
        101,
        "bot_db_contention",
        "investigate_telemetry",
        ("E00", "E101"),
        75.0,
    ),
    (
        202,
        "seat_cache_stampede",
        "investigate_release",
        ("E01", "E03", "E203"),
        50.0,
    ),
    (
        303,
        "regional_gateway_degradation",
        "investigate_payments",
        ("E02", "E04", "E303"),
        25.0,
    ),
)
WORKER_LIFECYCLE_KINDS = frozenset(
    {
        EventKind.RETRY_EXHAUSTED,
        EventKind.TASK_REASSIGNED,
        EventKind.FALLBACK_COMPLETED,
        EventKind.FAULT_CONTAINED,
    }
)


def metadata(event: TraceEvent) -> dict[str, str]:
    return dict(event.metadata)


def events(
    result: object,
    kind: EventKind,
    *,
    task_id: str | None = None,
) -> tuple[TraceEvent, ...]:
    return tuple(
        event
        for event in getattr(result, "trace")
        if event.kind is kind and (task_id is None or event.task_id == task_id)
    )


def only_event(
    result: object,
    kind: EventKind,
    *,
    task_id: str | None = None,
) -> TraceEvent:
    observed = events(result, kind, task_id=task_id)
    if len(observed) != 1:
        raise AssertionError(
            f"expected one {kind.value} event for {task_id!r}; got {len(observed)}"
        )
    return observed[0]


def policy_safe(result: object, scenario: object) -> bool:
    observed = derive_run_metrics(result)
    allowed = {
        policy.action_id
        for policy in getattr(scenario, "action_policies")
        if policy.allowed
    }
    return observed.approval_violations == 0 and set(
        observed.executed_action_ids
    ).issubset(allowed)


class WorkerFaultFixtureTests(unittest.TestCase):
    """The fault changes exactly one already-bounded worker attempt."""

    def test_canonical_variants_target_the_retrying_specialist(self) -> None:
        for seed, variant, target, evidence_ids, _ in CANONICAL_CASES:
            with self.subTest(seed=seed, variant=variant):
                scenario = build_scenario(seed, variant, WORKER_FAULT)
                fixture = scenario.worker_failure_fixture

                self.assertIsNotNone(fixture)
                assert fixture is not None
                self.assertEqual(scenario.worker_fault_target_task_id, target)
                self.assertEqual(scenario.worker_fault_target_attempt, 2)
                self.assertEqual(
                    scenario.worker_fault_target_evidence_ids,
                    evidence_ids,
                )
                self.assertEqual(fixture.target_task_id, target)
                self.assertEqual(fixture.target_evidence_ids, evidence_ids)
                self.assertEqual(scenario.worker_fallback_task_id, "broad_log_scan")
                self.assertTrue(scenario.worker_fallback_evidence_ids)
                self.assertTrue(
                    set(scenario.worker_fallback_evidence_ids).isdisjoint(
                        evidence_ids
                    )
                )
                self.assertTrue(
                    all(
                        evidence_id.startswith("FB-")
                        for evidence_id in scenario.worker_fallback_evidence_ids
                    )
                )

    def test_application_is_deterministic_immutable_and_second_attempt_only(self) -> None:
        for seed, variant, target, evidence_ids, _ in CANONICAL_CASES:
            with self.subTest(seed=seed, variant=variant):
                scenario = build_scenario(seed, variant, WORKER_FAULT)
                original = scenario.script_for(target)
                first = apply_worker_fault(
                    original,
                    PERMANENT_WORKER_FAILURE,
                    seed=seed,
                    target_task_id=target,
                )
                second = apply_worker_fault(
                    original,
                    PERMANENT_WORKER_FAILURE,
                    seed=seed,
                    target_task_id=target,
                )

                self.assertIsInstance(first, WorkerFaultApplication)
                self.assertEqual(first, second)
                self.assertTrue(first.injected)
                self.assertIs(first.original_script, original)
                self.assertEqual(first.target_task_id, target)
                self.assertEqual(first.target_attempt, 2)
                self.assertEqual(first.candidate_evidence_ids, evidence_ids)
                self.assertNotEqual(
                    first.candidate_fingerprint,
                    first.faulty_fingerprint,
                )
                self.assertEqual(len(first.script), len(original))
                self.assertIs(first.script[0], original[0])

                candidate = original[1]
                failed = first.script[1]
                self.assertEqual(candidate.status, TaskStatus.SUCCEEDED)
                self.assertTrue(candidate.evidence)
                self.assertEqual(failed.status, TaskStatus.FAILED)
                self.assertIsNone(failed.output)
                self.assertIn("PERMANENT_WORKER_FAILURE", failed.error or "")
                self.assertEqual(failed.evidence, ())
                self.assertTrue(failed.retryable)
                self.assertEqual(failed.duration_ms, candidate.duration_ms)
                self.assertEqual(failed.cost_units, candidate.cost_units)
                self.assertEqual(failed.tool_calls, candidate.tool_calls)
                self.assertEqual(failed.security_denial, candidate.security_denial)

                # Applying the intervention never mutates the scenario fixture.
                self.assertIs(scenario.script_for(target), original)
                self.assertEqual(original[1], candidate)

    def test_no_fault_is_an_identity_application(self) -> None:
        scenario = build_scenario(101, "bot_db_contention", WORKER_FAULT)
        target = scenario.worker_fault_target_task_id
        assert target is not None
        original = scenario.script_for(target)

        application = apply_worker_fault(
            original,
            NO_FAULT,
            seed=scenario.seed,
            target_task_id=target,
        )

        self.assertFalse(application.injected)
        self.assertIs(application.original_script, original)
        self.assertIs(application.script, original)
        self.assertIsNone(application.fault_id)
        self.assertIsNone(application.target_task_id)
        self.assertIsNone(application.target_attempt)
        self.assertEqual(
            application.candidate_fingerprint,
            application.faulty_fingerprint,
        )


class MatchedWorkerControlTests(unittest.TestCase):
    """The identity control is distinct but carries the same dormant fixture."""

    def test_control_and_fault_share_inputs_and_have_distinct_identity(self) -> None:
        seed = 101
        variant = "bot_db_contention"
        clean = build_scenario(seed, variant)
        control = build_scenario(
            seed,
            variant,
            control_for_fault=WORKER_FAULT,
        )
        faulted = build_scenario(seed, variant, WORKER_FAULT)

        self.assertNotEqual(control, clean)
        self.assertFalse(control.fault_active)
        self.assertEqual(control.fault_arm, "control")
        self.assertTrue(faulted.fault_active)
        self.assertEqual(faulted.fault_arm, "fault")
        self.assertEqual(control.variant, faulted.variant)
        self.assertEqual(control.tasks, faulted.tasks)
        self.assertEqual(control.action_policies, faulted.action_policies)
        self.assertEqual(control.limits, faulted.limits)
        self.assertEqual(control.initial_metrics, faulted.initial_metrics)
        self.assertEqual(
            control.worker_failure_fixture,
            faulted.worker_failure_fixture,
        )

        control_result = run_preset(
            "specialists_with_critic",
            seed,
            variant,
            control_for_fault=WORKER_FAULT,
        )
        fault_result = run_preset(
            "specialists_with_critic",
            seed,
            variant,
            WORKER_FAULT,
        )
        self.assertNotEqual(control_result.run_id, fault_result.run_id)
        self.assertTrue(
            control_result.run_id.endswith(
                f"study={WORKER_FAULT}:arm=control"
            )
        )
        self.assertTrue(fault_result.run_id.endswith(f"fault={WORKER_FAULT}"))
        self.assertEqual(events(control_result, EventKind.FAULT_INJECTED), ())
        self.assertEqual(events(control_result, EventKind.RETRY_EXHAUSTED), ())
        self.assertEqual(events(control_result, EventKind.TASK_REASSIGNED), ())
        self.assertEqual(events(control_result, EventKind.FAULT_CONTAINED), ())
        self.assertTrue(derive_run_metrics(control_result).recovered)


class WorkerFailureOutcomeTests(unittest.TestCase):
    """Retry exhaustion remains visible even when a fallback later succeeds."""

    def test_no_fallback_exhausts_retry_and_degrades_safely(self) -> None:
        for seed, variant, target, target_evidence, expected_recall in CANONICAL_CASES:
            with self.subTest(seed=seed, variant=variant):
                scenario = build_scenario(seed, variant, WORKER_FAULT)
                result = run_preset(
                    "specialists_with_critic",
                    seed,
                    variant,
                    WORKER_FAULT,
                )
                observed = derive_run_metrics(result)
                target_result = next(
                    task for task in result.task_results if task.task_id == target
                )

                self.assertEqual(target_result.status, TaskStatus.FAILED)
                self.assertEqual(target_result.attempts, 2)
                self.assertEqual(target_result.evidence_ids, ())
                self.assertEqual(
                    tuple(
                        metadata(event)["attempt"]
                        for event in events(
                            result,
                            EventKind.TOOL_ATTEMPT,
                            task_id=target,
                        )
                    ),
                    ("1", "2"),
                )
                self.assertEqual(
                    len(
                        events(
                            result,
                            EventKind.TASK_RETRY_SCHEDULED,
                            task_id=target,
                        )
                    ),
                    1,
                )
                exhaustion = only_event(
                    result,
                    EventKind.RETRY_EXHAUSTED,
                    task_id=target,
                )
                self.assertEqual(metadata(exhaustion)["attempts"], "2")
                self.assertEqual(
                    metadata(exhaustion)["fault_name"],
                    WORKER_FAULT,
                )
                self.assertEqual(
                    metadata(exhaustion)["target_task_id"],
                    target,
                )
                self.assertEqual(
                    events(result, EventKind.TASK_SUCCEEDED, task_id=target),
                    (),
                )
                self.assertEqual(
                    events(result, EventKind.EVIDENCE_COMMITTED, task_id=target),
                    (),
                )
                self.assertTrue(
                    set(target_evidence).isdisjoint(
                        item.evidence_id for item in result.evidence
                    )
                )

                self.assertEqual(set(observed.diagnosis_causes), scenario.private_truth)
                self.assertEqual(
                    round(
                        100.0
                        * len(
                            set(observed.executed_action_ids)
                            & scenario.required_actions
                        )
                        / len(scenario.required_actions),
                        2,
                    ),
                    expected_recall,
                )
                self.assertFalse(observed.recovered)
                self.assertEqual(observed.verification_passes, 0)
                self.assertEqual(result.status, TaskStatus.FAILED)
                self.assertTrue(policy_safe(result, scenario))
                self.assertEqual(events(result, EventKind.TASK_REASSIGNED), ())
                self.assertEqual(events(result, EventKind.FALLBACK_COMPLETED), ())
                self.assertEqual(events(result, EventKind.FAULT_CONTAINED), ())
                communication = only_event(
                    result,
                    EventKind.COMMUNICATION_PUBLISHED,
                )
                self.assertIn("remains degraded", communication.message.lower())

    def test_fallback_recovers_with_distinct_generalist_evidence(self) -> None:
        for seed, variant, target, target_evidence, _ in CANONICAL_CASES:
            with self.subTest(seed=seed, variant=variant):
                scenario = build_scenario(seed, variant, WORKER_FAULT)
                result = run_preset(
                    "specialists_with_fallback",
                    seed,
                    variant,
                    WORKER_FAULT,
                )
                observed = derive_run_metrics(result)
                fallback_task_id = scenario.worker_fallback_task_id
                assert fallback_task_id is not None
                target_result = next(
                    task for task in result.task_results if task.task_id == target
                )
                fallback_result = next(
                    task
                    for task in result.task_results
                    if task.task_id == fallback_task_id
                )

                # Containment routes around, rather than resurrecting, the worker.
                self.assertEqual(target_result.status, TaskStatus.FAILED)
                self.assertEqual(target_result.attempts, 2)
                self.assertEqual(target_result.evidence_ids, ())
                self.assertEqual(fallback_result.status, TaskStatus.SUCCEEDED)
                self.assertEqual(fallback_result.role, Role.GENERALIST)
                self.assertEqual(fallback_result.attempts, 1)
                self.assertEqual(
                    fallback_result.evidence_ids,
                    scenario.worker_fallback_evidence_ids,
                )

                by_id = {item.evidence_id: item for item in result.evidence}
                self.assertTrue(
                    set(target_evidence).isdisjoint(by_id)
                )
                self.assertTrue(
                    set(scenario.worker_fallback_evidence_ids).issubset(by_id)
                )
                for evidence_id in scenario.worker_fallback_evidence_ids:
                    item = by_id[evidence_id]
                    item_metadata = dict(item.metadata)
                    self.assertEqual(item.task_id, fallback_task_id)
                    self.assertEqual(item.role, Role.GENERALIST)
                    self.assertEqual(item_metadata["fallback_for"], target)
                    self.assertTrue(evidence_id.startswith("FB-"))

                self.assertTrue(observed.recovered)
                self.assertEqual(observed.verification_passes, 2)
                self.assertEqual(
                    set(observed.executed_action_ids),
                    scenario.required_actions,
                )
                self.assertEqual(set(observed.diagnosis_causes), scenario.private_truth)
                self.assertEqual(result.status, TaskStatus.SUCCEEDED)
                self.assertTrue(policy_safe(result, scenario))
                self.assertEqual(
                    len(events(result, EventKind.TASK_REASSIGNED)),
                    1,
                )
                self.assertEqual(
                    len(events(result, EventKind.FALLBACK_COMPLETED)),
                    1,
                )
                self.assertEqual(
                    len(events(result, EventKind.FAULT_CONTAINED)),
                    1,
                )

    def test_reassignment_toggle_is_the_only_isolated_config_difference(self) -> None:
        differences = {
            field.name
            for field in fields(SPECIALISTS_WITH_CRITIC)
            if getattr(SPECIALISTS_WITH_CRITIC, field.name)
            != getattr(SPECIALISTS_WITH_FALLBACK, field.name)
        }

        self.assertEqual(differences, {"name", "failure_reassignment"})
        self.assertFalse(SPECIALISTS_WITH_CRITIC.failure_reassignment)
        self.assertTrue(SPECIALISTS_WITH_FALLBACK.failure_reassignment)


class WorkerFailureLifecycleTests(unittest.TestCase):
    """Containment requires one ordered and identifier-linked fallback chain."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = build_scenario(
            101,
            "bot_db_contention",
            WORKER_FAULT,
        )
        cls.result = run_preset(
            "specialists_with_fallback",
            101,
            "bot_db_contention",
            WORKER_FAULT,
        )

    def test_linked_lifecycle_is_ordered_around_terminal_worker(self) -> None:
        result = self.result
        target = self.scenario.worker_fault_target_task_id
        fallback = self.scenario.worker_fallback_task_id
        assert target is not None and fallback is not None

        injection = only_event(result, EventKind.FAULT_INJECTED, task_id=target)
        exhaustion = only_event(result, EventKind.RETRY_EXHAUSTED, task_id=target)
        failed = only_event(result, EventKind.TASK_FAILED, task_id=target)
        detected = only_event(result, EventKind.FAULT_DETECTED)
        reassigned = only_event(result, EventKind.TASK_REASSIGNED)
        fallback_started = only_event(
            result,
            EventKind.TASK_STARTED,
            task_id=fallback,
        )
        fallback_succeeded = only_event(
            result,
            EventKind.TASK_SUCCEEDED,
            task_id=fallback,
        )
        fallback_completed = only_event(result, EventKind.FALLBACK_COMPLETED)
        contained = only_event(result, EventKind.FAULT_CONTAINED)
        actions = events(result, EventKind.ACTION_EXECUTED)
        verifications = events(result, EventKind.VERIFICATION_COMPLETED)

        self.assertTrue(actions)
        self.assertEqual(len(verifications), 2)
        ordered = (
            injection,
            exhaustion,
            failed,
            detected,
            reassigned,
            fallback_started,
            fallback_succeeded,
            fallback_completed,
            *actions,
            *verifications,
            contained,
        )
        self.assertEqual(
            tuple(event.sequence for event in ordered),
            tuple(sorted(event.sequence for event in ordered)),
        )

        fault_id = metadata(injection)["fault_id"]
        self.assertTrue(fault_id)
        for event in (
            exhaustion,
            detected,
            reassigned,
            fallback_completed,
            contained,
        ):
            self.assertEqual(metadata(event).get("fault_id"), fault_id)

        linked = {
            **metadata(reassigned),
            **metadata(fallback_completed),
        }
        self.assertIn(target, linked.values())
        self.assertIn(fallback, linked.values())
        self.assertEqual(metadata(contained).get("target_task_id"), target)
        self.assertEqual(metadata(contained).get("fallback_task_id"), fallback)
        self.assertEqual(metadata(contained).get("verification_passes"), "2")


class WorkerFaultCompatibilityTests(unittest.TestCase):
    """Adding an investigation fault must not alter legacy run paths."""

    def test_default_and_explicit_none_remain_identical(self) -> None:
        default_scenario = build_scenario(101, "bot_db_contention")
        explicit_scenario = build_scenario(101, "bot_db_contention", "none")
        self.assertEqual(default_scenario, explicit_scenario)

        default = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
        )
        explicit = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            "none",
        )
        self.assertEqual(default, explicit)
        self.assertEqual(result_json(default), result_json(explicit))
        self.assertFalse(
            any(event.kind in WORKER_LIFECYCLE_KINDS for event in default.trace)
        )

    def test_planning_omission_keeps_its_distinct_lifecycle(self) -> None:
        result = run_preset(
            "specialists_with_critic",
            101,
            "bot_db_contention",
            PLANNING_OMISSION.name,
        )
        injected = only_event(result, EventKind.FAULT_INJECTED)

        self.assertEqual(
            metadata(injected)["fault_name"],
            PLANNING_OMISSION.name,
        )
        self.assertTrue(derive_run_metrics(result).fault_repaired)
        self.assertTrue(derive_run_metrics(result).recovered)
        self.assertFalse(
            any(event.kind in WORKER_LIFECYCLE_KINDS for event in result.trace)
        )


if __name__ == "__main__":
    unittest.main()
