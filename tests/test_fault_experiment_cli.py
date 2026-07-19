"""Paired fault-matrix contracts, reporting, and CLI coverage."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import unittest

from aurora_lab.cli import build_parser, main
from aurora_lab.experiments import run_fault_experiment
from aurora_lab.reporting import format_fault_experiment, to_primitive
from aurora_lab.simulation import ABLATION_PRESETS


class FaultExperimentTests(unittest.TestCase):
    def test_validation_and_declared_conditions(self) -> None:
        for invalid in (0, -1):
            with self.subTest(count=invalid):
                with self.assertRaisesRegex(ValueError, "at least 1"):
                    run_fault_experiment(count=invalid)

        with self.assertRaisesRegex(TypeError, "count must be an integer"):
            run_fault_experiment(count="1")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "start_seed must be an integer"):
            run_fault_experiment(start_seed="0")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "fault_name must be a string"):
            run_fault_experiment(fault_name=None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "injectable fault"):
            run_fault_experiment(fault_name="none")

        report = run_fault_experiment(start_seed=101, count=1)
        self.assertEqual(
            report.condition_order,
            ("matched_control", "planning_omission"),
        )
        self.assertEqual(report.fault_name, "planning_omission")

    def test_pairs_are_seed_major_and_follow_preset_order(self) -> None:
        report = run_fault_experiment(start_seed=12, count=2)
        expected_order = tuple(preset.name for preset in ABLATION_PRESETS)

        self.assertEqual(report.preset_order, expected_order)
        self.assertEqual(len(report.pairs), 2 * len(expected_order))
        self.assertEqual(
            tuple((pair.seed, pair.strategy) for pair in report.pairs),
            tuple(
                (seed, strategy)
                for seed in (12, 13)
                for strategy in expected_order
            ),
        )
        self.assertEqual(
            tuple(item.strategy for item in report.aggregates),
            expected_order,
        )
        self.assertEqual(sum(count for _, count in report.variant_counts), 2)

    def test_fault_changes_actual_recovery_only_without_independent_review(self) -> None:
        report = run_fault_experiment(start_seed=101, count=1)
        by_strategy = {pair.strategy: pair for pair in report.pairs}

        for strategy in (
            "sequential_generalist",
            "parallel_generalist",
            "specialists_no_critic",
        ):
            with self.subTest(strategy=strategy):
                pair = by_strategy[strategy]
                self.assertEqual(pair.control.fault_arm, "control")
                self.assertEqual(pair.faulted.fault_arm, "fault")
                self.assertIn("study=planning_omission:arm=control", pair.control.run_id)
                self.assertIn("fault=planning_omission", pair.faulted.run_id)
                self.assertTrue(pair.control.recovered)
                self.assertFalse(pair.faulted.recovered)
                self.assertIsNone(pair.faulted.confirmed_recovery_ms)
                self.assertTrue(pair.faulted.fault_injected)
                self.assertFalse(pair.faulted.fault_detected)
                self.assertFalse(pair.faulted.fault_repaired)
                self.assertEqual(pair.faulted.required_action_recall_pct, 75.0)
                self.assertTrue(pair.faulted.policy_safe)

        for strategy in ("specialists_with_critic", "full_orchestration"):
            with self.subTest(strategy=strategy):
                pair = by_strategy[strategy]
                self.assertEqual(pair.control.fault_arm, "control")
                self.assertEqual(pair.faulted.fault_arm, "fault")
                self.assertTrue(pair.control.recovered)
                self.assertTrue(pair.faulted.recovered)
                self.assertIsNotNone(pair.faulted.confirmed_recovery_ms)
                self.assertTrue(pair.faulted.fault_injected)
                self.assertTrue(pair.faulted.fault_detected)
                self.assertTrue(pair.faulted.fault_repaired)
                self.assertEqual(pair.faulted.required_action_recall_pct, 100.0)
                self.assertTrue(pair.faulted.policy_safe)

    def test_critic_effect_is_matched_and_reports_difference_in_differences(self) -> None:
        report = run_fault_experiment(start_seed=101, count=2)
        effect = report.critic_effect

        self.assertEqual(effect.without_critic_strategy, "specialists_no_critic")
        self.assertEqual(effect.with_critic_strategy, "specialists_with_critic")
        self.assertEqual(effect.cases, 2)
        self.assertEqual(effect.control_with_minus_without_recovery_rate_pp, 0.0)
        self.assertEqual(effect.fault_with_minus_without_recovery_rate_pp, 100.0)
        self.assertEqual(effect.recovery_difference_in_differences_pp, 100.0)
        self.assertEqual(effect.fault_rescues, 2)
        self.assertEqual(effect.fault_regressions, 0)
        self.assertEqual(effect.fault_net_rescues, 2)
        self.assertEqual(effect.fault_common_recovery_pairs, 0)
        self.assertIsNone(
            effect.mean_fault_with_minus_without_confirmed_recovery_ms
        )
        self.assertEqual(
            effect.fault_with_minus_without_required_action_recall_pp,
            25.0,
        )
        self.assertGreater(effect.fault_with_minus_without_mean_cost_units, 0.0)

    def test_missing_recovery_is_null_in_json_and_na_in_report(self) -> None:
        report = run_fault_experiment(start_seed=101, count=1)
        primitive = to_primitive(report)
        no_critic_index = report.preset_order.index("specialists_no_critic")
        faulted = primitive["pairs"][no_critic_index]["faulted"]
        table = format_fault_experiment(report, show_runs=True)

        self.assertIsNone(faulted["confirmed_recovery_ms"])
        self.assertIn('"confirmed_recovery_ms": null', json.dumps(primitive))
        self.assertIn("n/a", table)
        self.assertIn("INDEPENDENT CRITIC EFFECT", table)
        self.assertIn("+100.0pp", table)
        self.assertIn("PAIRED FAULT RUNS", table)

    def test_latency_report_leads_with_paired_delta_and_exposes_survivors(self) -> None:
        report = run_fault_experiment(start_seed=101, count=2)
        table = format_fault_experiment(report, show_runs=True)

        self.assertIn("CONFIRMED-RECOVERY LATENCY", table)
        self.assertIn("FAULT-ARM TRACE QUALITY", table)
        self.assertIn("Injected/Detected/Repaired F", table)
        self.assertIn("Paired F-C (common n)", table)
        self.assertIn("Control survivor mean (n/cases)", table)
        self.assertIn("Fault survivor mean (n/cases)", table)
        self.assertIn("Policy safety F", table)
        self.assertIn("Policy safety C/F", table)

        latency_lines = tuple(
            line
            for line in table.splitlines()
            if line.startswith(("specialists_no_critic", "specialists_with_critic"))
            and "(n=" in line
        )
        self.assertEqual(len(latency_lines), 2)
        no_critic = next(
            line for line in latency_lines if line.startswith("specialists_no_critic")
        )
        with_critic = next(
            line for line in latency_lines if line.startswith("specialists_with_critic")
        )
        self.assertIn("n/a (n=0)", no_critic)
        self.assertIn("(2/2)", no_critic)
        self.assertIn("n/a (0/2)", no_critic)
        self.assertIn("(n=2)", with_critic)
        self.assertEqual(with_critic.count("(2/2)"), 2)

    def test_latency_report_does_not_hide_partial_recovery_denominators(self) -> None:
        report = run_fault_experiment(start_seed=101, count=1)
        aggregate = replace(
            report.aggregates[0],
            cases=4,
            mean_fault_minus_control_confirmed_recovery_ms=1_234.0,
            common_recovery_pairs=2,
            control_mean_confirmed_recovery_ms=10_000.0,
            control_confirmed_recovery_samples=4,
            fault_mean_confirmed_recovery_ms=99_000.0,
            fault_confirmed_recovery_samples=2,
        )
        table = format_fault_experiment(
            replace(report, aggregates=(aggregate,)),
        )
        latency_line = next(
            line
            for line in table.splitlines()
            if line.startswith(aggregate.strategy) and "(n=" in line
        )

        self.assertIn("+1.23s (n=2)", latency_line)
        self.assertIn("10.00s (4/4)", latency_line)
        self.assertIn("99.00s (2/4)", latency_line)

    def test_report_is_deterministic(self) -> None:
        first = run_fault_experiment(start_seed=30, count=2)
        second = run_fault_experiment(start_seed=30, count=2)

        self.assertEqual(first, second)
        self.assertEqual(to_primitive(first), to_primitive(second))


class FaultCliTests(unittest.TestCase):
    def test_parser_accepts_fault_matrix_and_direct_faults(self) -> None:
        args = build_parser().parse_args(
            [
                "fault-matrix",
                "--start-seed",
                "17",
                "--count",
                "4",
                "--variant",
                "regional_gateway_degradation",
                "--fault",
                "planning_omission",
                "--show-runs",
                "--json",
                "fault.json",
            ]
        )
        self.assertEqual(args.command, "fault-matrix")
        self.assertEqual(args.fault, "planning_omission")
        self.assertTrue(args.show_runs)

        for command in ("run", "compare"):
            with self.subTest(command=command):
                direct = build_parser().parse_args(
                    [command, "--fault", "planning_omission"]
                )
                self.assertEqual(direct.fault, "planning_omission")
                self.assertFalse(direct.fault_control)

                control = build_parser().parse_args(
                    [
                        command,
                        "--fault",
                        "planning_omission",
                        "--fault-control",
                    ]
                )
                self.assertTrue(control.fault_control)

    def test_direct_run_compact_trace_includes_fault_and_self_review(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--seed",
                    "101",
                    "--strategy",
                    "specialists_no_critic",
                    "--fault",
                    "planning_omission",
                    "--trace",
                    "summary",
                ]
            )

        rendered = output.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("self_review_completed", rendered)
        self.assertIn("fault_injected", rendered)

    def test_direct_matched_control_has_distinct_arm_without_fault_events(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--seed",
                    "101",
                    "--strategy",
                    "specialists_with_critic",
                    "--fault",
                    "planning_omission",
                    "--fault-control",
                    "--trace",
                    "summary",
                ]
            )

        rendered = output.getvalue()
        self.assertEqual(status, 0)
        self.assertIn("Fault study: planning_omission · arm: control", rendered)
        self.assertIn("fault_arm=control", rendered)
        self.assertIn("fault_study=planning_omission", rendered)
        self.assertIn("critique_accepted", rendered)
        self.assertNotIn("fault_injected", rendered)
        self.assertNotIn("replan_created", rendered)

    def test_main_prints_table_and_writes_structured_report(self) -> None:
        destination = Path.cwd() / f".fault-cli-test-{id(self)}.json"
        try:
            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "fault-matrix",
                        "--start-seed",
                        "101",
                        "--count",
                        "1",
                        "--json",
                        str(destination),
                    ]
                )

            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertIn("FAULT MATRIX", output.getvalue())
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["condition_order"], [
                "matched_control",
                "planning_omission",
            ])
            self.assertEqual(len(payload["pairs"]), len(ABLATION_PRESETS))
            self.assertEqual(payload["critic_effect"][
                "recovery_difference_in_differences_pp"
            ], 100.0)
        finally:
            destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
