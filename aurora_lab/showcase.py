"""Generate deterministic, static artifacts for the Aurora Run Explorer.

The bundle is intentionally presentation-framework agnostic.  A browser can
discover runs, reports, stories, and relationships from ``manifest.json``
without knowing Aurora's current fault names or event vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .experiments import run_ablation_experiment, run_fault_experiment
from .faults import (
    FALLBACK_BUDGET_EXHAUSTION,
    PERMANENT_WORKER_FAILURE,
    PLANNING_OMISSION,
)
from .metrics import derive_run_metrics
from .reporting import to_primitive
from .simulation import run_preset


SHOWCASE_SCHEMA_VERSION = "1.0.0"
DEFAULT_SHOWCASE_OUTPUT = Path("dashboard/public/data")
DEFAULT_MATRIX_START_SEED = 0
DEFAULT_MATRIX_COUNT = 30
CURATED_SEED = 101
CURATED_VARIANT = "bot_db_contention"


@dataclass(frozen=True, slots=True)
class ShowcaseBundle:
    """Filesystem locations produced by :func:`write_showcase_bundle`."""

    output_dir: Path
    manifest_path: Path
    artifact_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _CuratedRun:
    artifact_id: str
    title: str
    summary: str
    strategy: str
    study: str | None
    study_arm: str
    expected_outcome: str
    tags: tuple[str, ...]
    extra_metadata: tuple[tuple[str, Any], ...] = ()

    @property
    def fault_active(self) -> bool:
        return self.study is not None and self.study_arm == "fault"


_CURATED_RUNS = (
    _CuratedRun(
        artifact_id="clean-full-orchestration",
        title="Clean full orchestration",
        summary=(
            "A healthy reference run with parallel specialists, independent "
            "review, governed execution, and verified recovery."
        ),
        strategy="full_orchestration",
        study=None,
        study_arm="baseline",
        expected_outcome="recovered",
        tags=("clean", "reference", "orchestration"),
    ),
    _CuratedRun(
        artifact_id="planning-control-without-critic",
        title="Planning control without critic",
        summary=(
            "The identity-control candidate plan remains complete even though "
            "the specialist team has no independent critic."
        ),
        strategy="specialists_no_critic",
        study=PLANNING_OMISSION.name,
        study_arm="matched_control",
        expected_outcome="recovered",
        tags=("control", "planning", "no-critic"),
    ),
    _CuratedRun(
        artifact_id="planning-fault-without-critic",
        title="Planning omission without critic",
        summary=(
            "A causal mitigation is omitted and self-review lets the incomplete "
            "plan proceed to a safely degraded outcome."
        ),
        strategy="specialists_no_critic",
        study=PLANNING_OMISSION.name,
        study_arm="fault",
        expected_outcome="safely_degraded",
        tags=("fault", "planning", "no-critic", "degraded"),
    ),
    _CuratedRun(
        artifact_id="planning-control-with-critic",
        title="Planning control with critic",
        summary=(
            "The matched identity control shows that independent review is "
            "dormant when the candidate plan is already complete."
        ),
        strategy="specialists_with_critic",
        study=PLANNING_OMISSION.name,
        study_arm="matched_control",
        expected_outcome="recovered",
        tags=("control", "planning", "critic"),
    ),
    _CuratedRun(
        artifact_id="planning-fault-with-critic",
        title="Planning omission caught by critic",
        summary=(
            "Independent review rejects the incomplete plan, drives one replan, "
            "and verifies repair of the planning fault."
        ),
        strategy="specialists_with_critic",
        study=PLANNING_OMISSION.name,
        study_arm="fault",
        expected_outcome="recovered",
        tags=("fault", "planning", "critic", "repaired"),
    ),
    _CuratedRun(
        artifact_id="worker-control-without-fallback",
        title="Worker control without fallback",
        summary=(
            "The target investigator succeeds on its bounded retry; no fallback "
            "capability is needed."
        ),
        strategy="specialists_with_critic",
        study=PERMANENT_WORKER_FAILURE.name,
        study_arm="matched_control",
        expected_outcome="recovered",
        tags=("control", "worker", "no-fallback"),
    ),
    _CuratedRun(
        artifact_id="worker-fault-without-fallback",
        title="Permanent worker failure without fallback",
        summary=(
            "The target retry fails terminally, required approval evidence is "
            "missing, and the system stops safely."
        ),
        strategy="specialists_with_critic",
        study=PERMANENT_WORKER_FAILURE.name,
        study_arm="fault",
        expected_outcome="safely_degraded",
        tags=("fault", "worker", "no-fallback", "degraded"),
    ),
    _CuratedRun(
        artifact_id="worker-control-with-fallback",
        title="Worker control with fallback enabled",
        summary=(
            "The target investigator succeeds normally, showing that the "
            "fallback toggle has no control-arm recovery effect."
        ),
        strategy="specialists_with_fallback",
        study=PERMANENT_WORKER_FAILURE.name,
        study_arm="matched_control",
        expected_outcome="recovered",
        tags=("control", "worker", "fallback"),
    ),
    _CuratedRun(
        artifact_id="worker-fault-with-fallback",
        title="Permanent worker failure with bounded fallback",
        summary=(
            "A distinct generalist is assigned once, restores linked evidence, "
            "and contains the fault while the original worker remains failed."
        ),
        strategy="specialists_with_fallback",
        study=PERMANENT_WORKER_FAILURE.name,
        study_arm="fault",
        expected_outcome="recovered",
        tags=("fault", "worker", "fallback", "contained"),
    ),
    _CuratedRun(
        artifact_id="budget-control-negative-control",
        title="Exact-fit capacity without fallback",
        summary=(
            "The critic-only negative control has no fallback to admit and "
            "degrades safely under the shared permanent worker failure."
        ),
        strategy="specialists_with_critic",
        study=FALLBACK_BUDGET_EXHAUSTION.name,
        study_arm="matched_control",
        expected_outcome="safely_degraded",
        tags=("control", "budget", "worker", "negative-control"),
        extra_metadata=(
            ("budget_condition", "exact_fit"),
            ("fallback_enabled", False),
            ("negative_control", True),
            ("shared_condition", PERMANENT_WORKER_FAILURE.name),
        ),
    ),
    _CuratedRun(
        artifact_id="budget-fault-negative-control",
        title="Tight capacity without fallback",
        summary=(
            "The capacity intervention remains dormant when reassignment is "
            "disabled, preserving the same safe degradation."
        ),
        strategy="specialists_with_critic",
        study=FALLBACK_BUDGET_EXHAUSTION.name,
        study_arm="fault",
        expected_outcome="safely_degraded",
        tags=("fault", "budget", "worker", "negative-control"),
        extra_metadata=(
            ("budget_condition", "tight_capacity"),
            ("fallback_enabled", False),
            ("negative_control", True),
            ("shared_condition", PERMANENT_WORKER_FAILURE.name),
        ),
    ),
    _CuratedRun(
        artifact_id="budget-control-with-fallback",
        title="Exact-fit fallback reservation",
        summary=(
            "The scoped ledger atomically admits an exact-fit fallback request, "
            "allowing reassignment and verified containment."
        ),
        strategy="specialists_with_fallback",
        study=FALLBACK_BUDGET_EXHAUSTION.name,
        study_arm="matched_control",
        expected_outcome="recovered",
        tags=("control", "budget", "worker", "fallback", "exact-fit"),
        extra_metadata=(
            ("budget_condition", "exact_fit"),
            ("fallback_enabled", True),
            ("negative_control", False),
            ("shared_condition", PERMANENT_WORKER_FAILURE.name),
        ),
    ),
    _CuratedRun(
        artifact_id="budget-fault-with-fallback",
        title="Tight capacity refuses fallback",
        summary=(
            "One missing cost unit causes atomic admission denial, zero fallback "
            "spend, and the same auditable safe-degradation path."
        ),
        strategy="specialists_with_fallback",
        study=FALLBACK_BUDGET_EXHAUSTION.name,
        study_arm="fault",
        expected_outcome="safely_degraded",
        tags=("fault", "budget", "worker", "fallback", "zero-spend"),
        extra_metadata=(
            ("budget_condition", "tight_capacity"),
            ("fallback_enabled", True),
            ("negative_control", False),
            ("shared_condition", PERMANENT_WORKER_FAILURE.name),
        ),
    ),
)


_RELATIONSHIPS: tuple[dict[str, Any], ...] = (
    {
        "id": "pair-planning-without-critic",
        "type": "matched_control_for",
        "source_artifact_id": "planning-control-without-critic",
        "target_artifact_id": "planning-fault-without-critic",
        "metadata": {"mechanism": "independent_critic", "enabled": False},
    },
    {
        "id": "pair-planning-with-critic",
        "type": "matched_control_for",
        "source_artifact_id": "planning-control-with-critic",
        "target_artifact_id": "planning-fault-with-critic",
        "metadata": {"mechanism": "independent_critic", "enabled": True},
    },
    {
        "id": "contrast-planning-critic",
        "type": "mechanism_contrast",
        "source_artifact_id": "planning-fault-without-critic",
        "target_artifact_id": "planning-fault-with-critic",
        "metadata": {
            "mechanism": "independent_critic",
            "source_enabled": False,
            "target_enabled": True,
        },
    },
    {
        "id": "pair-worker-without-fallback",
        "type": "matched_control_for",
        "source_artifact_id": "worker-control-without-fallback",
        "target_artifact_id": "worker-fault-without-fallback",
        "metadata": {"mechanism": "failure_reassignment", "enabled": False},
    },
    {
        "id": "pair-worker-with-fallback",
        "type": "matched_control_for",
        "source_artifact_id": "worker-control-with-fallback",
        "target_artifact_id": "worker-fault-with-fallback",
        "metadata": {"mechanism": "failure_reassignment", "enabled": True},
    },
    {
        "id": "contrast-worker-fallback",
        "type": "mechanism_contrast",
        "source_artifact_id": "worker-fault-without-fallback",
        "target_artifact_id": "worker-fault-with-fallback",
        "metadata": {
            "mechanism": "failure_reassignment",
            "source_enabled": False,
            "target_enabled": True,
        },
    },
    {
        "id": "pair-budget-negative-control",
        "type": "matched_control_for",
        "source_artifact_id": "budget-control-negative-control",
        "target_artifact_id": "budget-fault-negative-control",
        "metadata": {
            "mechanism": "scoped_fallback_capacity",
            "fallback_enabled": False,
        },
    },
    {
        "id": "pair-budget-with-fallback",
        "type": "matched_control_for",
        "source_artifact_id": "budget-control-with-fallback",
        "target_artifact_id": "budget-fault-with-fallback",
        "metadata": {
            "mechanism": "scoped_fallback_capacity",
            "fallback_enabled": True,
        },
    },
    {
        "id": "contrast-budget-exact-fit-fallback",
        "type": "mechanism_contrast",
        "source_artifact_id": "budget-control-negative-control",
        "target_artifact_id": "budget-control-with-fallback",
        "metadata": {
            "budget_condition": "exact_fit",
            "mechanism": "failure_reassignment",
            "source_enabled": False,
            "target_enabled": True,
        },
    },
    {
        "id": "contrast-budget-tight-fallback",
        "type": "mechanism_contrast",
        "source_artifact_id": "budget-fault-negative-control",
        "target_artifact_id": "budget-fault-with-fallback",
        "metadata": {
            "budget_condition": "tight_capacity",
            "mechanism": "failure_reassignment",
            "source_enabled": False,
            "target_enabled": True,
        },
    },
)


_STORIES: tuple[dict[str, Any], ...] = (
    {
        "id": "clean-orchestration",
        "kind": "single_run",
        "title": "How a coordinated incident response should work",
        "summary": (
            "Follow parallel investigations, one approved response, and two "
            "health checks that confirm recovery."
        ),
        "artifact_ids": ("clean-full-orchestration", "report-ablation"),
        "relationship_ids": (),
        "metadata": {
            "focus": "orchestration_baseline",
            "business_context": (
                "A customer-facing incident can involve several business "
                "systems at once. The response team needs one coordinated "
                "view before it changes anything."
            ),
            "what_is_being_tested": (
                "Whether several specialist agents can investigate in "
                "parallel, combine their findings, obtain approval, take "
                "action, and confirm recovery."
            ),
            "why_it_matters": (
                "Fast coordination is useful only when decisions remain "
                "traceable and the business can see that the fix worked."
            ),
            "what_to_watch": (
                "Watch the specialist tasks finish in parallel, evidence feed "
                "the decision, approval come before action, and verification "
                "close the incident."
            ),
            "simple_explanation": (
                "Several software helpers investigate a checkout and payment "
                "problem at the same time. A coordinator joins their findings, "
                "checks the plan, runs only approved fixes, and confirms that "
                "the service recovered."
            ),
            "everyday_analogy": (
                "It is like an emergency team where scouts report what they "
                "see, a supervisor checks the plan, and an inspector confirms "
                "the repair worked."
            ),
            "exact_steps": (
                {
                    "label": "Split the search",
                    "explanation": (
                        "Four helpers check service data, the latest software "
                        "release, payment records, and signs of abuse; an "
                        "unnecessary wider search is cancelled."
                    ),
                },
                {
                    "label": "Check the plan",
                    "explanation": (
                        "A reviewer finds that the first plan misses part of "
                        "the problem, so the coordinator adds the missing fixes."
                    ),
                },
                {
                    "label": "Allow safe actions",
                    "explanation": (
                        "Two unsafe suggestions are refused, while four "
                        "carefully limited fixes are approved and performed in "
                        "order."
                    ),
                },
                {
                    "label": "Prove recovery",
                    "explanation": (
                        "Two later health checks confirm that checkout and "
                        "payments are working and that no extra inventory was "
                        "sold."
                    ),
                },
            ),
            "business_value": (
                "This approach shortens the investigation while keeping "
                "important safeguards in place. It reduces guesswork, blocks "
                "unsafe changes, and prevents the team from declaring victory "
                "too early."
            ),
            "build_rationale": (
                "We built this first to show the complete, desired process "
                "from problem to proven recovery. It gives every later "
                "demonstration a clear reference point."
            ),
        },
    },
    {
        "id": "planning-critic-contrast",
        "kind": "matched_mechanism_contrast",
        "title": "Can an independent reviewer catch a missing step?",
        "summary": (
            "Compare the response with the reviewer off and on, both with a "
            "complete plan and with one step removed."
        ),
        "artifact_ids": (
            "planning-control-without-critic",
            "planning-fault-without-critic",
            "planning-control-with-critic",
            "planning-fault-with-critic",
            "report-planning-omission",
        ),
        "relationship_ids": (
            "pair-planning-without-critic",
            "pair-planning-with-critic",
            "contrast-planning-critic",
        ),
        "metadata": {
            "study": PLANNING_OMISSION.name,
            "mechanism": "independent_critic",
            "business_context": (
                "A response plan can look complete while leaving out one "
                "action needed to fix the underlying problem."
            ),
            "what_is_being_tested": (
                "Whether a separate reviewer catches the missing step before "
                "execution. Here, an independent critic is simply a second "
                "agent reviewing a plan it did not write."
            ),
            "why_it_matters": (
                "A missed step can turn a confident response into an "
                "incomplete fix. Independent review should improve reliability "
                "without slowing down plans that are already correct."
            ),
            "what_to_watch": (
                "Compare the same omission with and without the reviewer. The "
                "reviewer rejects the incomplete plan once, prompts a "
                "correction, and stays quiet when the plan is already complete."
            ),
            "simple_explanation": (
                "One version of the response plan is complete, while another "
                "is missing an important fix. We run both versions with and "
                "without a separate reviewer to see whether the reviewer "
                "catches the mistake."
            ),
            "everyday_analogy": (
                "It is like asking a second person to check that every "
                "ingredient is on the shopping list before cooking begins."
            ),
            "exact_steps": (
                {
                    "label": "Create matching cases",
                    "explanation": (
                        "The same incident receives one complete plan and one "
                        "plan with a necessary step removed."
                    ),
                },
                {
                    "label": "Try no reviewer",
                    "explanation": (
                        "Without a second check, the incomplete plan moves "
                        "forward and the service does not fully recover."
                    ),
                },
                {
                    "label": "Add one reviewer",
                    "explanation": (
                        "A separate helper rejects the incomplete plan once "
                        "and asks the coordinator to repair it."
                    ),
                },
                {
                    "label": "Compare the results",
                    "explanation": (
                        "The repaired plan restores service, while an already "
                        "complete plan continues without unnecessary delay."
                    ),
                },
            ),
            "business_value": (
                "A second set of eyes can prevent a confident but incomplete "
                "response from reaching customers. The comparison also shows "
                "that a good plan passes review without an unnecessary "
                "rejection or rewrite."
            ),
            "build_rationale": (
                "We keep the incident the same and change only whether review "
                "is present. This makes it clear that the better result comes "
                "from the independent check, not from easier circumstances."
            ),
        },
    },
    {
        "id": "worker-fallback-contrast",
        "kind": "matched_mechanism_contrast",
        "title": (
            "Can a backup agent keep the response moving when a specialist "
            "fails?"
        ),
        "summary": (
            "Compare the same specialist outage with backup reassignment off "
            "and on."
        ),
        "artifact_ids": (
            "worker-control-without-fallback",
            "worker-fault-without-fallback",
            "worker-control-with-fallback",
            "worker-fault-with-fallback",
            "report-permanent-worker-failure",
        ),
        "relationship_ids": (
            "pair-worker-without-fallback",
            "pair-worker-with-fallback",
            "contrast-worker-fallback",
        ),
        "metadata": {
            "study": PERMANENT_WORKER_FAILURE.name,
            "mechanism": "failure_reassignment",
            "business_context": (
                "During an incident, the specialist responsible for a "
                "critical investigation may remain unavailable even after a "
                "retry."
            ),
            "what_is_being_tested": (
                "Whether one limited reassignment to a generalist can replace "
                "the missing evidence. Here, fallback means a designated "
                "backup gets one controlled attempt."
            ),
            "why_it_matters": (
                "The response should keep moving when one specialist is lost, "
                "but it must not hide the failure or allow unlimited retries."
            ),
            "what_to_watch": (
                "Compare the same worker loss with fallback off and on. With "
                "fallback on, the original worker remains failed while the "
                "backup restores the evidence needed for a verified recovery."
            ),
            "simple_explanation": (
                "The helper checking the service's warning signs becomes "
                "unavailable and still fails when tried again. We compare "
                "continuing without that missing information with giving one "
                "backup helper a tightly limited chance to finish that "
                "investigation."
            ),
            "everyday_analogy": (
                "It is like a cross-trained coworker covering one urgent "
                "assignment when the usual specialist is unexpectedly absent."
            ),
            "exact_steps": (
                {
                    "label": "Lose the specialist",
                    "explanation": (
                        "The monitoring helper fails on the first attempt and "
                        "on the one allowed retry."
                    ),
                },
                {
                    "label": "Show no backup",
                    "explanation": (
                        "With backup turned off, the team lacks an important "
                        "piece of evidence and cannot fully recover the service."
                    ),
                },
                {
                    "label": "Allow one handoff",
                    "explanation": (
                        "With backup turned on, a general helper gets one "
                        "controlled attempt to collect the missing evidence."
                    ),
                },
                {
                    "label": "Finish and verify",
                    "explanation": (
                        "The coordinator uses that replacement evidence, "
                        "completes the approved response, and confirms recovery "
                        "while still showing the original failure."
                    ),
                },
            ),
            "business_value": (
                "The business can keep responding when one specialist is "
                "unavailable without hiding the problem or retrying forever. "
                "This creates resilience with a clear limit."
            ),
            "build_rationale": (
                "We run the same specialist failure with backup turned off and "
                "on. That direct comparison shows the value of reassignment "
                "while proving that the backup remains limited and visible."
            ),
        },
    },
    {
        "id": "fallback-budget-contrast",
        "kind": "factorial_capacity_contrast",
        "title": (
            "Will the system stop safely when backup work exceeds the limit?"
        ),
        "summary": (
            "Compare a backup request that fits the resource limit with one "
            "that is one unit too large, plus two runs with backup disabled."
        ),
        "artifact_ids": (
            "budget-control-negative-control",
            "budget-fault-negative-control",
            "budget-control-with-fallback",
            "budget-fault-with-fallback",
            "report-fallback-budget-exhaustion",
        ),
        "relationship_ids": (
            "pair-budget-negative-control",
            "pair-budget-with-fallback",
            "contrast-budget-exact-fit-fallback",
            "contrast-budget-tight-fallback",
        ),
        "metadata": {
            "study": FALLBACK_BUDGET_EXHAUSTION.name,
            "mechanism": "scoped_fallback_capacity",
            "shared_condition": PERMANENT_WORKER_FAILURE.name,
            "business_context": (
                "A backup action can be available but still exceed the money "
                "or capacity set aside for it."
            ),
            "what_is_being_tested": (
                "Whether fallback starts only when its full cost fits, and is "
                "refused before any spending when capacity is one unit short."
            ),
            "why_it_matters": (
                "A reliable system should not begin work it cannot finish or "
                "quietly exceed an agreed limit; it should stop safely and "
                "leave a clear record."
            ),
            "what_to_watch": (
                "At four cost units, the request fits and recovery proceeds. "
                "At three, the request is denied with zero fallback spend and "
                "a safe stop. Runs with no fallback stay unchanged."
            ),
            "simple_explanation": (
                "The backup job needs four effort units, a simple stand-in for "
                "money or computer capacity. We give it either all four units "
                "or only three to prove that work begins only when there is "
                "enough capacity to finish it."
            ),
            "everyday_analogy": (
                "It works like a purchase that is approved only when the full "
                "amount is available, without taking a partial payment first."
            ),
            "exact_steps": (
                {
                    "label": "Set two limits",
                    "explanation": (
                        "One run receives four available units, while the "
                        "matching run receives three."
                    ),
                },
                {
                    "label": "Price the backup",
                    "explanation": (
                        "The system determines that the entire backup attempt "
                        "will require four units."
                    ),
                },
                {
                    "label": "Decide before spending",
                    "explanation": (
                        "With four units, it reserves the full amount and "
                        "begins; with three, it refuses before assigning work "
                        "or using any resources."
                    ),
                },
                {
                    "label": "Record the outcome",
                    "explanation": (
                        "The full-limit run recovers, while the smaller-limit "
                        "run stops safely with zero backup spending and a clear "
                        "explanation."
                    ),
                },
            ),
            "business_value": (
                "The system does not start work it cannot afford to finish and "
                "does not quietly exceed its limit. Leaders receive a "
                "predictable cost boundary and a clear record of why work "
                "proceeded or stopped."
            ),
            "build_rationale": (
                "The two limits differ by only one unit, making the decision "
                "boundary easy to see. Matching runs with backup turned off "
                "show that the limit changes nothing when no backup work is "
                "requested."
            ),
        },
    },
)


def _relationship_ids_for(artifact_id: str) -> tuple[str, ...]:
    return tuple(
        relationship["id"]
        for relationship in _RELATIONSHIPS
        if artifact_id
        in {
            relationship["source_artifact_id"],
            relationship["target_artifact_id"],
        }
    )


def _run_metadata(spec: _CuratedRun, metrics: Any) -> dict[str, Any]:
    metadata = {
        "expected_outcome": spec.expected_outcome,
        "fault": spec.study,
        "fault_active": spec.fault_active,
        "seed": CURATED_SEED,
        "strategy": spec.strategy,
        "study": spec.study,
        "study_arm": spec.study_arm,
        "variant": CURATED_VARIANT,
    }
    metadata.update(dict(spec.extra_metadata))
    reservations = to_primitive(getattr(metrics, "budget_reservations", ()))
    if spec.study == FALLBACK_BUDGET_EXHAUSTION.name:
        metadata.update(
            {
                "budget_reservation_observed": bool(reservations),
                "shared_worker_fault_injected": (
                    PERMANENT_WORKER_FAILURE.name
                    in getattr(metrics, "injected_fault_names", ())
                ),
                "study_intervention_injected": bool(
                    getattr(metrics, "study_intervention_fault_ids", ())
                ),
            }
        )
    if reservations:
        # One reservation is the intentionally bounded contract in this study.
        # Export the trace-derived fact rather than duplicating fixture values.
        metadata["scoped_budget"] = reservations[0]
    return metadata


def _artifact_descriptor(
    *,
    artifact_id: str,
    artifact_type: str,
    kind: str,
    path: str,
    title: str,
    summary: str,
    tags: tuple[str, ...],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Create a generic descriptor; type-specific data stays in its file."""

    return {
        "id": artifact_id,
        "type": artifact_type,
        "kind": kind,
        "path": path,
        "title": title,
        "summary": summary,
        "tags": tags,
        "relationship_ids": _relationship_ids_for(artifact_id),
        "metadata": metadata,
    }


