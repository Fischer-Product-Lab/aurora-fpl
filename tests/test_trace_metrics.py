"""Verification tests for trace-derived metrics and strategy ablations."""

from __future__ import annotations

from dataclasses import replace
import unittest

from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import EventKind, TaskStatus
from aurora_lab.reporting import format_comparison
from aurora_lab.simulation import ABLATION_PRESETS, run_preset


class TraceDerivedMetricsTests(unittest.TestCase):
    """Treat the immutable trace and task results as the measurement source."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = {
            preset.name: run_preset(preset.name, seed=101)
            for preset in ABLATION_PRESETS
        }
        cls.metrics = {
            name: derive_run_metrics(result)
            for name, result in cls.results.items()
        }

    def test_summary_metadata_cannot_forge_observed_facts(self) -> None:
        original = self.results["full_orchestration"]
        forged = replace(
            original,
            metadata=(
                ("cancellation_count", "0"),
                ("critic_rejections", "0"),
                ("injection_denied", "false"),
                ("policy_violations", "999"),
                ("retry_recovered", "false"),
                ("useful_parallelism", "false"),
                ("verification_passes", "0"),
            ),
        )

        self.assertEqual(
            derive_run_metrics(forged),
            derive_run_metrics(original),
        )
        observed = derive_run_metrics(forged)
        self.assertTrue(observed.parallelism_used)
        self.assertEqual(observed.cancelled_task_ids, ("broad_log_scan",))
        self.assertEqual(observed.critic_rejections, 1)
        self.assertTrue(observed.injection_denied)
        self.assertTrue(observed.retry_recovered)
        self.assertEqual(observed.verification_passes, 2)
        self.assertEqual(observed.approval_violations, 0)

    def test_full_orchestration_cancels_a_real_queued_task(self) -> None:
        result = self.results["full_orchestration"]
        cancelled = next(
            task for task in result.task_results if task.task_id == "broad_log_scan"
        )
        lifecycle = tuple(
            event.kind
            for event in result.trace
            if event.task_id == cancelled.task_id
        )

        self.assertIs(cancelled.status, TaskStatus.CANCELLED)
        self.assertEqual(cancelled.attempts, 0)
        self.assertIsNone(cancelled.started_at_ms)
        self.assertEqual(cancelled.cost_units, 0)
        self.assertEqual(cancelled.tool_calls, 0)
        self.assertIn(EventKind.TASK_QUEUED, lifecycle)
        self.assertIn(EventKind.TASK_CANCELLED, lifecycle)
        self.assertNotIn(EventKind.TASK_STARTED, lifecycle)
        self.assertNotIn(EventKind.TOOL_ATTEMPT, lifecycle)
        self.assertEqual(
            derive_run_metrics(result).cancelled_task_ids,
            ("broad_log_scan",),
        )

        missing_event = replace(
            result,
            trace=tuple(
                event
                for event in result.trace
                if not (
                    event.kind is EventKind.TASK_CANCELLED
                    and event.task_id == "broad_log_scan"
                )
            ),
        )
        self.assertEqual(
            derive_run_metrics(missing_event).cancelled_task_ids,
            (),
        )

    def test_parallelism_and_recovery_milestones_follow_time_order(self) -> None:
        sequential = self.metrics["sequential_generalist"]
        parallel = self.metrics["parallel_generalist"]

        self.assertEqual(sequential.max_concurrency, 1)
        self.assertFalse(sequential.parallelism_used)
        self.assertGreater(parallel.max_concurrency, 1)
        self.assertTrue(parallel.parallelism_used)
        self.assertLess(parallel.last_action_ms, sequential.last_action_ms)

        for name, metrics in self.metrics.items():
            with self.subTest(strategy=name):
                self.assertEqual(metrics.verification_passes, 2)
                self.assertIsNotNone(metrics.last_action_ms)
                self.assertIsNotNone(metrics.first_slo_pass_ms)
                self.assertIsNotNone(metrics.confirmed_recovery_ms)
                self.assertLess(metrics.last_action_ms, metrics.first_slo_pass_ms)
                self.assertLess(
                    metrics.first_slo_pass_ms,
                    metrics.confirmed_recovery_ms,
                )
                self.assertTrue(metrics.recovered)

    def test_presets_expose_the_intended_ablation_relationships(self) -> None:
        names = tuple(preset.name for preset in ABLATION_PRESETS)
        self.assertEqual(
            names,
            (
                "sequential_generalist",
                "parallel_generalist",
                "specialists_no_critic",
                "specialists_with_critic",
                "full_orchestration",
            ),
        )
        self.assertTrue(all(preset.policy_controls for preset in ABLATION_PRESETS))

        for name in (
            "sequential_generalist",
            "parallel_generalist",
            "specialists_no_critic",
        ):
            with self.subTest(strategy=name, review="self"):
                self.assertEqual(self.metrics[name].critic_rejections, 0)
                self.assertEqual(self.metrics[name].replans, 0)

        for name in ("specialists_with_critic", "full_orchestration"):
            with self.subTest(strategy=name, review="independent"):
                self.assertEqual(self.metrics[name].critic_rejections, 1)
                self.assertEqual(self.metrics[name].replans, 1)

        parallel_generalist = self.results["parallel_generalist"]
        specialists = self.results["specialists_no_critic"]
        reviewed = self.results["specialists_with_critic"]
        full = self.results["full_orchestration"]

        # Deterministic v1 deliberately makes specialization a null result;
        # only task ownership changes, not the scripted tools or outcome.
        self.assertEqual(
            self.metrics["parallel_generalist"].last_action_ms,
            self.metrics["specialists_no_critic"].last_action_ms,
        )
        self.assertEqual(
            parallel_generalist.cost_units_used,
            specialists.cost_units_used,
        )
        self.assertEqual(
            parallel_generalist.tool_calls_used,
            specialists.tool_calls_used,
        )

        self.assertGreater(reviewed.cost_units_used, specialists.cost_units_used)
        self.assertGreater(
            self.metrics["specialists_with_critic"].last_action_ms,
            self.metrics["specialists_no_critic"].last_action_ms,
        )
        self.assertLess(full.cost_units_used, reviewed.cost_units_used)
        self.assertLess(full.tool_calls_used, reviewed.tool_calls_used)
        self.assertEqual(
            self.metrics["full_orchestration"].cancelled_task_ids,
            ("broad_log_scan",),
        )
        self.assertTrue(
            all(
                result.status is TaskStatus.SUCCEEDED
                for result in self.results.values()
            )
        )

    def test_comparison_does_not_substitute_total_time_for_missing_actions(self) -> None:
        original = self.results["full_orchestration"]
        no_actions = replace(
            original,
            trace=tuple(
                event
                for event in original.trace
                if event.kind is not EventKind.ACTION_EXECUTED
            ),
        )

        rendered = format_comparison(no_actions, original, trace_mode="none")

        last_action_row = next(
            line for line in rendered.splitlines() if line.startswith("Last action")
        )
        self.assertIn("n/a", last_action_row)


if __name__ == "__main__":
    unittest.main()
