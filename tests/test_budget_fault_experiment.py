"""Acceptance coverage for the fallback-budget exhaustion study."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import unittest

from aurora_lab.cli import build_parser, main
from aurora_lab.experiments import (
    BUDGET_FAULT_PRESET_NAMES,
    BudgetFaultArmMeasurement,
    BudgetFaultExperimentReport,
    BudgetFaultStrategyAggregate,
    run_budget_fault_experiment,
    run_fault_experiment,
)
from aurora_lab.faults import FALLBACK_BUDGET_EXHAUSTION
from aurora_lab.metrics import derive_run_metrics
from aurora_lab.model import EventKind
from aurora_lab.reporting import format_fault_experiment, to_primitive
from aurora_lab.simulation import run_preset


BUDGET_FAULT = FALLBACK_BUDGET_EXHAUSTION.name


class BudgetFaultExperimentTests(unittest.TestCase):
    def test_matrix_separates_shared_worker_loss_from_budget_intervention(self) -> None:
        report = run_fault_experiment(
            start_seed=101,
            count=3,
            fault_name=BUDGET_FAULT,
        )

        self.assertIsInstance(report, BudgetFaultExperimentReport)
        self.assertEqual(report.preset_order, BUDGET_FAULT_PRESET_NAMES)
        self.assertEqual(
            report.condition_order,
            ("exact_fit_control", BUDGET_FAULT),
        )
        self.assertEqual(len(report.pairs), 3 * len(BUDGET_FAULT_PRESET_NAMES))
        self.assertIsNone(report.critic_effect)
        self.assertTrue(
            all(
                isinstance(pair.control, BudgetFaultArmMeasurement)
                and isinstance(pair.faulted, BudgetFaultArmMeasurement)
                for pair in report.pairs
            )
        )
        self.assertEqual(
            tuple((pair.seed, pair.strategy) for pair in report.pairs),
            tuple(
                (seed, strategy)
                for seed in (101, 102, 103)
                for strategy in BUDGET_FAULT_PRESET_NAMES
            ),
        )

        for pair in report.pairs:
            with self.subTest(seed=pair.seed, strategy=pair.strategy):
                for arm in (pair.control, pair.faulted):
                    self.assertTrue(arm.shared_worker_fault_injected)
                    self.assertTrue(arm.shared_worker_fault_detected)
                    self.assertTrue(arm.shared_worker_terminal_failure)
                    self.assertTrue(arm.policy_safe)
                    self.assertTrue(arm.budget_safe)
                    self.assertTrue(arm.zero_budget_overrun)
                    self.assertEqual(arm.invalid_reservation_count, 0)
                    self.assertEqual(arm.budget_overrun_attempts, 0)
                    self.assertEqual(arm.budget_overrun_cost_units, 0)
                    self.assertEqual(arm.budget_overrun_tool_calls, 0)
                    self.assertEqual(arm.budget_overrun_duration_ms, 0)

                if pair.strategy == "specialists_with_critic":
                    self.assertFalse(pair.control.recovered)
                    self.assertFalse(pair.faulted.recovered)
                    self.assertTrue(pair.control.safe_degraded)
                    self.assertTrue(pair.faulted.safe_degraded)
                    self.assertEqual(pair.control.reservation_request_count, 0)
                    self.assertEqual(pair.faulted.reservation_request_count, 0)
                    self.assertFalse(pair.control.study_fault_injected)
                    self.assertFalse(pair.faulted.study_fault_injected)
                    self.assertEqual(pair.control.attempts_used, pair.faulted.attempts_used)
                    self.assertEqual(pair.control.cost_units, pair.faulted.cost_units)
                    self.assertEqual(pair.control.tool_calls, pair.faulted.tool_calls)
                    self.assertEqual(pair.control.total_ms, pair.faulted.total_ms)
                    continue

                self.assertTrue(pair.control.recovered)
                self.assertTrue(pair.control.fallback_succeeded)
                self.assertTrue(pair.control.fault_contained)
                self.assertFalse(pair.control.safe_degraded)
                self.assertEqual(pair.control.reservation_request_count, 1)
                self.assertEqual(pair.control.reservation_grant_count, 1)
                self.assertEqual(pair.control.reservation_denial_count, 0)
                self.assertEqual(pair.control.fallback_skip_count, 0)
                self.assertEqual(pair.control.requested_cost_units, 4)
                self.assertEqual(pair.control.capacity_cost_units, 4)
                self.assertEqual(pair.control.scoped_usage_cost_units, 4)
                self.assertFalse(pair.control.study_fault_injected)

                self.assertFalse(pair.faulted.recovered)
                self.assertFalse(pair.faulted.fallback_succeeded)
                self.assertFalse(pair.faulted.fault_contained)
                self.assertTrue(pair.faulted.safe_degraded)
                self.assertEqual(pair.faulted.reservation_request_count, 1)
                self.assertEqual(pair.faulted.reservation_grant_count, 0)
                self.assertEqual(pair.faulted.reservation_denial_count, 1)
                self.assertEqual(pair.faulted.fallback_skip_count, 1)
                self.assertEqual(pair.faulted.requested_cost_units, 4)
                self.assertEqual(pair.faulted.capacity_cost_units, 3)
                self.assertEqual(pair.faulted.scoped_usage_cost_units, 0)
                self.assertEqual(pair.faulted.binding_dimensions, ("cost_units",))
                self.assertTrue(pair.faulted.study_fault_injected)
                self.assertTrue(pair.faulted.study_fault_detected)
                self.assertTrue(pair.faulted.denied_zero_spend)
                self.assertTrue(pair.faulted.denied_no_dispatch)
                self.assertEqual(
                    pair.faulted.attempts_used - pair.control.attempts_used,
                    -1,
                )
                self.assertEqual(pair.faulted.cost_units - pair.control.cost_units, -4)
                self.assertEqual(pair.faulted.tool_calls - pair.control.tool_calls, -1)

    def test_aggregates_and_difference_in_differences_are_explicit(self) -> None:
        report = run_budget_fault_experiment(start_seed=101, count=3)
        self.assertTrue(
            all(
                isinstance(item, BudgetFaultStrategyAggregate)
                for item in report.aggregates
            )
        )
        by_strategy = {item.strategy: item for item in report.aggregates}
        negative = by_strategy["specialists_with_critic"]
        treatment = by_strategy["specialists_with_fallback"]

        self.assertEqual(negative.control_recovery_rate_pct, 0.0)
        self.assertEqual(negative.fault_recovery_rate_pct, 0.0)
        self.assertEqual(negative.fault_minus_control_mean_cost_units, 0.0)
        self.assertEqual(negative.fault_minus_control_mean_tool_calls, 0.0)
        self.assertEqual(negative.fault_study_injection_rate_pct, 0.0)

        self.assertEqual(treatment.control_recovery_rate_pct, 100.0)
        self.assertEqual(treatment.fault_recovery_rate_pct, 0.0)
        self.assertEqual(treatment.fault_minus_control_recovery_rate_pp, -100.0)
        self.assertEqual(treatment.control_mean_reservation_grant_count, 1.0)
        self.assertEqual(treatment.fault_mean_reservation_denial_count, 1.0)
        self.assertEqual(treatment.fault_mean_fallback_skip_count, 1.0)
        self.assertEqual(treatment.control_mean_capacity_cost_units, 4.0)
        self.assertEqual(treatment.fault_mean_capacity_cost_units, 3.0)
        self.assertEqual(treatment.fault_minus_control_mean_attempts_used, -1.0)
        self.assertEqual(treatment.fault_minus_control_mean_cost_units, -4.0)
        self.assertEqual(treatment.fault_minus_control_mean_tool_calls, -1.0)
        self.assertEqual(treatment.fault_budget_safe_rate_pct, 100.0)
        self.assertEqual(treatment.fault_zero_budget_overrun_rate_pct, 100.0)

        effect = report.fallback_effect
        self.assertEqual(effect.exact_fit_fallback_benefit_pp, 100.0)
        self.assertEqual(effect.tight_budget_fallback_benefit_pp, 0.0)
        self.assertEqual(
            effect.fallback_benefit_difference_in_differences_pp,
            -100.0,
        )
        self.assertEqual(effect.exact_fit_rescues, 3)
        self.assertEqual(effect.tight_budget_rescues, 0)
        self.assertEqual(effect.lost_rescues, 3)
        self.assertEqual(effect.exact_fit_with_fallback_grant_rate_pct, 100.0)
        self.assertEqual(effect.tight_budget_with_fallback_denial_rate_pct, 100.0)
        self.assertEqual(effect.tight_budget_with_fallback_skip_rate_pct, 100.0)
        self.assertEqual(effect.tight_budget_with_fallback_zero_spend_rate_pct, 100.0)
        self.assertEqual(effect.tight_budget_with_fallback_no_dispatch_rate_pct, 100.0)
        self.assertEqual(
            effect.tight_minus_exact_without_fallback_mean_cost_units,
            0.0,
        )
        self.assertEqual(effect.tight_minus_exact_with_fallback_mean_cost_units, -4.0)

    def test_budget_safety_rejects_tampered_grant_and_denial_ledgers(self) -> None:
        control = run_preset(
            "specialists_with_fallback",
            101,
            "bot_db_contention",
            control_for_fault=BUDGET_FAULT,
        )
        grant = next(
            event
            for event in control.trace
            if event.kind is EventKind.BUDGET_RESERVATION_GRANTED
        )
        bad_grant = replace(
            grant,
            metadata=tuple(
                (key, "0" if key == "scoped_usage_cost_units" else value)
                for key, value in grant.metadata
            ),
        )
        grant_result = replace(
            control,
            trace=tuple(
                bad_grant if event.sequence == grant.sequence else event
                for event in control.trace
            ),
        )
        grant_fact = derive_run_metrics(grant_result).budget_reservations[0]
        self.assertFalse(grant_fact.valid)
        self.assertFalse(grant_fact.budget_safe)

        faulted = run_preset(
            "specialists_with_fallback",
            101,
            "bot_db_contention",
            BUDGET_FAULT,
        )
        denial = next(
            event
            for event in faulted.trace
            if event.kind is EventKind.BUDGET_RESERVATION_DENIED
        )
        bad_denial = replace(
            denial,
            metadata=tuple(
                (key, "999" if key == "ledger_after_cost_units" else value)
                for key, value in denial.metadata
            ),
        )
        denial_result = replace(
            faulted,
            trace=tuple(
                bad_denial if event.sequence == denial.sequence else event
                for event in faulted.trace
            ),
        )
        denial_fact = derive_run_metrics(denial_result).budget_reservations[0]
        self.assertFalse(denial_fact.ledger_unchanged)
        self.assertFalse(denial_fact.budget_safe)

    def test_report_json_and_cli_explain_atomic_safe_degradation(self) -> None:
        report = run_budget_fault_experiment(start_seed=101, count=1)
        rendered = format_fault_experiment(report, show_runs=True)
        payload = to_primitive(report)

        self.assertIn("FALLBACK BUDGET MATRIX", rendered)
        self.assertIn("same permanently failed worker", rendered)
        self.assertIn("exact-fit 4 units to 3 units", rendered)
        self.assertIn("ISOLATED FALLBACK BENEFIT UNDER BUDGET PRESSURE", rendered)
        self.assertIn("+100.0pp benefit", rendered)
        self.assertIn("-100.0pp", rendered)
        self.assertIn("zero spend 100.0%", rendered)
        self.assertIn("no fallback dispatch 100.0%", rendered)
        self.assertIn("PAIRED FALLBACK-BUDGET RUNS", rendered)
        self.assertNotIn("FAULT-ARM TRACE QUALITY", rendered)
        self.assertEqual(payload["critic_effect"], None)
        self.assertEqual(payload["condition_order"][0], "exact_fit_control")
        self.assertEqual(
            payload["fallback_effect"][
                "fallback_benefit_difference_in_differences_pp"
            ],
            -100.0,
        )
        fallback_pair = payload["pairs"][1]
        self.assertTrue(fallback_pair["faulted"]["budget_safe"])
        self.assertTrue(fallback_pair["faulted"]["denied_zero_spend"])
        self.assertEqual(fallback_pair["control"]["capacity_cost_units"], 4)
        self.assertEqual(fallback_pair["faulted"]["capacity_cost_units"], 3)

        parser = build_parser()
        matrix = parser.parse_args(
            ["fault-matrix", "--fault", BUDGET_FAULT, "--count", "1"]
        )
        direct = parser.parse_args(
            [
                "run",
                "--strategy",
                "specialists_with_fallback",
                "--fault",
                BUDGET_FAULT,
            ]
        )
        self.assertEqual(matrix.fault, BUDGET_FAULT)
        self.assertEqual(direct.fault, BUDGET_FAULT)

        output = StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "run",
                    "--seed",
                    "101",
                    "--variant",
                    "bot_db_contention",
                    "--strategy",
                    "specialists_with_fallback",
                    "--fault",
                    BUDGET_FAULT,
                    "--trace",
                    "summary",
                ]
            )
        self.assertEqual(status, 1)
        self.assertIn("budget_reservation_requested", output.getvalue())
        self.assertIn("budget_reservation_denied", output.getvalue())
        self.assertIn("fallback_skipped", output.getvalue())

    def test_budget_report_is_deterministic(self) -> None:
        first = run_budget_fault_experiment(start_seed=30, count=2)
        second = run_budget_fault_experiment(start_seed=30, count=2)

        self.assertEqual(first, second)
        self.assertEqual(to_primitive(first), to_primitive(second))


if __name__ == "__main__":
    unittest.main()
