"""Layered fallback-budget fault boundaries and zero-spend guarantees."""

from __future__ import annotations

import unittest

from aurora_lab.faults import (
    FALLBACK_BUDGET_EXHAUSTION,
    NO_FAULT,
    apply_fallback_budget_fault,
)
from aurora_lab.model import EventKind, TaskStatus
from aurora_lab.runtime import (
    BudgetExceeded,
    ResourceVector,
    ScopedBudgetLedger,
)
from aurora_lab.scenario import build_scenario
from aurora_lab.simulation import run_preset


BUDGET_FAULT = FALLBACK_BUDGET_EXHAUSTION.name
CASES = (
    (101, "bot_db_contention"),
    (202, "seat_cache_stampede"),
    (303, "regional_gateway_degradation"),
)


def metadata(event: object) -> dict[str, str]:
    return dict(getattr(event, "metadata"))


def events(result: object, kind: EventKind) -> tuple[object, ...]:
    return tuple(
        event
        for event in getattr(result, "trace")
        if event.kind is kind
    )


def only_event(result: object, kind: EventKind, *, role: str | None = None) -> object:
    selected = tuple(
        event
        for event in events(result, kind)
        if role is None or metadata(event).get("fault_role") == role
    )
    if len(selected) != 1:
        raise AssertionError(f"expected one {kind.value}, found {len(selected)}")
    return selected[0]


class ScopedBudgetPrimitiveTests(unittest.TestCase):
    def test_exact_fit_is_admitted_and_one_over_is_atomic(self) -> None:
        exact = ResourceVector(
            attempts=1,
            cost_units=4,
            tool_calls=1,
            duration_ms=2_000,
        )
        admitted = ScopedBudgetLedger(exact)
        self.assertEqual(admitted.reserve(exact), exact)

        tight = ScopedBudgetLedger(
            ResourceVector(
                attempts=1,
                cost_units=3,
                tool_calls=1,
                duration_ms=2_000,
            )
        )
        before = tight.usage
        self.assertFalse(tight.can_reserve(exact))
        with self.assertRaises(BudgetExceeded):
            tight.reserve(exact)
        self.assertEqual(tight.usage, before)
        self.assertEqual(
            exact.deficits_against(tight.capacity),
            ResourceVector(cost_units=1),
        )


class FallbackBudgetFixtureTests(unittest.TestCase):
    def test_identity_control_and_fault_differ_only_on_cost_capacity(self) -> None:
        scenario = build_scenario(101, fault_name=BUDGET_FAULT)
        fixture = scenario.fallback_budget_fixture
        self.assertIsNotNone(fixture)
        assert fixture is not None

        control = apply_fallback_budget_fault(
            fixture.request,
            fixture.control_capacity,
            NO_FAULT,
            seed=101,
            target_task_id=fixture.fallback_task_id,
        )
        faulted = apply_fallback_budget_fault(
            fixture.request,
            fixture.control_capacity,
            FALLBACK_BUDGET_EXHAUSTION,
            seed=101,
            target_task_id=fixture.fallback_task_id,
        )

        self.assertFalse(control.injected)
        self.assertEqual(control.request, control.capacity)
        self.assertTrue(faulted.injected)
        self.assertEqual(faulted.binding_dimensions, ("cost_units",))
        self.assertEqual(faulted.request, control.request)
        self.assertEqual(faulted.capacity.attempts, control.capacity.attempts)
        self.assertEqual(faulted.capacity.tool_calls, control.capacity.tool_calls)
        self.assertEqual(faulted.capacity.duration_ms, control.capacity.duration_ms)
        self.assertEqual(faulted.capacity.cost_units, control.capacity.cost_units - 1)
        self.assertEqual(
            (faulted.request.attempts, faulted.request.cost_units, faulted.request.tool_calls),
            (1, 4, 1),
        )
        self.assertNotEqual(
            faulted.candidate_fingerprint,
            faulted.faulty_fingerprint,
        )

    def test_budget_study_uses_the_worker_fixture_in_both_arms(self) -> None:
        for seed, variant in CASES:
            with self.subTest(variant=variant):
                control = build_scenario(
                    seed,
                    variant,
                    control_for_fault=BUDGET_FAULT,
                )
                faulted = build_scenario(seed, variant, BUDGET_FAULT)
                self.assertEqual(
                    control.worker_failure_fixture,
                    faulted.worker_failure_fixture,
                )
                self.assertEqual(
                    control.fallback_budget_fixture,
                    faulted.fallback_budget_fixture,
                )
                self.assertEqual(control.limits, faulted.limits)
                self.assertEqual(control.limits.max_cost_units, 100)
                self.assertEqual(control.limits.max_tool_calls, 24)
                self.assertEqual(control.limits.resolution_deadline_ms, 900_000)


