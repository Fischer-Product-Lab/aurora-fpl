"""Tests for deterministic multi-seed orchestration experiments."""

from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr
from io import StringIO
import json
from statistics import fmean
import unittest

from aurora_lab.cli import build_parser
from aurora_lab.experiments import run_experiment
from aurora_lab.reporting import format_experiment, to_primitive


class ExperimentTests(unittest.TestCase):
    def test_count_validation(self) -> None:
        for invalid in (0, -1, -20):
            with self.subTest(count=invalid):
                with self.assertRaisesRegex(ValueError, "at least 1"):
                    run_experiment(count=invalid)

        with self.assertRaisesRegex(TypeError, "count must be an integer"):
            run_experiment(count="2")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "start_seed must be an integer"):
            run_experiment(start_seed="0")  # type: ignore[arg-type]

    def test_report_and_json_primitive_are_deterministic(self) -> None:
        first = run_experiment(start_seed=7, count=5)
        second = run_experiment(start_seed=7, count=5)

        self.assertEqual(first, second)
        first_primitive = to_primitive(first)
        second_primitive = to_primitive(second)
        self.assertEqual(first_primitive, second_primitive)
        first_json = json.dumps(
            first_primitive,
            sort_keys=True,
            separators=(",", ":"),
        )
        second_json = json.dumps(
            second_primitive,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(first_json, second_json)
        self.assertEqual(json.loads(first_json), first_primitive)

    def test_count_and_variant_counts_match_pair_rows(self) -> None:
        report = run_experiment(start_seed=10, count=9)
        actual_counts = Counter(pair.variant for pair in report.pairs)

        self.assertEqual(report.count, 9)
        self.assertEqual(len(report.pairs), report.count)
        self.assertEqual(
            [pair.seed for pair in report.pairs],
            list(range(10, 19)),
        )
        self.assertEqual(dict(report.variant_counts), dict(actual_counts))
        self.assertEqual(sum(count for _, count in report.variant_counts), 9)
        self.assertEqual(sum(item.cases for item in report.by_variant), 9)
        self.assertEqual(report.orchestrated.runs, 9)
        self.assertEqual(report.baseline.runs, 9)

    def test_variant_filter_pins_every_case(self) -> None:
        variant = "seat_cache_stampede"
        report = run_experiment(
            start_seed=20,
            count=6,
            variant_name=variant,
        )

        self.assertEqual(report.variant_filter, variant)
        self.assertEqual({pair.variant for pair in report.pairs}, {variant})
        self.assertEqual(report.variant_counts, ((variant, 6),))
        self.assertEqual(len(report.by_variant), 1)
        self.assertEqual(report.by_variant[0].variant, variant)
        self.assertEqual(report.by_variant[0].cases, 6)

    def test_aggregates_are_derived_from_pair_measurements(self) -> None:
        report = run_experiment(start_seed=30, count=8)

        for prefix, aggregate in (
            ("orchestrated", report.orchestrated),
            ("baseline", report.baseline),
        ):
            values = lambda suffix: tuple(
                float(getattr(pair, f"{prefix}_{suffix}"))
                for pair in report.pairs
            )
            expected = {
                "runs": len(report.pairs),
                "success_rate": round(100.0 * fmean(values("success")), 2),
                "mean_score": round(fmean(values("score")), 2),
                "minimum_score": round(min(values("score")), 2),
                "mean_last_action_ms": round(
                    fmean(values("last_action_ms")), 2
                ),
                "mean_total_ms": round(fmean(values("total_ms")), 2),
                "mean_cost_units": round(fmean(values("cost_units")), 2),
                "mean_tool_calls": round(fmean(values("tool_calls")), 2),
            }
            for field, expected_value in expected.items():
                with self.subTest(strategy=prefix, field=field):
                    self.assertEqual(getattr(aggregate, field), expected_value)

        for aggregate in report.by_variant:
            group = tuple(
                pair for pair in report.pairs if pair.variant == aggregate.variant
            )
            with self.subTest(variant=aggregate.variant):
                self.assertEqual(aggregate.cases, len(group))
                self.assertEqual(
                    aggregate.orchestrated_mean_score,
                    round(fmean(pair.orchestrated_score for pair in group), 2),
                )
                self.assertEqual(
                    aggregate.baseline_mean_score,
                    round(fmean(pair.baseline_score for pair in group), 2),
                )
                self.assertEqual(
                    aggregate.mean_score_delta,
                    round(fmean(pair.score_delta for pair in group), 2),
                )
                self.assertEqual(
                    aggregate.mean_action_time_advantage_ms,
                    round(
                        fmean(pair.action_time_advantage_ms for pair in group),
                        2,
                    ),
                )

    def test_current_thirty_seed_acceptance_matrix(self) -> None:
        report = run_experiment(start_seed=0, count=30)

        self.assertEqual(
            report.orchestrated_wins + report.baseline_wins + report.ties,
            report.count,
        )
        self.assertEqual(report.orchestrated_wins, 30)
        self.assertEqual(report.baseline_wins, 0)
        self.assertEqual(report.ties, 0)
        self.assertEqual(report.orchestrated.success_rate, 100.0)
        self.assertEqual(report.baseline.success_rate, 100.0)
        self.assertGreater(
            report.baseline.mean_last_action_ms
            - report.orchestrated.mean_last_action_ms,
            0,
        )
        self.assertTrue(
            all(pair.action_time_advantage_ms > 0 for pair in report.pairs)
        )
        self.assertGreater(
            report.orchestrated.mean_cost_units,
            report.baseline.mean_cost_units,
        )

    def test_formatter_optionally_includes_pair_rows(self) -> None:
        report = run_experiment(start_seed=40, count=2)

        summary = format_experiment(report, show_runs=False)
        detailed = format_experiment(report, show_runs=True)

        self.assertIn("EXPERIMENT MATRIX", summary)
        self.assertIn("BY VARIANT", summary)
        self.assertNotIn("PAIRED RUNS", summary)
        self.assertIn("PAIRED RUNS", detailed)
        for pair in report.pairs:
            self.assertIn(
                f"{pair.seed:>4}  {pair.variant:<31}",
                detailed,
            )


class MatrixParserTests(unittest.TestCase):
    def test_parser_rejects_zero_count(self) -> None:
        error_output = StringIO()
        with redirect_stderr(error_output):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["matrix", "--count", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("at least 1", error_output.getvalue())

    def test_parser_accepts_matrix_options_without_running_main(self) -> None:
        args = build_parser().parse_args(
            [
                "matrix",
                "--start-seed",
                "17",
                "--count",
                "4",
                "--variant",
                "regional_gateway_degradation",
                "--show-runs",
                "--json",
                "experiment.json",
            ]
        )

        self.assertEqual(args.command, "matrix")
        self.assertEqual(args.start_seed, 17)
        self.assertEqual(args.count, 4)
        self.assertEqual(args.variant, "regional_gateway_degradation")
        self.assertTrue(args.show_runs)
        self.assertEqual(args.json, "experiment.json")


if __name__ == "__main__":
    unittest.main()