def _artifact_envelope(
    descriptor: dict[str, Any],
    data: Any,
    *,
    metrics: Any | None = None,
) -> dict[str, Any]:
    envelope = {
        "schema_version": SHOWCASE_SCHEMA_VERSION,
        "id": descriptor["id"],
        "type": descriptor["type"],
        "kind": descriptor["kind"],
        "title": descriptor["title"],
        "summary": descriptor["summary"],
        "tags": descriptor["tags"],
        "relationship_ids": descriptor["relationship_ids"],
        "metadata": descriptor["metadata"],
        "data": to_primitive(data),
    }
    if metrics is not None:
        envelope["metrics"] = to_primitive(metrics)
    return envelope


def _compact_report(report: Any) -> dict[str, Any]:
    """Retain experiment declarations, aggregates, and causal effects only."""

    primitive = to_primitive(report)
    return {
        key: value
        for key, value in primitive.items()
        if key not in {"pairs", "runs"}
    }


def _json_text(payload: Any) -> str:
    return json.dumps(to_primitive(payload), indent=2, sort_keys=True) + "\n"


def _validate_matrix_arguments(start_seed: int, count: int) -> None:
    if not isinstance(start_seed, int):
        raise TypeError("matrix_start_seed must be an integer")
    if not isinstance(count, int):
        raise TypeError("matrix_count must be an integer")
    if count < 1:
        raise ValueError("matrix_count must be at least 1")