class LayeredFallbackBudgetRunTests(unittest.TestCase):
    def _run_pair(self, seed: int, variant: str, strategy: str):
        control = run_preset(
            strategy,
            seed=seed,
            variant_name=variant,
            control_for_fault=BUDGET_FAULT,
        )
        faulted = run_preset(
            strategy,
            seed=seed,
            variant_name=variant,
            fault_name=BUDGET_FAULT,
        )
        return control, faulted

    def test_shared_worker_is_byte_identical_before_budget_intervention(self) -> None:
        for seed, variant in CASES:
            with self.subTest(variant=variant):
                control, faulted = self._run_pair(
                    seed,
                    variant,
                    "specialists_with_fallback",
                )
                control_worker = only_event(
                    control,
                    EventKind.FAULT_INJECTED,
                    role="shared_condition",
                )
                fault_worker = only_event(
                    faulted,
                    EventKind.FAULT_INJECTED,
                    role="shared_condition",
                )
                self.assertEqual(control_worker.at_ms, fault_worker.at_ms)
                self.assertEqual(control_worker.task_id, fault_worker.task_id)
                self.assertEqual(control_worker.metadata, fault_worker.metadata)
                worker_data = metadata(control_worker)
                self.assertEqual(worker_data["fault_name"], "permanent_worker_failure")
                self.assertEqual(worker_data["fault_role"], "shared_condition")
                self.assertEqual(worker_data["study_fault_name"], BUDGET_FAULT)

                control_target = next(
                    task for task in control.task_results
                    if task.task_id == control_worker.task_id
                )
                fault_target = next(
                    task for task in faulted.task_results
                    if task.task_id == fault_worker.task_id
                )
                self.assertEqual(control_target, fault_target)
                self.assertEqual(control_target.status, TaskStatus.FAILED)
                self.assertEqual(control_target.attempts, 2)
                self.assertEqual(control_target.evidence_ids, ())

    def test_exact_fit_control_recovers_and_tight_arm_skips_without_spend(self) -> None:
        for seed, variant in CASES:
            with self.subTest(variant=variant):
                control, faulted = self._run_pair(
                    seed,
                    variant,
                    "specialists_with_fallback",
                )
                self.assertEqual(control.status, TaskStatus.SUCCEEDED)
                self.assertEqual(faulted.status, TaskStatus.FAILED)
                self.assertEqual(control.cost_units_used - faulted.cost_units_used, 4)
                self.assertEqual(control.tool_calls_used - faulted.tool_calls_used, 1)
                self.assertEqual(control.attempts_used - faulted.attempts_used, 1)

                requested = only_event(faulted, EventKind.BUDGET_RESERVATION_REQUESTED)
                denied = only_event(faulted, EventKind.BUDGET_RESERVATION_DENIED)
                skipped = only_event(faulted, EventKind.FALLBACK_SKIPPED)
                injected = only_event(
                    faulted,
                    EventKind.FAULT_INJECTED,
                    role="study_intervention",
                )
                detected = tuple(
                    event
                    for event in events(faulted, EventKind.FAULT_DETECTED)
                    if metadata(event).get("fault_role") == "study_intervention"
                )
                self.assertEqual(len(detected), 1)
                self.assertLess(injected.sequence, requested.sequence)
                self.assertLess(requested.sequence, denied.sequence)
                self.assertLess(denied.sequence, detected[0].sequence)
                self.assertLess(detected[0].sequence, skipped.sequence)

                request_data = metadata(requested)
                denial_data = metadata(denied)
                self.assertEqual(request_data["requested_attempts"], "1")
                self.assertEqual(request_data["requested_cost_units"], "4")
                self.assertEqual(request_data["requested_tool_calls"], "1")
                self.assertEqual(request_data["capacity_cost_units"], "3")
                self.assertEqual(request_data["binding_dimensions"], "cost_units")
                self.assertEqual(denial_data["deficit_cost_units"], "1")
                self.assertEqual(denial_data["scoped_usage_attempts"], "0")
                self.assertEqual(denial_data["scoped_usage_cost_units"], "0")
                self.assertEqual(denial_data["scoped_usage_tool_calls"], "0")
                for dimension in ("attempts", "cost_units", "tool_calls", "virtual_ms"):
                    self.assertEqual(
                        denial_data[f"ledger_before_{dimension}"],
                        denial_data[f"ledger_after_{dimension}"],
                    )
                self.assertEqual(metadata(skipped)["zero_spend"], "true")

                fallback_task_id = request_data["target_task_id"]
                fallback_result = next(
                    task for task in faulted.task_results
                    if task.task_id == fallback_task_id
                )
                self.assertEqual(fallback_result.status, TaskStatus.CANCELLED)
                self.assertEqual(fallback_result.attempts, 0)
                self.assertEqual(fallback_result.cost_units, 0)
                self.assertEqual(fallback_result.tool_calls, 0)
                self.assertEqual(fallback_result.evidence_ids, ())
                post_request = tuple(
                    event for event in faulted.trace
                    if event.sequence > requested.sequence
                    and event.task_id == fallback_task_id
                )
                self.assertFalse(any(
                    event.kind in {
                        EventKind.TASK_REASSIGNED,
                        EventKind.TASK_STARTED,
                        EventKind.TOOL_ATTEMPT,
                        EventKind.EVIDENCE_COMMITTED,
                        EventKind.FALLBACK_COMPLETED,
                    }
                    for event in post_request
                ))
                self.assertFalse(any(
                    item.evidence_id.startswith("FB-") for item in faulted.evidence
                ))
                self.assertEqual(events(faulted, EventKind.FAULT_CONTAINED), ())

                control_request = only_event(
                    control,
                    EventKind.BUDGET_RESERVATION_REQUESTED,
                )
                granted = only_event(
                    control,
                    EventKind.BUDGET_RESERVATION_GRANTED,
                )
                control_data = metadata(control_request)
                self.assertEqual(control_data["requested_cost_units"], "4")
                self.assertEqual(control_data["capacity_cost_units"], "4")
                self.assertEqual(control_data["binding_dimensions"], "")
                self.assertLess(control_request.sequence, granted.sequence)
                self.assertTrue(events(control, EventKind.TASK_REASSIGNED))
                self.assertTrue(events(control, EventKind.FALLBACK_COMPLETED))
                self.assertTrue(events(control, EventKind.FAULT_CONTAINED))
                self.assertEqual(
                    tuple(
                        event for event in events(control, EventKind.FAULT_INJECTED)
                        if metadata(event).get("fault_role") == "study_intervention"
                    ),
                    (),
                )

    def test_no_fallback_strategy_is_an_unaffected_negative_control(self) -> None:
        for seed, variant in CASES:
            with self.subTest(variant=variant):
                control, faulted = self._run_pair(
                    seed,
                    variant,
                    "specialists_with_critic",
                )
                self.assertEqual(control.status, TaskStatus.FAILED)
                self.assertEqual(faulted.status, TaskStatus.FAILED)
                self.assertEqual(control.task_results, faulted.task_results)
                self.assertEqual(control.evidence, faulted.evidence)
                self.assertEqual(control.attempts_used, faulted.attempts_used)
                self.assertEqual(control.cost_units_used, faulted.cost_units_used)
                self.assertEqual(control.tool_calls_used, faulted.tool_calls_used)
                for kind in (
                    EventKind.BUDGET_RESERVATION_REQUESTED,
                    EventKind.BUDGET_RESERVATION_GRANTED,
                    EventKind.BUDGET_RESERVATION_DENIED,
                    EventKind.FALLBACK_SKIPPED,
                ):
                    self.assertEqual(events(control, kind), ())
                    self.assertEqual(events(faulted, kind), ())
                intervention_events = tuple(
                    event
                    for event in events(faulted, EventKind.FAULT_INJECTED)
                    if metadata(event).get("fault_role") == "study_intervention"
                )
                self.assertEqual(intervention_events, ())


if __name__ == "__main__":
    unittest.main()
