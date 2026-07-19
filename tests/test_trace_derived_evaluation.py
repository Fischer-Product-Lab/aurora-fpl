"""Focused tests for trace-derived scoring and evidence-bound approvals."""

from __future__ import annotations

from dataclasses import replace
import unittest

from aurora_lab.evaluation import evaluate
from aurora_lab.model import ActionProposal, RiskLevel
from aurora_lab.policies import HumanApprovalAuthority
from aurora_lab.runtime import EvidenceBoard, EventLog
from aurora_lab.scenario import ActionPolicy, build_scenario
from aurora_lab.simulation import ABLATION_PRESETS, run_orchestrated, run_preset


class TraceDerivedEvaluationTests(unittest.TestCase):
    def test_summary_metadata_cannot_forge_a_better_or_worse_score(self) -> None:
        scenario = build_scenario(101)
        result = run_orchestrated(101)
        forged = replace(
            result,
            metadata=(
                ("diagnosis_causes", "invented_cause"),
                ("diagnosis_evidence_ids", "E06"),
                ("outcome", "degraded"),
                ("policy_violations", "999"),
                ("valid_approvals", "false"),
                ("injection_denied", "false"),
                ("retry_recovered", "false"),
                ("critic_rejected", "false"),
                ("replans", "0"),
                ("useful_parallelism", "false"),
                ("cancelled_tasks", "0"),
                ("oversold_seats", "999"),
            ),
        )

        self.assertEqual(evaluate(forged, scenario), evaluate(result, scenario))

    def test_canonical_ablation_scores_follow_observed_mechanisms(self) -> None:
        expected = {
            "sequential_generalist": 95.5,
            "parallel_generalist": 97.5,
            "specialists_no_critic": 97.5,
            "specialists_with_critic": 99.0,
            "full_orchestration": 100.0,
        }

        for preset in ABLATION_PRESETS:
            with self.subTest(preset=preset.name):
                result = run_preset(preset.name, seed=101)
                self.assertIsNotNone(result.score)
                self.assertEqual(result.score.total, expected[preset.name])


class EvidenceBoundApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario(101)
        self.authority = HumanApprovalAuthority(seed=101)

    def _proposal(self, evidence_ids: tuple[str, ...]) -> ActionProposal:
        return ActionProposal(
            action_id="rollback_checkout_v214",
            summary="Rollback the faulty checkout release.",
            risk=RiskLevel.MEDIUM,
            evidence_ids=evidence_ids,
            rollback="Redeploy checkout v2.14.",
            idempotency_key=f"rollback:{','.join(evidence_ids)}",
        )

    def test_board_wide_tags_cannot_support_an_uncited_proposal(self) -> None:
        evidence = {
            item.evidence_id: item for item in self.scenario.evidence_catalog
        }
        board = EvidenceBoard((evidence["E00"], evidence["E03"]))
        policy = self.scenario.action_policy_for("rollback_checkout_v214")

        decision = self.authority.decide(
            self._proposal(("E00",)),
            policy,
            board,
            at_ms=10,
            state_version=0,
            review_accepted=True,
            log=EventLog(),
        )

        self.assertFalse(decision.approved)
        self.assertIn("cited trusted evidence", decision.reason)
        self.assertIn("retry_regression_confirmed", decision.reason)

    def test_cited_trusted_evidence_can_satisfy_required_tags(self) -> None:
        evidence = {
            item.evidence_id: item for item in self.scenario.evidence_catalog
        }
        board = EvidenceBoard((evidence["E03"],))
        policy = self.scenario.action_policy_for("rollback_checkout_v214")

        decision = self.authority.decide(
            self._proposal(("E03",)),
            policy,
            board,
            at_ms=10,
            state_version=0,
            review_accepted=True,
            log=EventLog(),
        )

        self.assertTrue(decision.approved)
        self.assertIsNotNone(decision.capability)

    def test_untrusted_cited_evidence_cannot_supply_required_tags(self) -> None:
        injection = next(
            item for item in self.scenario.evidence_catalog if item.evidence_id == "E06"
        )
        board = EvidenceBoard((injection,))
        policy = ActionPolicy(
            action_id="rollback_checkout_v214",
            summary="Test an evidence boundary.",
            risk=RiskLevel.MEDIUM,
            required_evidence_tags=frozenset({"prompt_injection_present"}),
            approvers=("incident_commander",),
        )

        decision = self.authority.decide(
            self._proposal(("E06",)),
            policy,
            board,
            at_ms=10,
            state_version=0,
            review_accepted=True,
            log=EventLog(),
        )

        self.assertFalse(decision.approved)
        self.assertIn("prompt_injection_present", decision.reason)


if __name__ == "__main__":
    unittest.main()