def write_showcase_bundle(
    output_dir: str | Path = DEFAULT_SHOWCASE_OUTPUT,
    *,
    matrix_start_seed: int = DEFAULT_MATRIX_START_SEED,
    matrix_count: int = DEFAULT_MATRIX_COUNT,
) -> ShowcaseBundle:
    """Generate a complete deterministic static-data bundle.

    ``output_dir`` is normally ``dashboard/public/data``.  Relative paths in
    the manifest always use POSIX separators, making the bundle portable to a
    static host.  No wall-clock timestamp or output location is serialized.
    """

    _validate_matrix_arguments(matrix_start_seed, matrix_count)
    destination = Path(output_dir)
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(f"showcase output is not a directory: {destination}")

    descriptors: list[dict[str, Any]] = []
    envelopes: list[tuple[str, dict[str, Any]]] = []

    for spec in _CURATED_RUNS:
        result = run_preset(
            spec.strategy,
            seed=CURATED_SEED,
            variant_name=CURATED_VARIANT,
            fault_name=spec.study if spec.fault_active else None,
            control_for_fault=(
                spec.study if spec.study_arm == "matched_control" else None
            ),
        )
        metrics = derive_run_metrics(result)
        relative_path = f"runs/{spec.artifact_id}.json"
        descriptor = _artifact_descriptor(
            artifact_id=spec.artifact_id,
            artifact_type="run",
            kind="simulation_result",
            path=relative_path,
            title=spec.title,
            summary=spec.summary,
            tags=spec.tags,
            metadata=_run_metadata(spec, metrics),
        )
        descriptors.append(descriptor)
        envelopes.append(
            (
                relative_path,
                _artifact_envelope(
                    descriptor,
                    result,
                    metrics=metrics,
                ),
            )
        )

    ablation = run_ablation_experiment(
        start_seed=matrix_start_seed,
        count=matrix_count,
    )
    planning = run_fault_experiment(
        start_seed=matrix_start_seed,
        count=matrix_count,
        fault_name=PLANNING_OMISSION.name,
    )
    worker = run_fault_experiment(
        start_seed=matrix_start_seed,
        count=matrix_count,
        fault_name=PERMANENT_WORKER_FAILURE.name,
    )
    budget = run_fault_experiment(
        start_seed=matrix_start_seed,
        count=matrix_count,
        fault_name=FALLBACK_BUDGET_EXHAUSTION.name,
    )
    report_specs = (
        (
            "report-ablation",
            "ablation_matrix",
            "Orchestration feature ablation",
            "Aggregate effects of the five core orchestration presets.",
            ("report", "ablation", "aggregate"),
            {
                "fault": None,
                "matrix_count": matrix_count,
                "matrix_start_seed": matrix_start_seed,
                "study": "orchestration_ablation",
            },
            ablation,
        ),
        (
            "report-planning-omission",
            "fault_matrix",
            "Planning omission matrix",
            "Aggregate matched-control effects and the isolated critic contrast.",
            ("report", "fault-matrix", "planning", "critic"),
            {
                "fault": PLANNING_OMISSION.name,
                "matrix_count": matrix_count,
                "matrix_start_seed": matrix_start_seed,
                "study": PLANNING_OMISSION.name,
            },
            planning,
        ),
        (
            "report-permanent-worker-failure",
            "fault_matrix",
            "Permanent worker failure matrix",
            "Aggregate matched-control effects and the isolated fallback contrast.",
            ("report", "fault-matrix", "worker", "fallback"),
            {
                "fault": PERMANENT_WORKER_FAILURE.name,
                "matrix_count": matrix_count,
                "matrix_start_seed": matrix_start_seed,
                "study": PERMANENT_WORKER_FAILURE.name,
            },
            worker,
        ),
        (
            "report-fallback-budget-exhaustion",
            "fault_matrix",
            "Fallback budget exhaustion matrix",
            (
                "Aggregate exact-fit and tight-capacity admission effects with "
                "critic-only negative controls."
            ),
            ("report", "fault-matrix", "budget", "fallback", "atomic"),
            {
                "fault": FALLBACK_BUDGET_EXHAUSTION.name,
                "matrix_count": matrix_count,
                "matrix_start_seed": matrix_start_seed,
                "study": FALLBACK_BUDGET_EXHAUSTION.name,
            },
            budget,
        ),
    )
    for (
        artifact_id,
        kind,
        title,
        summary,
        tags,
        metadata,
        report,
    ) in report_specs:
        relative_path = f"reports/{artifact_id}.json"
        descriptor = _artifact_descriptor(
            artifact_id=artifact_id,
            artifact_type="report",
            kind=kind,
            path=relative_path,
            title=title,
            summary=summary,
            tags=tags,
            metadata=metadata,
        )
        descriptors.append(descriptor)
        envelopes.append(
            (
                relative_path,
                _artifact_envelope(descriptor, _compact_report(report)),
            )
        )

    manifest = {
        "schema_version": SHOWCASE_SCHEMA_VERSION,
        "type": "showcase_manifest",
        "kind": "aurora_run_explorer",
        "title": "Aurora Agent Orchestration Lab",
        "summary": (
            "Deterministic runs and matched experiments for exploring agent "
            "orchestration, governance, fault detection, and containment."
        ),
        "default_artifact_id": "clean-full-orchestration",
        "artifacts": descriptors,
        "relationships": _RELATIONSHIPS,
        "stories": _STORIES,
        "metadata": {
            "curated_seed": CURATED_SEED,
            "curated_variant": CURATED_VARIANT,
            "matrix_count": matrix_count,
            "matrix_start_seed": matrix_start_seed,
        },
    }

    destination.mkdir(parents=True, exist_ok=True)
    artifact_paths: list[Path] = []
    for relative_path, envelope in envelopes:
        artifact_path = destination / Path(relative_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(_json_text(envelope), encoding="utf-8")
        artifact_paths.append(artifact_path.resolve())
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(_json_text(manifest), encoding="utf-8")

    return ShowcaseBundle(
        output_dir=destination.resolve(),
        manifest_path=manifest_path.resolve(),
        artifact_paths=tuple(artifact_paths),
    )


__all__ = [
    "DEFAULT_SHOWCASE_OUTPUT",
    "SHOWCASE_SCHEMA_VERSION",
    "ShowcaseBundle",
    "write_showcase_bundle",
]
