"""Adversarial provenance tests for permanent worker-failure credit."""

from __future__ import annotations

from dataclasses import replace
import unittest

from aurora_lab.faults import PERMANENT_WORKER_FAILURE, PLANNING_OMISSION
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import (
    EventKind,
    RetryPolicy,
    Role,
    TaskContract,
    TaskStatus,
    ToolAttempt,
    TraceEvent,
)
from aurora_lab.runtime import (
    BudgetLedger,
    BudgetLimits,
    EvidenceBoard,
    EventLog,
    VirtualScheduler,
)
from aurora_lab.scenario import build_scenario
from aurora_lab.simulation import run_preset


WORKER_FAULT = PERMANENT_WORKER_FAILURE.name


def _metadata(record: object, **changes: str) -> object:
    values = dict(getattr(record, "metadata"))
    values.update(changes)
    return replace(record, metadata=tuple(sorted(values.items())))


def _rewrite_first_event(
    result: object,
    kind: EventKind,
    *,
    predicate: object | None = None,
    **changes: str,
) -> object:
    matches = predicate if callable(predicate) else lambda event: True
    changed = False
    trace = []
    for event in getattr(result, "trace"):
        if not changed and event.kind is kind and matches(event):
            event = _metadata(event, **changes)
            changed = True
        trace.append(event)
    if not changed:
        raise AssertionError(f"no {kind.value} event matched")
    return replace(result, trace=tuple(trace))


def _renumber(trace: list[TraceEvent]) -> tuple[TraceEvent, ...]:
    return tuple(replace(event, sequence=index) for index, event in enumerate(trace))


