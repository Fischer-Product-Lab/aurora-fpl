"""Focused tests for the configurable five-preset ablation."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from statistics import fmean
import unittest

from aurora_lab.cli import build_parser, main
from aurora_lab.experiments import run_ablation_experiment, run_experiment
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.reporting import format_ablation, to_primitive
from aurora_lab.simulation import ABLATION_PRESETS, run_comparison


class AblationExperimentTests(unittest.TestCase):
    def test_validation_matches_paired_experiment(self) -> None:
        for invalid in (0, -1):
            with self.subTest(count=invalid):
                with self.assertRaisesRegex(ValueError, "at least 1"):
                    run_ablation_experiment(count=invalid)

        with self.assertRaisesRegex(TypeError, "count must be an integer"):
            run_ablation_experiment(count="2")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "start_seed must be an integer"):
            run_ablation_experiment(start_seed="0")  # type: ignore[arg-type]

    def test_runs_are_seed_major_and_follow_declared_preset_order(self) -> None:
        report = run_ablation_experiment(start_seed=12, count=2)
        expected_order = tuple(preset.name for preset in ABLATION_PRESETS)

        self.assertEqual(report.preset_order, expected_order)
        self.assertEqual(len(report.runs), 2 * len(expected_order))
        self.assertEqual(
            tuple((run.seed, run.strategy) for run in report.runs),
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

    def test_paired_milestones_are_derived_from_trace(self) -> None:
        report = run_experiment(start_seed=23, count=1)
        pair = report.pairs[0]
        orchestrated, baseline = run_comparison(23)
        orchestrated_metrics = derive_run_metrics(orchestrated)
        baseline_metrics = derive_run_metrics(baseline)

        self.assertEqual(
            pair.orchestrated_last_action_ms,
            orchestrated_metrics.last_action_ms,
        )
        self.assertEqual(
            pair.orchestrated_first_slo_pass_ms,
            orchestrated_metrics.first_slo_pass_ms,
        )
        self.assertEqual(
            pair.orchestrated_confirmed_recovery_ms,
            orchestrated_metrics.confirmed_recovery_ms,
        )
        self.assertEqual(pair.baseline_last_action_ms, baseline_metrics.last_action_ms)
        self.assertEqual(
            pair.baseline_first_slo_pass_ms,
            baseline_metrics.first_slo_pass_ms,
        )
        self.assertEqual(
            pair.baseline_confirmed_recovery_ms,
            baseline_metrics.confirmed_recovery_ms,
        )

    def test_aggregates_are_derived_from_strategy_rows(self) -> None:
        report = run_ablation_experiment(start_seed=30, count=3)

        for aggregate in report.aggregates:
            group = tuple(
                run for run in report.runs if run.strategy == aggregate.strategy
            )
            with self.subTest(strategy=aggregate.strategy):
                self.assertEqual(aggregate.runs, 3)
                self.assertEqual(
                    aggregate.mean_score,
                    round(fmean(run.score for run in group), 2),
                )
                self.assertEqual(
                    aggregate.mean_cost_units,
                    round(fmean(run.cost_units for run in group), 2),
                )
                first_slo_values = tuple(
                    run.first_slo_pass_ms
                    for run in group
                    if run.first_slo_pass_ms is not None
                )
                self.assertEqual(
                    aggregate.first_slo_pass_samples,
                    len(first_slo_values),
                )
                self.assertEqual(
                    aggregate.mean_first_slo_pass_ms,
                    (
                        round(fmean(first_slo_values), 2)
                        if first_slo_values
                        else None
                    ),
                )

    def test_missing_milestones_are_null_in_json_and_na_in_table(self) -> None:
        report = run_ablation_experiment(start_seed=5, count=1)
        missing_run = replace(
            report.runs[0],
            last_action_ms=None,
            first_slo_pass_ms=None,
            confirmed_recovery_ms=None,
        )
        missing_aggregate = replace(
            report.aggregates[0],
            mean_last_action_ms=None,
            last_action_samples=0,
            mean_first_slo_pass_ms=None,
            first_slo_pass_samples=0,
            mean_confirmed_recovery_ms=None,
            confirmed_recovery_samples=0,
        )
        report = replace(
            report,
            runs=(missing_run, *report.runs[1:]),
            aggregates=(missing_aggregate, *report.aggregates[1:]),
        )

        primitive = to_primitive(report)
        encoded = json.dumps(primitive, sort_keys=True)
        table = format_ablation(report, show_runs=True)

        self.assertIsNone(primitive["runs"][0]["first_slo_pass_ms"])
        self.assertIn('"first_slo_pass_ms": null', encoded)
        self.assertIn("n/a", table)

    def test_report_is_deterministic(self) -> None:
        first = run_ablation_experiment(start_seed=50, count=2)
        second = run_ablation_experiment(start_seed=50, count=2)

        self.assertEqual(first, second)
        self.assertEqual(to_primitive(first), to_primitive(second))


class AblationCliTests(unittest.TestCase):
    def test_run_command_accepts_every_ablation_preset(self) -> None:
        for preset in ABLATION_PRESETS:
            with self.subTest(strategy=preset.name):
                args = build_parser().parse_args(
                    ["run", "--strategy", preset.name, "--trace", "none"]
                )
                self.assertEqual(args.strategy, preset.name)

        output = StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--seed",
                    "101",
                    "--strategy",
                    "specialists_no_critic",
                    "--trace",
                    "none",
                ]
            )
        self.assertEqual(status, 0)
        self.assertIn("SPECIALISTS_NO_CRITIC", output.getvalue())

    def test_parser_accepts_ablation_options(self) -> None:
        args = build_parser().parse_args(
            [
                "ablation",
                "--start-seed",
                "17",
                "--count",
                "4",
                "--variant",
                "regional_gateway_degradation",
                "--show-runs",
                "--json",
                "ablation.json",
            ]
        )

        self.assertEqual(args.command, "ablation")
        self.assertEqual(args.start_seed, 17)
        self.assertEqual(args.count, 4)
        self.assertEqual(args.variant, "regional_gateway_degradation")
        self.assertTrue(args.show_runs)
        self.assertEqual(args.json, "ablation.json")

    def test_main_prints_table_and_writes_structured_report(self) -> None:
        destination = Path.cwd() / f".ablation-cli-test-{id(self)}.json"
        try:
            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "ablation",
                        "--start-seed",
                        "3",
                        "--count",
                        "1",
                        "--json",
                        str(destination),
                    ]
                )

            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(status, 0)
            self.assertIn("ABLATION MATRIX", output.getvalue())
            self.assertEqual(payload["count"], 1)
            self.assertEqual(len(payload["runs"]), len(ABLATION_PRESETS))
            self.assertEqual(payload["preset_order"], [
                preset.name for preset in ABLATION_PRESETS
            ])
        finally:
            destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
