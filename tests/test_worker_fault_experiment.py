"""Reporting acceptance tests for the permanent-worker-failure matrix."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest

from aurora_lab.cli import build_parser, main
from aurora_lab.experiments import (
    WorkerFaultArmMeasurement,
    WorkerFaultExperimentReport,
    WorkerFaultStrategyAggregate,
    run_fault_experiment,
)
from aurora_lab.faults import PERMANENT_WORKER_FAILURE
from aurora_lab.reporting import format_fault_experiment, to_primitive
from aurora_lab.simulation import WORKER_FAILURE_PRESETS


WORKER_FAULT = PERMANENT_WORKER_FAILURE.name


class WorkerFaultExperimentTests(unittest.TestCase):
    def test_matrix_exposes_safe_degradation_and_bounded_containment(self) -> None:
        report = run_fault_experiment(start_seed=101, count=3, fault_name=WORKER_FAULT)

        self.assertIsInstance(report, WorkerFaultExperimentReport)
        self.assertEqual(
            report.preset_order,
            tuple(preset.name for preset in WORKER_FAILURE_PRESETS),
        )
        self.assertEqual(report.condition_order, ("matched_control", WORKER_FAULT))
        self.assertEqual(len(report.pairs), 3 * len(WORKER_FAILURE_PRESETS))
        self.assertIsNone(report.critic_effect)
        self.assertTrue(
            all(
                isinstance(pair.control, WorkerFaultArmMeasurement)
                and isinstance(pair.faulted, WorkerFaultArmMeasurement)
                for pair in report.pairs
            )
        )

        by_strategy = {item.strategy: item for item in report.aggregates}
        self.assertTrue(
            all(
                isinstance(item, WorkerFaultStrategyAggregate)
                for item in report.aggregates
            )
        )
        for strategy, aggregate in by_strategy.items():
            self.assertEqual(aggregate.control_recovery_rate_pct, 100.0)
            self.assertEqual(aggregate.fault_injection_rate_pct, 100.0)
            self.assertEqual(
                aggregate.fault_target_terminal_failure_rate_pct,
                100.0,
            )
            self.assertEqual(aggregate.fault_detection_rate_pct, 100.0)
            self.assertEqual(aggregate.fault_repair_rate_pct, 0.0)
            self.assertEqual(aggregate.fault_policy_safe_rate_pct, 100.0)
            self.assertEqual(
                aggregate.control_mean_required_evidence_tag_coverage_pct,
                100.0,
            )
            has_fallback = strategy in {
                "specialists_with_fallback",
                "full_orchestration",
            }
            self.assertEqual(
                aggregate.fault_recovery_rate_pct,
                100.0 if has_fallback else 0.0,
            )
            self.assertEqual(
                aggregate.fault_reassignment_rate_pct,
                100.0 if has_fallback else 0.0,
            )
            self.assertEqual(
                aggregate.fault_fallback_success_rate_pct,
                100.0 if has_fallback else 0.0,
            )
            self.assertEqual(
                aggregate.fault_containment_rate_pct,
                100.0 if has_fallback else 0.0,
            )
            self.assertEqual(
                aggregate.fault_safe_degradation_rate_pct,
                0.0 if has_fallback else 100.0,
            )
            if has_fallback:
                self.assertEqual(
                    aggregate.fault_mean_required_evidence_tag_coverage_pct,
                    100.0,
                )
                self.assertEqual(aggregate.fault_missing_required_evidence_tags, ())
            else:
                self.assertLess(
                    aggregate.fault_mean_required_evidence_tag_coverage_pct,
                    100.0,
                )
                self.assertTrue(aggregate.fault_missing_required_evidence_tags)

        effect = report.fallback_effect
        self.assertEqual(effect.control_with_minus_without_recovery_rate_pp, 0.0)
        self.assertEqual(effect.fault_with_minus_without_recovery_rate_pp, 100.0)
        self.assertEqual(effect.recovery_difference_in_differences_pp, 100.0)
        self.assertEqual(effect.fault_rescues, 3)
        self.assertEqual(effect.fault_regressions, 0)
        self.assertEqual(effect.fault_with_minus_without_containment_rate_pp, 100.0)
        self.assertEqual(
            effect.fault_with_minus_without_safe_degradation_rate_pp,
            -100.0,
        )
        self.assertGreater(
            effect.fault_with_minus_without_required_evidence_tag_coverage_pp,
            0.0,
        )
        self.assertEqual(effect.fault_with_minus_without_policy_safe_rate_pp, 0.0)

    def test_worker_report_and_primitive_use_containment_language(self) -> None:
        report = run_fault_experiment(start_seed=101, count=1, fault_name=WORKER_FAULT)
        rendered = format_fault_experiment(report, show_runs=True)
        payload = to_primitive(report)

        self.assertIn("WORKER-FAILURE LIFECYCLE", rendered)
        self.assertIn("ISOLATED FALLBACK EFFECT", rendered)
        self.assertIn("Target terminal", rendered)
        self.assertIn("Containment means", rendered)
        self.assertNotIn("INDEPENDENT CRITIC EFFECT", rendered)
        self.assertIn("fallback_effect", payload)
        self.assertIsNone(payload["critic_effect"])
        self.assertEqual(payload["fallback_effect"]["fault_rescues"], 1)
        self.assertEqual(
            payload["aggregates"][0]["fault_target_terminal_failure_rate_pct"],
            100.0,
        )

    def test_cli_accepts_worker_matrix_and_fallback_preset(self) -> None:
        parser = build_parser()
        matrix = parser.parse_args(
            ["fault-matrix", "--fault", WORKER_FAULT, "--count", "1"]
        )
        direct = parser.parse_args(
            [
                "run",
                "--strategy",
                "specialists_with_fallback",
                "--fault",
                WORKER_FAULT,
            ]
        )

        self.assertEqual(matrix.fault, WORKER_FAULT)
        self.assertEqual(direct.strategy, "specialists_with_fallback")
        self.assertEqual(direct.fault, WORKER_FAULT)

    def test_direct_cli_exit_codes_distinguish_degradation_and_recovery(self) -> None:
        def invoke(strategy: str, *, control: bool = False) -> int:
            arguments = [
                "run",
                "--strategy",
                strategy,
                "--seed",
                "101",
                "--variant",
                "bot_db_contention",
                "--fault",
                WORKER_FAULT,
                "--trace",
                "none",
            ]
            if control:
                arguments.append("--fault-control")
            with redirect_stdout(StringIO()):
                return main(arguments)

        self.assertEqual(invoke("specialists_with_critic"), 1)
        self.assertEqual(invoke("specialists_with_fallback"), 0)
        self.assertEqual(invoke("specialists_with_fallback", control=True), 0)


if __name__ == "__main__":
    unittest.main()