class WorkerTraceCreditHardeningTests(unittest.TestCase):
    """No lifecycle stage may borrow an unrelated event or identity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = build_scenario(101, "bot_db_contention", WORKER_FAULT)
        cls.result = run_preset(
            "specialists_with_fallback",
            101,
            "bot_db_contention",
            WORKER_FAULT,
        )
        cls.target = cls.scenario.worker_fault_target_task_id
        cls.fallback = cls.scenario.worker_fallback_task_id
        assert cls.target is not None and cls.fallback is not None

    def test_baseline_earns_the_complete_containment_chain(self) -> None:
        observed = derive_run_metrics(self.result)

        self.assertTrue(observed.fault_injected)
        self.assertTrue(observed.target_terminal_failure)
        self.assertTrue(observed.fault_detected)
        self.assertTrue(observed.reassignment_attempted)
        self.assertTrue(observed.fallback_succeeded)
        self.assertTrue(observed.fault_contained)

    def test_injection_requires_the_attempt_envelope_to_match(self) -> None:
        forged = _rewrite_first_event(
            self.result,
            EventKind.TOOL_ATTEMPT,
            predicate=lambda event: dict(event.metadata).get("fault_injected")
            == "true",
            fault_injected="false",
            candidate_fingerprint="unrelated-candidate",
            candidate_status="failed",
            faulty_fingerprint="unrelated-fault",
            faulty_status="succeeded",
            target_task_id="unrelated-task",
        )

        observed = derive_run_metrics(forged)

        self.assertFalse(observed.fault_injected)
        self.assertFalse(observed.target_terminal_failure)
        self.assertFalse(observed.reassignment_attempted)
        self.assertFalse(observed.fallback_succeeded)
        self.assertFalse(observed.fault_contained)

    def test_failed_target_cannot_have_any_committed_evidence(self) -> None:
        candidate = self.scenario.script_for(self.target)[1].evidence[0]
        forged_item = replace(
            candidate,
            evidence_id="FORGED-TARGET-EVIDENCE",
            claim="an impossible observation from the terminal worker",
        )
        trace = list(self.result.trace)
        injection_index = next(
            index
            for index, event in enumerate(trace)
            if event.kind is EventKind.FAULT_INJECTED
            and event.task_id == self.target
        )
        injection = trace[injection_index]
        trace.insert(
            injection_index,
            TraceEvent(
                sequence=0,
                at_ms=injection.at_ms,
                kind=EventKind.EVIDENCE_COMMITTED,
                actor=forged_item.role,
                task_id=self.target,
                message=forged_item.claim,
                metadata=(("evidence_id", forged_item.evidence_id),),
            ),
        )
        forged = replace(
            self.result,
            evidence=self.result.evidence + (forged_item,),
            trace=_renumber(trace),
        )

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.fault_injected)
        self.assertFalse(observed.target_terminal_failure)
        self.assertFalse(observed.fault_detected)
        self.assertFalse(observed.reassignment_attempted)

    def test_reassignment_must_continue_the_detected_worker_identity(self) -> None:
        forged = _rewrite_first_event(
            self.result,
            EventKind.FAULT_DETECTED,
            original_assignment_id="assignment-from-another-run",
            original_worker_id="worker-from-another-run",
        )

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.target_terminal_failure)
        self.assertFalse(observed.reassignment_attempted)
        self.assertFalse(observed.fallback_succeeded)
        self.assertFalse(observed.fault_contained)

    def test_fallback_tool_attempt_must_continue_the_assignment_envelope(self) -> None:
        forged = _rewrite_first_event(
            self.result,
            EventKind.TOOL_ATTEMPT,
            predicate=lambda event: event.task_id == self.fallback,
            fault_name="unrelated-fault",
            original_assignment_id="unrelated-assignment",
            original_worker_id="unrelated-worker",
        )

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.reassignment_attempted)
        self.assertFalse(observed.fallback_succeeded)
        self.assertFalse(observed.fault_contained)

    def test_fallback_evidence_requires_matching_commit_events(self) -> None:
        trace = [
            event
            for event in self.result.trace
            if not (
                event.kind is EventKind.EVIDENCE_COMMITTED
                and event.task_id == self.fallback
            )
        ]
        forged = replace(self.result, trace=_renumber(trace))

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.reassignment_attempted)
        self.assertFalse(observed.fallback_succeeded)
        self.assertFalse(observed.fault_contained)

    def test_restored_evidence_must_point_to_the_lost_source_task(self) -> None:
        candidate_ids = set(self.scenario.worker_fault_target_evidence_ids)
        changed = False
        evidence = []
        for item in self.result.evidence:
            metadata = dict(item.metadata)
            if (
                not changed
                and metadata.get("original_evidence_id") in candidate_ids
            ):
                item = _metadata(item, original_task_id="unrelated-task")
                changed = True
            evidence.append(item)
        self.assertTrue(changed)
        forged = replace(self.result, evidence=tuple(evidence))

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.reassignment_attempted)
        self.assertFalse(observed.fallback_succeeded)
        self.assertFalse(observed.fault_contained)

    def test_containment_requires_fault_and_plan_linked_actions(self) -> None:
        forged = _rewrite_first_event(
            self.result,
            EventKind.ACTION_EXECUTED,
            fault_id="unrelated-fault",
            plan_id="unrelated-plan",
        )

        observed = derive_run_metrics(forged)

        self.assertTrue(observed.reassignment_attempted)
        self.assertTrue(observed.fallback_succeeded)
        self.assertTrue(observed.recovered)
        self.assertFalse(observed.fault_contained)


class RuntimeExhaustionHardeningTests(unittest.TestCase):
    """Injected metadata alone cannot claim that all retries are exhausted."""

    def test_retryable_injected_attempt_does_not_exhaust_while_retry_remains(self) -> None:
        limits = BudgetLimits(
            max_attempts=4,
            max_cost_units=4,
            max_tool_calls=4,
            max_virtual_ms=10_000,
        )
        scheduler = VirtualScheduler(limits, BudgetLedger(limits))
        board = EvidenceBoard()
        log = EventLog()
        task = TaskContract(
            task_id="investigate_worker",
            kind="incident_investigation",
            role=Role.TELEMETRY,
            timeout_ms=1_000,
            retry=RetryPolicy(max_attempts=2, backoff_ms=(25,)),
        )
        injected_metadata = (
            ("candidate_evidence_ids", "E1"),
            ("candidate_fingerprint", "candidate"),
            ("candidate_status", "succeeded"),
            ("fault_id", "fault-1"),
            ("fault_injected", "true"),
            ("fault_name", WORKER_FAULT),
            ("fault_stage", "investigation"),
            ("faulty_fingerprint", "faulty"),
            ("faulty_status", "failed"),
            ("target_attempt", "1"),
            ("target_task_id", task.task_id),
        )
        script = (
            ToolAttempt(
                duration_ms=10,
                status=TaskStatus.FAILED,
                error="retryable worker failure",
                retryable=True,
                metadata=injected_metadata,
            ),
            ToolAttempt(
                duration_ms=10,
                status=TaskStatus.SUCCEEDED,
                output="replacement attempt succeeded",
            ),
        )

        results, _ = scheduler.run_parallel(
            (task,),
            {task.task_id: script},
            start_ms=0,
            board=board,
            log=log,
        )

        self.assertEqual(results[0].status, TaskStatus.SUCCEEDED)
        self.assertEqual(results[0].attempts, 2)
        self.assertEqual(
            sum(
                event.kind is EventKind.TASK_RETRY_SCHEDULED
                for event in log.events
            ),
            1,
        )
        self.assertFalse(
            any(event.kind is EventKind.RETRY_EXHAUSTED for event in log.events)
        )


class WorkerPathCompatibilityHardeningTests(unittest.TestCase):
    """Worker-only metrics stay dormant on clean and planning-fault paths."""

    def test_clean_and_planning_paths_have_no_worker_lifecycle_credit(self) -> None:
        clean = derive_run_metrics(
            run_preset("specialists_with_critic", 101, "bot_db_contention")
        )
        planning = derive_run_metrics(
            run_preset(
                "specialists_with_critic",
                101,
                "bot_db_contention",
                PLANNING_OMISSION.name,
            )
        )

        for observed in (clean, planning):
            self.assertEqual(observed.fault_target_task_ids, ())
            self.assertEqual(observed.terminal_fault_ids, ())
            self.assertEqual(observed.reassigned_fault_ids, ())
            self.assertEqual(observed.fallback_completed_fault_ids, ())
            self.assertEqual(observed.contained_fault_ids, ())
            self.assertEqual(observed.fallback_task_ids, ())
        self.assertFalse(clean.fault_injected)
        self.assertTrue(planning.fault_injected)
        self.assertTrue(planning.fault_detected)
        self.assertTrue(planning.fault_repaired)


if __name__ == "__main__":
    unittest.main()
