"""Static showcase contract, determinism, and CLI coverage."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from aurora_lab.cli import build_parser, main
from aurora_lab.showcase import (
    DEFAULT_SHOWCASE_OUTPUT,
    SHOWCASE_SCHEMA_VERSION,
    ShowcaseBundle,
    write_showcase_bundle,
)


class ShowcaseBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._base = Path.cwd() / f".showcase-tests-{id(cls)}"
        cls._base.mkdir()
        cls.output = cls._base / "nested" / "public" / "data"
        cls.bundle = write_showcase_bundle(cls.output, matrix_count=1)
        cls.manifest = json.loads(
            cls.bundle.manifest_path.read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._base)

    def _artifact(self, artifact_id: str) -> tuple[dict, dict]:
        descriptor = next(
            item
            for item in self.manifest["artifacts"]
            if item["id"] == artifact_id
        )
        payload = json.loads(
            (self.output / descriptor["path"]).read_text(encoding="utf-8")
        )
        return descriptor, payload

    def test_bundle_is_versioned_self_contained_and_timestamp_free(self) -> None:
        self.assertEqual(self.manifest["schema_version"], SHOWCASE_SCHEMA_VERSION)
        self.assertEqual(self.manifest["type"], "showcase_manifest")
        self.assertEqual(self.manifest["default_artifact_id"], "clean-full-orchestration")
        self.assertEqual(len(self.manifest["artifacts"]), 17)
        self.assertEqual(len(self.bundle.artifact_paths), 17)
        self.assertNotIn("generated_at", json.dumps(self.manifest).lower())

        for descriptor in self.manifest["artifacts"]:
            with self.subTest(artifact=descriptor["id"]):
                self.assertFalse(Path(descriptor["path"]).is_absolute())
                self.assertNotIn("\\", descriptor["path"])
                artifact_path = self.output / descriptor["path"]
                self.assertTrue(artifact_path.is_file())
                envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
                self.assertEqual(envelope["schema_version"], SHOWCASE_SCHEMA_VERSION)
                self.assertEqual(envelope["id"], descriptor["id"])
                self.assertEqual(envelope["type"], descriptor["type"])
                self.assertEqual(envelope["kind"], descriptor["kind"])
                self.assertIn("metadata", envelope)
                self.assertIn("data", envelope)

    def test_curated_runs_cover_controls_faults_and_expected_outcomes(self) -> None:
        expected = {
            "clean-full-orchestration": (None, "baseline", "recovered"),
            "planning-control-without-critic": (
                "planning_omission",
                "matched_control",
                "recovered",
            ),
            "planning-fault-without-critic": (
                "planning_omission",
                "fault",
                "safely_degraded",
            ),
            "planning-control-with-critic": (
                "planning_omission",
                "matched_control",
                "recovered",
            ),
            "planning-fault-with-critic": (
                "planning_omission",
                "fault",
                "recovered",
            ),
            "worker-control-without-fallback": (
                "permanent_worker_failure",
                "matched_control",
                "recovered",
            ),
            "worker-fault-without-fallback": (
                "permanent_worker_failure",
                "fault",
                "safely_degraded",
            ),
            "worker-control-with-fallback": (
                "permanent_worker_failure",
                "matched_control",
                "recovered",
            ),
            "worker-fault-with-fallback": (
                "permanent_worker_failure",
                "fault",
                "recovered",
            ),
            "budget-control-negative-control": (
                "fallback_budget_exhaustion",
                "matched_control",
                "safely_degraded",
            ),
            "budget-fault-negative-control": (
                "fallback_budget_exhaustion",
                "fault",
                "safely_degraded",
            ),
            "budget-control-with-fallback": (
                "fallback_budget_exhaustion",
                "matched_control",
                "recovered",
            ),
            "budget-fault-with-fallback": (
                "fallback_budget_exhaustion",
                "fault",
                "safely_degraded",
            ),
        }

        for artifact_id, (study, arm, outcome) in expected.items():
            with self.subTest(artifact=artifact_id):
                descriptor, payload = self._artifact(artifact_id)
                metadata = descriptor["metadata"]
                self.assertEqual(descriptor["type"], "run")
                self.assertEqual(metadata["seed"], 101)
                self.assertEqual(metadata["variant"], "bot_db_contention")
                self.assertEqual(metadata["study"], study)
                self.assertEqual(metadata["study_arm"], arm)
                self.assertEqual(metadata["expected_outcome"], outcome)
                self.assertEqual(
                    payload["data"]["status"],
                    "failed" if outcome == "safely_degraded" else "succeeded",
                )
                self.assertIn("trace", payload["data"])
                self.assertIn("metrics", payload)

        _, control = self._artifact("planning-control-with-critic")
        _, planning = self._artifact("planning-fault-with-critic")
        _, worker = self._artifact("worker-fault-with-fallback")
        control_kinds = {event["kind"] for event in control["data"]["trace"]}
        planning_kinds = {event["kind"] for event in planning["data"]["trace"]}
        worker_kinds = {event["kind"] for event in worker["data"]["trace"]}

        self.assertNotIn("fault_injected", control_kinds)
        self.assertTrue(
            {"fault_injected", "fault_detected", "fault_repaired"}
            <= planning_kinds
        )
        self.assertTrue(
            {
                "retry_exhausted",
                "task_reassigned",
                "fallback_completed",
                "fault_contained",
            }
            <= worker_kinds
        )

    def test_stories_and_relationships_are_ordered_and_discoverable(self) -> None:
        self.assertEqual(
            [story["id"] for story in self.manifest["stories"]],
            [
                "clean-orchestration",
                "planning-critic-contrast",
                "worker-fallback-contrast",
                "fallback-budget-contrast",
            ],
        )
        artifact_ids = {item["id"] for item in self.manifest["artifacts"]}
        relationship_ids = {
            item["id"] for item in self.manifest["relationships"]
        }
        for relationship in self.manifest["relationships"]:
            with self.subTest(relationship=relationship["id"]):
                self.assertIn(relationship["source_artifact_id"], artifact_ids)
                self.assertIn(relationship["target_artifact_id"], artifact_ids)
                self.assertIn(relationship["type"], {
                    "matched_control_for",
                    "mechanism_contrast",
                })
                self.assertIsInstance(relationship["metadata"], dict)
        for story in self.manifest["stories"]:
            self.assertLessEqual(set(story["artifact_ids"]), artifact_ids)
            self.assertLessEqual(set(story["relationship_ids"]), relationship_ids)

        # Future faults and events fit the generic type/kind/metadata boundary;
        # the manifest carries no closed fault or event-kind registry.
        self.assertNotIn("fault_names", self.manifest)
        self.assertNotIn("event_kinds", self.manifest)
        self.assertTrue(
            all(
                {"type", "kind", "metadata"} <= descriptor.keys()
                for descriptor in self.manifest["artifacts"]
            )
        )

    def test_every_story_has_a_plain_business_brief(self) -> None:
        brief_fields = {
            "business_context",
            "what_is_being_tested",
            "why_it_matters",
            "what_to_watch",
        }
        expected_terms = {
            "clean-orchestration": ("customer-facing incident", "approval"),
            "planning-critic-contrast": ("separate reviewer", "missing step"),
            "worker-fallback-contrast": ("designated backup", "worker loss"),
            "fallback-budget-contrast": ("full cost fits", "zero fallback spend"),
        }

        for story in self.manifest["stories"]:
            with self.subTest(story=story["id"]):
                metadata = story["metadata"]
                self.assertLessEqual(brief_fields, metadata.keys())
                for field in brief_fields:
                    self.assertIsInstance(metadata[field], str)
                    self.assertTrue(metadata[field].strip())
                    self.assertNotIn("_", metadata[field])
                rendered = " ".join(metadata[field] for field in brief_fields)
                for term in expected_terms[story["id"]]:
                    self.assertIn(term, rendered)

    def test_every_story_has_a_simple_walkthrough_and_build_rationale(self) -> None:
        explanation_fields = {
            "simple_explanation",
            "everyday_analogy",
            "business_value",
            "build_rationale",
        }

        for story in self.manifest["stories"]:
            with self.subTest(story=story["id"]):
                metadata = story["metadata"]
                self.assertLessEqual(explanation_fields, metadata.keys())
                for field in explanation_fields:
                    self.assertIsInstance(metadata[field], str)
                    self.assertGreater(len(metadata[field].strip()), 60)

                steps = metadata["exact_steps"]
                self.assertIsInstance(steps, list)
                self.assertEqual(len(steps), 4)
                for step in steps:
                    self.assertEqual(set(step), {"label", "explanation"})
                    self.assertIsInstance(step["label"], str)
                    self.assertIsInstance(step["explanation"], str)
                    self.assertTrue(step["label"].strip())
                    self.assertGreater(len(step["explanation"].strip()), 35)

    def test_matrix_reports_are_compact_but_keep_causal_effects(self) -> None:
        _, ablation = self._artifact("report-ablation")
        _, planning = self._artifact("report-planning-omission")
        _, worker = self._artifact("report-permanent-worker-failure")
        _, budget = self._artifact("report-fallback-budget-exhaustion")

        self.assertNotIn("runs", ablation["data"])
        self.assertIn("aggregates", ablation["data"])
        self.assertNotIn("pairs", planning["data"])
        self.assertIn("critic_effect", planning["data"])
        self.assertNotIn("pairs", worker["data"])
        self.assertIn("fallback_effect", worker["data"])
        self.assertEqual(worker["data"]["count"], 1)
        self.assertNotIn("pairs", budget["data"])
        self.assertEqual(
            budget["data"]["condition_order"],
            ["exact_fit_control", "fallback_budget_exhaustion"],
        )
        self.assertEqual(
            budget["data"]["preset_order"],
            ["specialists_with_critic", "specialists_with_fallback"],
        )
        self.assertIn("fallback_effect", budget["data"])
        self.assertEqual(
            budget["data"]["fallback_effect"][
                "tight_budget_with_fallback_zero_spend_rate_pct"
            ],
            100.0,
        )

    def test_budget_story_exposes_atomic_admission_and_zero_spend(self) -> None:
        negative_control_descriptor, negative_control = self._artifact(
            "budget-control-negative-control"
        )
        negative_fault_descriptor, negative_fault = self._artifact(
            "budget-fault-negative-control"
        )
        control_descriptor, exact_fit = self._artifact(
            "budget-control-with-fallback"
        )
        fault_descriptor, tight = self._artifact("budget-fault-with-fallback")

        for payload in (negative_control, negative_fault):
            self.assertEqual(payload["data"]["status"], "failed")
            self.assertEqual(payload["metrics"]["budget_reservations"], [])
            self.assertFalse(
                any(
                    event["kind"].startswith("budget_reservation_")
                    for event in payload["data"]["trace"]
                )
            )
        for descriptor in (
            negative_control_descriptor,
            negative_fault_descriptor,
            control_descriptor,
            fault_descriptor,
        ):
            self.assertTrue(
                descriptor["metadata"]["shared_worker_fault_injected"]
            )
        self.assertFalse(
            negative_fault_descriptor["metadata"]["study_intervention_injected"]
        )
        self.assertFalse(
            control_descriptor["metadata"]["study_intervention_injected"]
        )
        self.assertTrue(
            fault_descriptor["metadata"]["study_intervention_injected"]
        )

        exact_kinds = {event["kind"] for event in exact_fit["data"]["trace"]}
        tight_kinds = {event["kind"] for event in tight["data"]["trace"]}
        self.assertTrue(
            {"budget_reservation_requested", "budget_reservation_granted"}
            <= exact_kinds
        )
        self.assertTrue(
            {
                "budget_reservation_requested",
                "budget_reservation_denied",
                "fallback_skipped",
            }
            <= tight_kinds
        )
        self.assertNotIn("fallback_completed", tight_kinds)

        exact_budget = control_descriptor["metadata"]["scoped_budget"]
        tight_budget = fault_descriptor["metadata"]["scoped_budget"]
        self.assertEqual(exact_budget["decision"], "granted")
        self.assertEqual(exact_budget["requested_cost_units"], 4)
        self.assertEqual(exact_budget["capacity_cost_units"], 4)
        self.assertEqual(exact_budget["scoped_usage_cost_units"], 4)
        self.assertTrue(exact_budget["atomic"])
        self.assertTrue(exact_budget["budget_safe"])
        self.assertEqual(tight_budget["decision"], "denied")
        self.assertEqual(tight_budget["requested_cost_units"], 4)
        self.assertEqual(tight_budget["capacity_cost_units"], 3)
        self.assertEqual(tight_budget["scoped_usage_cost_units"], 0)
        self.assertTrue(tight_budget["skipped"])
        self.assertTrue(tight_budget["budget_safe"])

        skipped_task = next(
            task
            for task in tight["data"]["task_results"]
            if task["task_id"] == tight_budget["target_task_id"]
        )
        self.assertEqual(skipped_task["status"], "cancelled")
        self.assertEqual(skipped_task["attempts"], 0)
        self.assertEqual(skipped_task["cost_units"], 0)
        self.assertEqual(skipped_task["tool_calls"], 0)

        # The viewer consumes arbitrary string kinds and metadata pairs. New
        # budget events need no schema-version or event-registry change.
        for event in tight["data"]["trace"]:
            if event["kind"] in {
                "budget_reservation_requested",
                "budget_reservation_denied",
                "fallback_skipped",
            }:
                self.assertIsInstance(event["kind"], str)
                self.assertIsInstance(event["metadata"], list)

    def test_repeated_generation_is_byte_deterministic(self) -> None:
        second_root = self._base / "determinism"
        second_output = second_root / "data"
        try:
            write_showcase_bundle(second_output, matrix_count=1)
            first_files = {
                path.relative_to(self.output).as_posix(): path.read_bytes()
                for path in self.output.rglob("*.json")
            }
            second_files = {
                path.relative_to(second_output).as_posix(): path.read_bytes()
                for path in second_output.rglob("*.json")
            }
            self.assertEqual(first_files, second_files)
        finally:
            shutil.rmtree(second_root, ignore_errors=True)

    def test_argument_validation_and_file_collision(self) -> None:
        with self.assertRaisesRegex(TypeError, "matrix_count"):
            write_showcase_bundle(self.output, matrix_count="1")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "at least 1"):
            write_showcase_bundle(self.output, matrix_count=0)
        collision = self._base / "collision"
        try:
            collision.write_text("occupied", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                write_showcase_bundle(collision, matrix_count=1)
        finally:
            collision.unlink(missing_ok=True)


class ShowcaseCliTests(unittest.TestCase):
    def test_parser_defaults_to_dashboard_public_data(self) -> None:
        default = build_parser().parse_args(["showcase"])
        custom = build_parser().parse_args(
            ["showcase", "--output", "somewhere/showcase"]
        )

        self.assertEqual(default.output, str(DEFAULT_SHOWCASE_OUTPUT))
        self.assertEqual(custom.output, "somewhere/showcase")

    def test_main_dispatches_generator_and_prints_manifest(self) -> None:
        output = Path.cwd() / f".showcase-cli-{id(self)}" / "data"
        fake = ShowcaseBundle(
            output_dir=output.resolve(),
            manifest_path=(output / "manifest.json").resolve(),
            artifact_paths=((output / "runs/demo.json").resolve(),),
        )
        rendered = StringIO()
        with (
            patch("aurora_lab.cli.write_showcase_bundle", return_value=fake) as writer,
            redirect_stdout(rendered),
        ):
            status = main(["showcase", "--output", str(output)])

        self.assertEqual(status, 0)
        writer.assert_called_once_with(str(output))
        self.assertIn(str(fake.manifest_path), rendered.getvalue())
        self.assertIn("1 artifacts", rendered.getvalue())


if __name__ == "__main__":
    unittest.main()
