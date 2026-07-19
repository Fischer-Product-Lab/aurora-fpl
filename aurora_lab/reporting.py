"""Human-readable and JSON reporting for simulation artifacts."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .faults import FALLBACK_BUDGET_EXHAUSTION, PERMANENT_WORKER_FAILURE
from .metrics import derive_run_metrics
from .model import SimulationResult, TraceEvent


def metadata_dict(metadata: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Convert stable metadata tuples to a convenient read-only-style dict."""

    return dict(metadata)


def to_primitive(value: Any) -> Any:
    """Recursively convert contracts into stable JSON-compatible values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [to_primitive(item) for item in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, Path):
        return str(value)
    return value


def result_json(result: SimulationResult, *, indent: int = 2) -> str:
    """Serialize a result with stable keys and formatting."""

    return json.dumps(to_primitive(result), indent=indent, sort_keys=True) + "\n"


def write_result_json(path: str | Path, result: SimulationResult) -> Path:
    """Write one structured run artifact and return its resolved path."""

    destination = Path(path)
    destination.write_text(result_json(result), encoding="utf-8")
    return destination.resolve()


def _format_time(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds = remainder / 1_000
    return f"{minutes:02d}:{seconds:04.1f}"


def format_trace_event(event: TraceEvent) -> str:
    """Render one event as a compact, aligned trace line."""

    task = f" [{event.task_id}]" if event.task_id else ""
    metadata = ""
    if event.metadata:
        metadata = " · " + ", ".join(f"{key}={value}" for key, value in event.metadata)
    return (
        f"{event.sequence:03d}  {_format_time(event.at_ms)}  "
        f"{event.actor.value:<13} {event.kind.value:<24}{task}  "
        f"{event.message}{metadata}"
    ).rstrip()


def format_trace(result: SimulationResult, *, compact: bool = False) -> str:
    """Render a complete or teaching-focused event trace."""

    events = result.trace
    if compact:
        important = {
            "run_started",
            "goal_created",
            "task_retry_scheduled",
            "retry_exhausted",
            "task_failed",
            "task_cancelled",
            "task_reassigned",
            "fallback_completed",
            "budget_reservation_requested",
            "budget_reservation_granted",
            "budget_reservation_denied",
            "fallback_skipped",
            "security_denied",
            "synthesis_created",
            "plan_proposed",
            "self_review_completed",
            "fault_injected",
            "fault_detected",
            "fault_repaired",
            "fault_contained",
            "critique_rejected",
            "replan_created",
            "critique_accepted",
            "approval_requested",
            "approval_granted",
            "approval_denied",
            "action_executed",
            "action_denied",
            "verification_completed",
            "communication_published",
            "score_computed",
            "run_completed",
        }
        events = tuple(event for event in events if event.kind.value in important)
    return "\n".join(format_trace_event(event) for event in events)


def format_result(result: SimulationResult, *, include_trace: str = "summary") -> str:
    """Render one run with outcome, metrics, score, and optional trace."""

    metadata = metadata_dict(result.metadata)
    lines = [
        f"{result.strategy.upper()} — {metadata.get('variant', 'unknown case')}",
        f"Outcome: {metadata.get('outcome', result.status.value)}",
        f"Virtual time: {_format_time(result.ended_at_ms - result.started_at_ms)}",
        (
            "Usage: "
            f"{result.tool_calls_used} tool calls · {result.attempts_used} attempts · "
            f"{result.cost_units_used} cost units"
        ),
    ]
    if metadata.get("fault_study"):
        lines.insert(
            1,
            (
                f"Fault study: {metadata['fault_study']} · "
                f"arm: {metadata.get('fault_arm', 'unknown')}"
            ),
        )

    diagnosis = metadata.get("diagnosis")
    if diagnosis:
        lines.append(f"Diagnosis: {diagnosis}")
    actions = metadata.get("actions")
    if actions:
        lines.append(f"Actions: {actions}")
    if result.score is not None:
        lines.append(
            "Score: "
            f"{result.score.total:.1f}/100 "
            f"(cause {result.score.root_cause:.1f}, recovery {result.score.recovery:.1f}, "
            f"safety {result.score.safety:.1f}, evidence {result.score.evidence:.1f}, "
            f"resilience {result.score.resilience:.1f}, efficiency {result.score.efficiency:.1f})"
        )

    if include_trace != "none":
        lines.extend(("", "Trace", "-----"))
        lines.append(format_trace(result, compact=include_trace == "summary"))
    return "\n".join(lines)


def format_comparison(
    orchestrated: SimulationResult,
    baseline: SimulationResult,
    *,
    trace_mode: str = "summary",
) -> str:
    """Render a concise paired comparison and optional traces."""

    if trace_mode not in {"summary", "full", "none"}:
        raise ValueError("trace_mode must be summary, full, or none")

    orchestrated_score = orchestrated.score.total if orchestrated.score else 0.0
    baseline_score = baseline.score.total if baseline.score else 0.0
    orchestrated_metrics = derive_run_metrics(orchestrated)
    baseline_metrics = derive_run_metrics(baseline)
    orchestrated_metadata = metadata_dict(orchestrated.metadata)
    baseline_metadata = metadata_dict(baseline.metadata)

    def milestone(milliseconds: int | None) -> str:
        return "n/a" if milliseconds is None else _format_time(milliseconds)

    rows = (
        ("Score", f"{orchestrated_score:.1f}", f"{baseline_score:.1f}"),
        (
            "Last action",
            milestone(orchestrated_metrics.last_action_ms),
            milestone(baseline_metrics.last_action_ms),
        ),
        (
            "First SLO pass",
            milestone(orchestrated_metrics.first_slo_pass_ms),
            milestone(baseline_metrics.first_slo_pass_ms),
        ),
        (
            "Confirmed recovery",
            milestone(orchestrated_metrics.confirmed_recovery_ms),
            milestone(baseline_metrics.confirmed_recovery_ms),
        ),
        (
            "Virtual time",
            _format_time(orchestrated.ended_at_ms - orchestrated.started_at_ms),
            _format_time(baseline.ended_at_ms - baseline.started_at_ms),
        ),
        ("Tool calls", str(orchestrated.tool_calls_used), str(baseline.tool_calls_used)),
        ("Cost units", str(orchestrated.cost_units_used), str(baseline.cost_units_used)),
        (
            "Outcome",
            orchestrated_metadata.get("outcome", orchestrated.status.value),
            baseline_metadata.get("outcome", baseline.status.value),
        ),
    )
    lines = ["PAIRED COMPARISON"]
    if (
        orchestrated_metadata.get("fault_study")
        and orchestrated_metadata.get("fault_study")
        == baseline_metadata.get("fault_study")
        and orchestrated_metadata.get("fault_arm")
        == baseline_metadata.get("fault_arm")
    ):
        lines.append(
            "Fault study: "
            f"{orchestrated_metadata['fault_study']} · "
            f"arm: {orchestrated_metadata['fault_arm']}"
        )
    lines.extend([
        "Metric                Orchestrated       Single agent",
        "--------------------  -----------------  -----------------",
    ])
    for label, left, right in rows:
        lines.append(f"{label:<20}  {left:<17}  {right:<17}")
    lines.extend(("", f"Score advantage: {orchestrated_score - baseline_score:+.1f} points"))
    if trace_mode != "none":
        label = "FULL" if trace_mode == "full" else "SUMMARY"
        lines.extend(
            (
                "",
                f"ORCHESTRATED {label} TRACE",
                "--------------------------",
                format_trace(orchestrated, compact=trace_mode == "summary"),
                "",
                f"SINGLE-AGENT {label} TRACE",
                "--------------------------",
                format_trace(baseline, compact=trace_mode == "summary"),
            )
        )
    return "\n".join(lines)


def format_experiment(report: Any, *, show_runs: bool = False) -> str:
    """Render a multi-seed paired experiment report."""

    first_seed = report.start_seed
    last_seed = report.start_seed + report.count - 1
    variant_mix = ", ".join(
        f"{variant}={count}" for variant, count in report.variant_counts
    )
    orchestrated = report.orchestrated
    baseline = report.baseline

    def seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:.2f}s"

    def difference(left: float | None, right: float | None) -> float | None:
        if left is None or right is None:
            return None
        return left - right

    rows = (
        (
            "Success rate",
            f"{orchestrated.success_rate:.1f}%",
            f"{baseline.success_rate:.1f}%",
            f"{orchestrated.success_rate - baseline.success_rate:+.1f}pp",
        ),
        (
            "Mean score",
            f"{orchestrated.mean_score:.2f}",
            f"{baseline.mean_score:.2f}",
            f"{orchestrated.mean_score - baseline.mean_score:+.2f}",
        ),
        (
            "Minimum score",
            f"{orchestrated.minimum_score:.2f}",
            f"{baseline.minimum_score:.2f}",
            f"{orchestrated.minimum_score - baseline.minimum_score:+.2f}",
        ),
        (
            "Mean last action",
            seconds(orchestrated.mean_last_action_ms),
            seconds(baseline.mean_last_action_ms),
            seconds(
                difference(
                    orchestrated.mean_last_action_ms,
                    baseline.mean_last_action_ms,
                )
            ),
        ),
        (
            "Mean first SLO pass",
            seconds(orchestrated.mean_first_slo_pass_ms),
            seconds(baseline.mean_first_slo_pass_ms),
            seconds(
                difference(
                    orchestrated.mean_first_slo_pass_ms,
                    baseline.mean_first_slo_pass_ms,
                )
            ),
        ),
        (
            "Mean confirmed recovery",
            seconds(orchestrated.mean_confirmed_recovery_ms),
            seconds(baseline.mean_confirmed_recovery_ms),
            seconds(
                difference(
                    orchestrated.mean_confirmed_recovery_ms,
                    baseline.mean_confirmed_recovery_ms,
                )
            ),
        ),
        (
            "Mean total time",
            seconds(orchestrated.mean_total_ms),
            seconds(baseline.mean_total_ms),
            seconds(orchestrated.mean_total_ms - baseline.mean_total_ms),
        ),
        (
            "Mean cost units",
            f"{orchestrated.mean_cost_units:.2f}",
            f"{baseline.mean_cost_units:.2f}",
            f"{orchestrated.mean_cost_units - baseline.mean_cost_units:+.2f}",
        ),
        (
            "Mean tool calls",
            f"{orchestrated.mean_tool_calls:.2f}",
            f"{baseline.mean_tool_calls:.2f}",
            f"{orchestrated.mean_tool_calls - baseline.mean_tool_calls:+.2f}",
        ),
    )
    lines = [
        f"EXPERIMENT MATRIX — seeds {first_seed}..{last_seed}",
        f"Cases: {report.count} · Variants: {variant_mix}",
        "",
        "Metric                   Orchestrated    Single agent    O-B delta",
        "-----------------------  --------------  --------------  ----------",
    ]
    for label, left, right, delta in rows:
        lines.append(f"{label:<23}  {left:<14}  {right:<14}  {delta:<10}")
    lines.extend(
        (
            "",
            (
                "Paired score wins: "
                f"orchestrated {report.orchestrated_wins} · "
                f"single agent {report.baseline_wins} · ties {report.ties}"
            ),
            "",
            "BY VARIANT",
            "Variant                          Cases  O score  S score  Delta  Action-time advantage",
            "-------------------------------  -----  -------  -------  -----  --------------------",
        )
    )
    for item in report.by_variant:
        lines.append(
            f"{item.variant:<31}  {item.cases:>5}  "
            f"{item.orchestrated_mean_score:>7.2f}  {item.baseline_mean_score:>7.2f}  "
            f"{item.mean_score_delta:>+5.2f}  {seconds(item.mean_action_time_advantage_ms):>20}"
        )

    if show_runs:
        lines.extend(
            (
                "",
                "PAIRED RUNS",
                "Seed  Variant                          O score  S score  Delta  Action-time advantage",
                "----  -------------------------------  -------  -------  -----  --------------------",
            )
        )
        for pair in report.pairs:
            lines.append(
                f"{pair.seed:>4}  {pair.variant:<31}  "
                f"{pair.orchestrated_score:>7.2f}  {pair.baseline_score:>7.2f}  "
                f"{pair.score_delta:>+5.2f}  {seconds(pair.action_time_advantage_ms):>20}"
            )
    return "\n".join(lines)


def format_ablation(report: Any, *, show_runs: bool = False) -> str:
    """Render the five-preset orchestration ablation."""

    first_seed = report.start_seed
    last_seed = report.start_seed + report.count - 1
    variant_mix = ", ".join(
        f"{variant}={count}" for variant, count in report.variant_counts
    )

    def seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:.2f}s"

    lines = [
        f"ABLATION MATRIX — seeds {first_seed}..{last_seed}",
        (
            f"Cases: {report.count} · Runs: {len(report.runs)} · "
            f"Variants: {variant_mix}"
        ),
        "",
        (
            "Strategy                   Success   Score  Last act  First SLO  "
            "Confirmed   Total   Cost  Calls"
        ),
        (
            "-------------------------  --------  ------  --------  ---------  "
            "---------  ------  -----  -----"
        ),
    ]
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  {item.success_rate:>7.1f}%  "
            f"{item.mean_score:>6.2f}  {seconds(item.mean_last_action_ms):>8}  "
            f"{seconds(item.mean_first_slo_pass_ms):>9}  "
            f"{seconds(item.mean_confirmed_recovery_ms):>9}  "
            f"{seconds(item.mean_total_ms):>6}  "
            f"{item.mean_cost_units:>5.2f}  {item.mean_tool_calls:>5.2f}"
        )

    lines.extend(
        (
            "",
            "Milestone means use trace-derived observations; absent milestones display as n/a.",
        )
    )
    if show_runs:
        lines.extend(
            (
                "",
                "ABLATION RUNS",
                (
                    "Seed  Variant                          Strategy                   "
                    "Status  Score  Last act  First SLO  Confirmed  Cost  Calls"
                ),
                (
                    "----  -------------------------------  -------------------------  "
                    "------  -----  --------  ---------  ---------  ----  -----"
                ),
            )
        )
        for run in report.runs:
            status = "pass" if run.success else "fail"
            lines.append(
                f"{run.seed:>4}  {run.variant:<31}  {run.strategy:<25}  "
                f"{status:>6}  {run.score:>5.1f}  {seconds(run.last_action_ms):>8}  "
                f"{seconds(run.first_slo_pass_ms):>9}  "
                f"{seconds(run.confirmed_recovery_ms):>9}  "
                f"{run.cost_units:>4}  {run.tool_calls:>5}"
            )
    return "\n".join(lines)


def _format_worker_fault_experiment(
    report: Any,
    *,
    show_runs: bool = False,
) -> str:
    """Render terminal worker loss without describing tolerance as repair."""

    first_seed = report.start_seed
    last_seed = report.start_seed + report.count - 1
    variant_mix = ", ".join(
        f"{variant}={count}" for variant, count in report.variant_counts
    )

    def seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:.2f}s"

    def signed_seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:+.2f}s"

    def rate_pair(control: float, faulted: float) -> str:
        return f"{control:.1f}%/{faulted:.1f}%"

    def time_pair(control: float | None, faulted: float | None) -> str:
        return f"{seconds(control)}/{seconds(faulted)}"

    lines = [
        (
            f"FAULT MATRIX — {report.fault_name} — "
            f"seeds {first_seed}..{last_seed}"
        ),
        (
            f"Cases: {report.count} · Paired rows: {len(report.pairs)} · "
            f"Runs: {2 * len(report.pairs)} · Variants: {variant_mix}"
        ),
        (
            "Conditions: matched_control retains the seeded worker's successful "
            "bounded retry; the fault makes that same final attempt fail "
            "terminally without committing evidence. Reassignment and fallback "
            "are orchestration responses."
        ),
        "",
        (
            "Strategy                   Recovery C/F     F-C     Required evidence C/F  "
            "Safe degraded F  Policy safety F  Cost Δ  Calls Δ"
        ),
        (
            "-------------------------  ---------------  ------  ---------------------  "
            "---------------  ---------------  ------  -------"
        ),
    ]
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  "
            f"{rate_pair(item.control_recovery_rate_pct, item.fault_recovery_rate_pct):>15}  "
            f"{item.fault_minus_control_recovery_rate_pp:>+5.1f}pp  "
            f"{rate_pair(item.control_mean_required_evidence_tag_coverage_pct, item.fault_mean_required_evidence_tag_coverage_pct):>21}  "
            f"{item.fault_safe_degradation_rate_pct:>14.1f}%  "
            f"{item.fault_policy_safe_rate_pct:>14.1f}%  "
            f"{item.fault_minus_control_mean_cost_units:>+6.2f}  "
            f"{item.fault_minus_control_mean_tool_calls:>+7.2f}"
        )

    lines.extend(
        (
            "",
            "WORKER-FAILURE LIFECYCLE — fault arm",
            (
                "Strategy                   Injected  Target terminal  Detected  "
                "Reassigned  Fallback succeeded  Contained"
            ),
            (
                "-------------------------  --------  ---------------  --------  "
                "----------  ------------------  ---------"
            ),
        )
    )
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  "
            f"{item.fault_injection_rate_pct:>7.1f}%  "
            f"{item.fault_target_terminal_failure_rate_pct:>14.1f}%  "
            f"{item.fault_detection_rate_pct:>7.1f}%  "
            f"{item.fault_reassignment_rate_pct:>9.1f}%  "
            f"{item.fault_fallback_success_rate_pct:>17.1f}%  "
            f"{item.fault_containment_rate_pct:>8.1f}%"
        )

    lines.extend(("", "MISSING REQUIRED APPROVAL-EVIDENCE TAGS — fault arm"))
    for item in report.aggregates:
        missing = ",".join(item.fault_missing_required_evidence_tags) or "none"
        lines.append(f"{item.strategy:<25}  {missing}")

    lines.extend(
        (
            "",
            "CONFIRMED-RECOVERY LATENCY",
            (
                "Strategy                   Paired F-C (common n)  "
                "Control survivor mean (n/cases)  Fault survivor mean (n/cases)"
            ),
            (
                "-------------------------  ---------------------  "
                "-------------------------------  -----------------------------"
            ),
        )
    )
    for item in report.aggregates:
        paired = (
            f"{signed_seconds(item.mean_fault_minus_control_confirmed_recovery_ms)} "
            f"(n={item.common_recovery_pairs})"
        )
        control_survivors = (
            f"{seconds(item.control_mean_confirmed_recovery_ms)} "
            f"({item.control_confirmed_recovery_samples}/{item.cases})"
        )
        fault_survivors = (
            f"{seconds(item.fault_mean_confirmed_recovery_ms)} "
            f"({item.fault_confirmed_recovery_samples}/{item.cases})"
        )
        lines.append(
            f"{item.strategy:<25}  {paired:>21}  "
            f"{control_survivors:>31}  {fault_survivors:>29}"
        )

    effect = report.fallback_effect
    common_latency = signed_seconds(
        effect.mean_fault_with_minus_without_confirmed_recovery_ms
    )
    lines.extend(
        (
            "",
            "ISOLATED FALLBACK EFFECT — matched specialist presets",
            (
                "Strategies: "
                f"{effect.without_fallback_strategy} without → "
                f"{effect.with_fallback_strategy} with"
            ),
            (
                "Fault recovery: "
                f"{effect.fault_without_fallback_recovery_rate_pct:.1f}% without → "
                f"{effect.fault_with_fallback_recovery_rate_pct:.1f}% with "
                f"({effect.fault_with_minus_without_recovery_rate_pp:+.1f}pp)"
            ),
            (
                "Control recovery lift: "
                f"{effect.control_with_minus_without_recovery_rate_pp:+.1f}pp"
            ),
            (
                "Recovery difference-in-differences: "
                f"{effect.recovery_difference_in_differences_pp:+.1f}pp"
            ),
            (
                "Fault rescues/regressions/net: "
                f"{effect.fault_rescues}/{effect.fault_regressions}/"
                f"{effect.fault_net_rescues:+d}"
            ),
            (
                "Fault containment: "
                f"{effect.fault_without_fallback_containment_rate_pct:.1f}% without → "
                f"{effect.fault_with_fallback_containment_rate_pct:.1f}% with "
                f"({effect.fault_with_minus_without_containment_rate_pp:+.1f}pp)"
            ),
            (
                "Fault required-evidence coverage: "
                f"{effect.fault_without_fallback_mean_required_evidence_tag_coverage_pct:.1f}% "
                f"without → {effect.fault_with_fallback_mean_required_evidence_tag_coverage_pct:.1f}% "
                f"with ({effect.fault_with_minus_without_required_evidence_tag_coverage_pp:+.1f}pp)"
            ),
            (
                "Policy-safe degradation: "
                f"{effect.fault_without_fallback_safe_degradation_rate_pct:.1f}% without → "
                f"{effect.fault_with_fallback_safe_degradation_rate_pct:.1f}% with "
                f"({effect.fault_with_minus_without_safe_degradation_rate_pp:+.1f}pp)"
            ),
            (
                "Fault cost/call effect: "
                f"{effect.fault_with_minus_without_mean_cost_units:+.2f} units / "
                f"{effect.fault_with_minus_without_mean_tool_calls:+.2f} calls"
            ),
            (
                "Common-recovery latency effect: "
                f"{common_latency} (with minus without; "
                f"n={effect.fault_common_recovery_pairs})"
            ),
            "",
            (
                "The target worker remains terminally failed. Containment means "
                "a linked fallback restored trusted evidence and the incident then "
                "reached two valid SLO passes; it does not mean the worker recovered."
            ),
            (
                "Required-evidence coverage counts trusted tags required to approve "
                "the evaluator-required action set. Lifecycle rates use all fault-arm "
                "cases. Paired latency deltas use only cases where both compared runs "
                "recovered; survivor means show observed/cases denominators. Missing "
                "milestones remain n/a."
            ),
        )
    )

    if show_runs:
        lines.extend(
            (
                "",
                "PAIRED WORKER-FAULT RUNS",
                (
                    "Seed  Variant                          Strategy                   "
                    "Target task                  Recovery C/F  Evidence C/F  "
                    "Safe degraded  Reassign/Fallback/Contained  Confirmed C/F       Cost Δ"
                ),
                (
                    "----  -------------------------------  -------------------------  "
                    "---------------------------  ------------  ------------  "
                    "-------------  -----------------------------  ------------------  ------"
                ),
            )
        )
        for pair in report.pairs:
            target = ",".join(pair.faulted.fault_target_task_ids) or "n/a"
            recovery = (
                f"{'pass' if pair.control.recovered else 'fail'}/"
                f"{'pass' if pair.faulted.recovered else 'fail'}"
            )
            evidence = (
                f"{pair.control.required_evidence_tag_coverage_pct:.1f}%/"
                f"{pair.faulted.required_evidence_tag_coverage_pct:.1f}%"
            )
            lifecycle = (
                f"{'yes' if pair.faulted.reassignment_attempted else 'no'}/"
                f"{'yes' if pair.faulted.fallback_succeeded else 'no'}/"
                f"{'yes' if pair.faulted.fault_contained else 'no'}"
            )
            lines.append(
                f"{pair.seed:>4}  {pair.variant:<31}  {pair.strategy:<25}  "
                f"{target:<27}  {recovery:>12}  {evidence:>12}  "
                f"{'yes' if pair.faulted.safe_degraded else 'no':>13}  "
                f"{lifecycle:>29}  "
                f"{time_pair(pair.control.confirmed_recovery_ms, pair.faulted.confirmed_recovery_ms):>18}  "
                f"{pair.faulted.cost_units - pair.control.cost_units:>+6}"
            )
    return "\n".join(lines)


def _format_budget_fault_experiment(
    report: Any,
    *,
    show_runs: bool = False,
) -> str:
    """Render the atomic fallback-budget study and its shared worker condition."""

    first_seed = report.start_seed
    last_seed = report.start_seed + report.count - 1
    variant_mix = ", ".join(
        f"{variant}={count}" for variant, count in report.variant_counts
    )

    def rate_pair(control: float, faulted: float) -> str:
        return f"{control:.1f}%/{faulted:.1f}%"

    def count_pair(control: float, faulted: float) -> str:
        return f"{control:.1f}/{faulted:.1f}"

    def signed_seconds(milliseconds: float | int) -> str:
        return f"{milliseconds / 1000:+.2f}s"

    lines = [
        (
            f"FALLBACK BUDGET MATRIX - {report.fault_name} - "
            f"seeds {first_seed}..{last_seed}"
        ),
        (
            f"Cases: {report.count} | Paired rows: {len(report.pairs)} | "
            f"Runs: {2 * len(report.pairs)} | Variants: {variant_mix}"
        ),
        (
            "Shared condition: every exact-fit and tight-budget run has the same "
            "permanently failed worker. The study intervention changes only the "
            "fallback's scoped cost capacity from an exact-fit 4 units to 3 units."
        ),
        (
            "The two presets are a no-fallback negative control and an otherwise "
            "matched fallback treatment. A budget fault is injected only when the "
            "fallback treatment reaches admission."
        ),
        "",
        (
            "Strategy                   Recovery E/T   Safe degraded E/T  "
            "Budget safe E/T  Requests E/T  Grants E/T  Denials E/T  "
            "Skips E/T  Cost d  Calls d"
        ),
        (
            "-------------------------  -------------  -----------------  "
            "---------------  ------------  ----------  -----------  "
            "---------  ------  -------"
        ),
    ]
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  "
            f"{rate_pair(item.control_recovery_rate_pct, item.fault_recovery_rate_pct):>13}  "
            f"{rate_pair(item.control_safe_degradation_rate_pct, item.fault_safe_degradation_rate_pct):>17}  "
            f"{rate_pair(item.control_budget_safe_rate_pct, item.fault_budget_safe_rate_pct):>15}  "
            f"{count_pair(item.control_mean_reservation_request_count, item.fault_mean_reservation_request_count):>12}  "
            f"{count_pair(item.control_mean_reservation_grant_count, item.fault_mean_reservation_grant_count):>10}  "
            f"{count_pair(item.control_mean_reservation_denial_count, item.fault_mean_reservation_denial_count):>11}  "
            f"{count_pair(item.control_mean_fallback_skip_count, item.fault_mean_fallback_skip_count):>9}  "
            f"{item.fault_minus_control_mean_cost_units:>+6.2f}  "
            f"{item.fault_minus_control_mean_tool_calls:>+7.2f}"
        )

    lines.extend(
        (
            "",
            "ADMISSION AND RESOURCE ACCOUNTING - exact-fit/tight-budget",
            (
                "Strategy                   Request cost  Capacity cost  Scoped use  "
                "Attempts d  Cost d  Calls d  Time d"
            ),
            (
                "-------------------------  ------------  -------------  ----------  "
                "----------  ------  -------  -------"
            ),
        )
    )
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  "
            f"{count_pair(item.control_mean_requested_cost_units, item.fault_mean_requested_cost_units):>12}  "
            f"{count_pair(item.control_mean_capacity_cost_units, item.fault_mean_capacity_cost_units):>13}  "
            f"{count_pair(item.control_mean_scoped_usage_cost_units, item.fault_mean_scoped_usage_cost_units):>10}  "
            f"{item.fault_minus_control_mean_attempts_used:>+10.2f}  "
            f"{item.fault_minus_control_mean_cost_units:>+6.2f}  "
            f"{item.fault_minus_control_mean_tool_calls:>+7.2f}  "
            f"{signed_seconds(item.fault_minus_control_mean_total_ms):>7}"
        )

    lines.extend(
        (
            "",
            "TRACE-DERIVED SAFETY AND LIFECYCLE",
            (
                "Strategy                   Shared worker injected E/T  "
                "Shared worker terminal E/T  Study injected/detected T  "
                "Zero overrun E/T  Policy safe E/T"
            ),
            (
                "-------------------------  --------------------------  "
                "--------------------------  -------------------------  "
                "----------------  ---------------"
            ),
        )
    )
    for item in report.aggregates:
        study = (
            f"{item.fault_study_injection_rate_pct:.1f}%/"
            f"{item.fault_study_detection_rate_pct:.1f}%"
        )
        lines.append(
            f"{item.strategy:<25}  "
            f"{rate_pair(item.control_shared_worker_injection_rate_pct, item.fault_shared_worker_injection_rate_pct):>26}  "
            f"{rate_pair(item.control_shared_worker_terminal_failure_rate_pct, item.fault_shared_worker_terminal_failure_rate_pct):>26}  "
            f"{study:>25}  "
            f"{rate_pair(item.control_zero_budget_overrun_rate_pct, item.fault_zero_budget_overrun_rate_pct):>16}  "
            f"{rate_pair(item.control_policy_safe_rate_pct, item.fault_policy_safe_rate_pct):>15}"
        )

    effect = report.fallback_effect
    lines.extend(
        (
            "",
            "ISOLATED FALLBACK BENEFIT UNDER BUDGET PRESSURE",
            (
                "Strategies: "
                f"{effect.without_fallback_strategy} without -> "
                f"{effect.with_fallback_strategy} with fallback"
            ),
            (
                "Exact-fit recovery: "
                f"{effect.exact_fit_without_fallback_recovery_rate_pct:.1f}% "
                f"without -> {effect.exact_fit_with_fallback_recovery_rate_pct:.1f}% "
                f"with ({effect.exact_fit_fallback_benefit_pp:+.1f}pp benefit)"
            ),
            (
                "Tight-budget recovery: "
                f"{effect.tight_budget_without_fallback_recovery_rate_pct:.1f}% "
                f"without -> {effect.tight_budget_with_fallback_recovery_rate_pct:.1f}% "
                f"with ({effect.tight_budget_fallback_benefit_pp:+.1f}pp benefit)"
            ),
            (
                "Fallback-benefit difference-in-differences: "
                f"{effect.fallback_benefit_difference_in_differences_pp:+.1f}pp"
            ),
            (
                "Exact-fit/tight/lost rescues: "
                f"{effect.exact_fit_rescues}/{effect.tight_budget_rescues}/"
                f"{effect.lost_rescues}"
            ),
            (
                "Treatment admission: "
                f"exact-fit grant {effect.exact_fit_with_fallback_grant_rate_pct:.1f}%; "
                f"tight denial {effect.tight_budget_with_fallback_denial_rate_pct:.1f}%; "
                f"tight skip {effect.tight_budget_with_fallback_skip_rate_pct:.1f}%"
            ),
            (
                "Tight denial contract: "
                f"zero spend {effect.tight_budget_with_fallback_zero_spend_rate_pct:.1f}%; "
                f"no fallback dispatch {effect.tight_budget_with_fallback_no_dispatch_rate_pct:.1f}%; "
                f"budget safe {effect.tight_budget_with_fallback_budget_safe_rate_pct:.1f}%; "
                f"safe degradation {effect.tight_budget_with_fallback_safe_degradation_rate_pct:.1f}%"
            ),
            (
                "Treatment tight-minus-exact resources: "
                f"{effect.tight_minus_exact_with_fallback_mean_attempts_used:+.2f} attempts, "
                f"{effect.tight_minus_exact_with_fallback_mean_cost_units:+.2f} cost units, "
                f"{effect.tight_minus_exact_with_fallback_mean_tool_calls:+.2f} calls, "
                f"{signed_seconds(effect.tight_minus_exact_with_fallback_mean_total_ms)}"
            ),
            (
                "Negative-control tight-minus-exact resources: "
                f"{effect.tight_minus_exact_without_fallback_mean_attempts_used:+.2f} attempts, "
                f"{effect.tight_minus_exact_without_fallback_mean_cost_units:+.2f} cost units, "
                f"{effect.tight_minus_exact_without_fallback_mean_tool_calls:+.2f} calls, "
                f"{signed_seconds(effect.tight_minus_exact_without_fallback_mean_total_ms)}"
            ),
            "",
            (
                "Budget safety is reconstructed from reservation events, scoped "
                "usage, global-ledger before/after values, task results, and the "
                "absence of task-start, tool-attempt, or evidence events after a denial."
            ),
            (
                "A tight-budget failure is safe degradation, not worker recovery: "
                "the failed worker remains terminal and the denied fallback is never run."
            ),
        )
    )

    if show_runs:
        lines.extend(
            (
                "",
                "PAIRED FALLBACK-BUDGET RUNS",
                (
                    "Seed  Variant                          Strategy                   "
                    "Recovery E/T  Safe E/T  Budget E/T  Req/Grant/Deny/Skip E  "
                    "Req/Grant/Deny/Skip T  Capacity E/T  Use E/T  Cost d  Calls d"
                ),
                (
                    "----  -------------------------------  -------------------------  "
                    "------------  --------  ----------  ---------------------  "
                    "---------------------  ------------  -------  ------  -------"
                ),
            )
        )
        for pair in report.pairs:
            recovery = (
                f"{'pass' if pair.control.recovered else 'fail'}/"
                f"{'pass' if pair.faulted.recovered else 'fail'}"
            )
            safe = (
                f"{'yes' if pair.control.safe_degraded else 'no'}/"
                f"{'yes' if pair.faulted.safe_degraded else 'no'}"
            )
            budget = (
                f"{'yes' if pair.control.budget_safe else 'no'}/"
                f"{'yes' if pair.faulted.budget_safe else 'no'}"
            )
            exact_events = (
                f"{pair.control.reservation_request_count}/"
                f"{pair.control.reservation_grant_count}/"
                f"{pair.control.reservation_denial_count}/"
                f"{pair.control.fallback_skip_count}"
            )
            tight_events = (
                f"{pair.faulted.reservation_request_count}/"
                f"{pair.faulted.reservation_grant_count}/"
                f"{pair.faulted.reservation_denial_count}/"
                f"{pair.faulted.fallback_skip_count}"
            )
            capacity = (
                f"{pair.control.capacity_cost_units}/"
                f"{pair.faulted.capacity_cost_units}"
            )
            usage = (
                f"{pair.control.scoped_usage_cost_units}/"
                f"{pair.faulted.scoped_usage_cost_units}"
            )
            lines.append(
                f"{pair.seed:>4}  {pair.variant:<31}  {pair.strategy:<25}  "
                f"{recovery:>12}  {safe:>8}  {budget:>10}  "
                f"{exact_events:>21}  {tight_events:>21}  "
                f"{capacity:>12}  {usage:>7}  "
                f"{pair.faulted.cost_units - pair.control.cost_units:>+6}  "
                f"{pair.faulted.tool_calls - pair.control.tool_calls:>+7}"
            )
    return "\n".join(lines)


def format_fault_experiment(report: Any, *, show_runs: bool = False) -> str:
    """Render paired outcomes and the fault's isolated handling mechanism."""

    if report.fault_name == FALLBACK_BUDGET_EXHAUSTION.name:
        return _format_budget_fault_experiment(report, show_runs=show_runs)
    if report.fault_name == PERMANENT_WORKER_FAILURE.name:
        return _format_worker_fault_experiment(report, show_runs=show_runs)

    first_seed = report.start_seed
    last_seed = report.start_seed + report.count - 1
    variant_mix = ", ".join(
        f"{variant}={count}" for variant, count in report.variant_counts
    )

    def seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:.2f}s"

    def signed_seconds(milliseconds: float | int | None) -> str:
        if milliseconds is None:
            return "n/a"
        return f"{milliseconds / 1000:+.2f}s"

    def rate_pair(control: float, faulted: float) -> str:
        return f"{control:.1f}%/{faulted:.1f}%"

    def time_pair(control: float | None, faulted: float | None) -> str:
        return f"{seconds(control)}/{seconds(faulted)}"

    lines = [
        (
            f"FAULT MATRIX — {report.fault_name} — "
            f"seeds {first_seed}..{last_seed}"
        ),
        (
            f"Cases: {report.count} · Paired rows: {len(report.pairs)} · "
            f"Runs: {2 * len(report.pairs)} · Variants: {variant_mix}"
        ),
        (
            "Conditions: matched_control uses the same comprehensive candidate "
            f"pipeline with no omission; fault={report.fault_name}."
        ),
        "",
        (
            "Strategy                   Recovery C/F     F-C     Action F  "
            "Policy safety F  Cost Δ  Calls Δ"
        ),
        (
            "-------------------------  ---------------  ------  --------  "
            "-------------  ------  -------"
        ),
    ]
    for item in report.aggregates:
        lines.append(
            f"{item.strategy:<25}  "
            f"{rate_pair(item.control_recovery_rate_pct, item.fault_recovery_rate_pct):>15}  "
            f"{item.fault_minus_control_recovery_rate_pp:>+5.1f}pp  "
            f"{item.fault_mean_required_action_recall_pct:>7.1f}%  "
            f"{item.fault_policy_safe_rate_pct:>12.1f}%  "
            f"{item.fault_minus_control_mean_cost_units:>+6.2f}  "
            f"{item.fault_minus_control_mean_tool_calls:>+7.2f}"
        )

    lines.extend(
        (
            "",
            "FAULT-ARM TRACE QUALITY",
            (
                "Strategy                   Diagnosis P/R F  Action C/F       "
                "Injected/Detected/Repaired F  Policy safety C/F"
            ),
            (
                "-------------------------  ---------------  ---------------  "
                "----------------------------  -----------------"
            ),
        )
    )
    for item in report.aggregates:
        diagnosis = (
            f"{item.fault_mean_diagnosis_precision_pct:.1f}%/"
            f"{item.fault_mean_diagnosis_recall_pct:.1f}%"
        )
        lifecycle = (
            f"{item.fault_injection_rate_pct:.1f}%/"
            f"{item.fault_detection_rate_pct:.1f}%/"
            f"{item.fault_repair_rate_pct:.1f}%"
        )
        lines.append(
            f"{item.strategy:<25}  {diagnosis:>15}  "
            f"{rate_pair(item.control_mean_required_action_recall_pct, item.fault_mean_required_action_recall_pct):>15}  "
            f"{lifecycle:>28}  "
            f"{rate_pair(item.control_policy_safe_rate_pct, item.fault_policy_safe_rate_pct):>17}"
        )

    lines.extend(
        (
            "",
            "CONFIRMED-RECOVERY LATENCY",
            (
                "Strategy                   Paired F-C (common n)  "
                "Control survivor mean (n/cases)  Fault survivor mean (n/cases)"
            ),
            (
                "-------------------------  ---------------------  "
                "-------------------------------  -----------------------------"
            ),
        )
    )
    for item in report.aggregates:
        paired_delta = signed_seconds(
            item.mean_fault_minus_control_confirmed_recovery_ms
        )
        paired = f"{paired_delta} (n={item.common_recovery_pairs})"
        control_survivors = (
            f"{seconds(item.control_mean_confirmed_recovery_ms)} "
            f"({item.control_confirmed_recovery_samples}/{item.cases})"
        )
        fault_survivors = (
            f"{seconds(item.fault_mean_confirmed_recovery_ms)} "
            f"({item.fault_confirmed_recovery_samples}/{item.cases})"
        )
        lines.append(
            f"{item.strategy:<25}  {paired:>21}  "
            f"{control_survivors:>31}  {fault_survivors:>29}"
        )

    effect = report.critic_effect
    common_latency = signed_seconds(
        effect.mean_fault_with_minus_without_confirmed_recovery_ms
    )
    lines.extend(
        (
            "",
            "INDEPENDENT CRITIC EFFECT — matched specialist presets",
            (
                "Fault recovery: "
                f"{effect.fault_without_critic_recovery_rate_pct:.1f}% without → "
                f"{effect.fault_with_critic_recovery_rate_pct:.1f}% with "
                f"({effect.fault_with_minus_without_recovery_rate_pp:+.1f}pp)"
            ),
            (
                "Control recovery lift: "
                f"{effect.control_with_minus_without_recovery_rate_pp:+.1f}pp"
            ),
            (
                "Recovery difference-in-differences: "
                f"{effect.recovery_difference_in_differences_pp:+.1f}pp"
            ),
            (
                "Fault rescues/regressions/net: "
                f"{effect.fault_rescues}/{effect.fault_regressions}/"
                f"{effect.fault_net_rescues:+d}"
            ),
            (
                "Fault required-action recall: "
                f"{effect.fault_without_critic_mean_required_action_recall_pct:.1f}% "
                f"without → {effect.fault_with_critic_mean_required_action_recall_pct:.1f}% "
                f"with ({effect.fault_with_minus_without_required_action_recall_pp:+.1f}pp)"
            ),
            (
                "Fault cost/call effect: "
                f"{effect.fault_with_minus_without_mean_cost_units:+.2f} units / "
                f"{effect.fault_with_minus_without_mean_tool_calls:+.2f} calls"
            ),
            (
                "Common-recovery latency effect: "
                f"{common_latency} (with minus without; "
                f"n={effect.fault_common_recovery_pairs})"
            ),
            "",
            (
                "Recovery requires valid trace verification plus execution of the "
                "evaluator-required action set. Paired latency deltas use only cases "
                "where both compared runs recovered; survivor means use recovered "
                "runs in each arm separately and show observed/cases denominators. "
                "Missing milestones remain n/a."
            ),
        )
    )

    if show_runs:
        lines.extend(
            (
                "",
                "PAIRED FAULT RUNS",
                (
                    "Seed  Variant                          Strategy                   "
                    "Recovery C/F  Action F  Confirmed C/F       Policy safety C/F  Cost Δ"
                ),
                (
                    "----  -------------------------------  -------------------------  "
                    "------------  --------  ------------------  ---------------  ------"
                ),
            )
        )
        for pair in report.pairs:
            recovery = (
                f"{'pass' if pair.control.recovered else 'fail'}/"
                f"{'pass' if pair.faulted.recovered else 'fail'}"
            )
            safe = (
                f"{'yes' if pair.control.policy_safe else 'no'}/"
                f"{'yes' if pair.faulted.policy_safe else 'no'}"
            )
            lines.append(
                f"{pair.seed:>4}  {pair.variant:<31}  {pair.strategy:<25}  "
                f"{recovery:>12}  {pair.faulted.required_action_recall_pct:>7.1f}%  "
                f"{time_pair(pair.control.confirmed_recovery_ms, pair.faulted.confirmed_recovery_ms):>18}  "
                f"{safe:>8}  "
                f"{pair.faulted.cost_units - pair.control.cost_units:>+6}"
            )
    return "\n".join(lines)
