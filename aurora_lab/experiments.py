"""Multi-seed experiments for measuring orchestration tradeoffs."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Iterable

from .faults import (
    FALLBACK_BUDGET_EXHAUSTION,
    INJECTABLE_FAULT_NAMES,
    NO_FAULT,
    PERMANENT_WORKER_FAILURE,
    PLANNING_OMISSION,
)
from .metrics import derive_run_metrics
from .model import EventKind, SimulationResult, TaskStatus
from .scenario import Scenario, build_scenario
from .simulation import (
    ABLATION_PRESETS,
    WORKER_FAILURE_PRESETS,
    run_comparison,
    run_preset,
)


BUDGET_FAULT_PRESET_NAMES = (
    "specialists_with_critic",
    "specialists_with_fallback",
)


@dataclass(frozen=True, slots=True)
class PairMeasurement:
    """Comparable measurements from two strategies on one seeded case."""

    seed: int
    variant: str
    orchestrated_success: bool
    baseline_success: bool
    orchestrated_score: float
    baseline_score: float
    orchestrated_last_action_ms: int | None
    baseline_last_action_ms: int | None
    orchestrated_first_slo_pass_ms: int | None
    baseline_first_slo_pass_ms: int | None
    orchestrated_confirmed_recovery_ms: int | None
    baseline_confirmed_recovery_ms: int | None
    orchestrated_total_ms: int
    baseline_total_ms: int
    orchestrated_cost_units: int
    baseline_cost_units: int
    orchestrated_tool_calls: int
    baseline_tool_calls: int

    @property
    def score_delta(self) -> float:
        return round(self.orchestrated_score - self.baseline_score, 2)

    @property
    def action_time_advantage_ms(self) -> int | None:
        """Positive when orchestration completes its final action sooner."""

        if (
            self.orchestrated_last_action_ms is None
            or self.baseline_last_action_ms is None
        ):
            return None
        return self.baseline_last_action_ms - self.orchestrated_last_action_ms


@dataclass(frozen=True, slots=True)
class StrategyAggregate:
    """Aggregate performance for one strategy over a paired case set."""

    runs: int
    success_rate: float
    mean_score: float
    minimum_score: float
    mean_last_action_ms: float | None
    last_action_samples: int
    mean_first_slo_pass_ms: float | None
    first_slo_pass_samples: int
    mean_confirmed_recovery_ms: float | None
    confirmed_recovery_samples: int
    mean_total_ms: float
    mean_cost_units: float
    mean_tool_calls: float


@dataclass(frozen=True, slots=True)
class VariantAggregate:
    """Paired aggregate for one incident family."""

    variant: str
    cases: int
    orchestrated_mean_score: float
    baseline_mean_score: float
    mean_score_delta: float
    mean_action_time_advantage_ms: float | None


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    """Stable output of a deterministic multi-seed experiment."""

    start_seed: int
    count: int
    variant_filter: str | None
    pairs: tuple[PairMeasurement, ...]
    orchestrated: StrategyAggregate
    baseline: StrategyAggregate
    orchestrated_wins: int
    baseline_wins: int
    ties: int
    variant_counts: tuple[tuple[str, int], ...]
    by_variant: tuple[VariantAggregate, ...]


@dataclass(frozen=True, slots=True)
class AblationMeasurement:
    """One strategy measurement for one seeded case."""

    seed: int
    variant: str
    strategy: str
    success: bool
    score: float
    last_action_ms: int | None
    first_slo_pass_ms: int | None
    confirmed_recovery_ms: int | None
    total_ms: int
    cost_units: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class AblationAggregate:
    """Aggregate measurements for one ablation preset."""

    strategy: str
    runs: int
    success_rate: float
    mean_score: float
    minimum_score: float
    mean_last_action_ms: float | None
    last_action_samples: int
    mean_first_slo_pass_ms: float | None
    first_slo_pass_samples: int
    mean_confirmed_recovery_ms: float | None
    confirmed_recovery_samples: int
    mean_total_ms: float
    mean_cost_units: float
    mean_tool_calls: float


@dataclass(frozen=True, slots=True)
class AblationReport:
    """Stable output of the five-preset orchestration ablation."""

    start_seed: int
    count: int
    variant_filter: str | None
    preset_order: tuple[str, ...]
    runs: tuple[AblationMeasurement, ...]
    aggregates: tuple[AblationAggregate, ...]
    variant_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FaultArmMeasurement:
    """Trace-derived outcomes for one arm of a paired fault intervention."""

    recovered: bool
    confirmed_recovery_ms: int | None
    diagnosis_precision_pct: float
    diagnosis_recall_pct: float
    required_action_recall_pct: float
    policy_safe: bool
    policy_violations: int
    fault_injected: bool
    fault_detected: bool
    fault_repaired: bool
    fault_target_action_ids: tuple[str, ...]
    cost_units: int
    tool_calls: int
    run_id: str = ""
    fault_arm: str = ""


@dataclass(frozen=True, slots=True)
class WorkerFaultArmMeasurement(FaultArmMeasurement):
    """Worker-loss facts kept separate from planning-repair semantics."""

    fault_target_task_ids: tuple[str, ...] = ()
    target_terminal_failure: bool = False
    reassignment_attempted: bool = False
    fallback_succeeded: bool = False
    fault_contained: bool = False
    fallback_task_ids: tuple[str, ...] = ()
    safe_degraded: bool = False
    required_evidence_tag_coverage_pct: float = 100.0
    missing_required_evidence_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetFaultArmMeasurement:
    """Trace-derived worker-loss and atomic fallback-budget outcomes."""

    recovered: bool
    confirmed_recovery_ms: int | None
    policy_safe: bool
    policy_violations: int
    safe_degraded: bool
    required_evidence_tag_coverage_pct: float
    missing_required_evidence_tags: tuple[str, ...]
    shared_worker_fault_injected: bool
    shared_worker_fault_detected: bool
    shared_worker_terminal_failure: bool
    fallback_succeeded: bool
    fault_contained: bool
    study_fault_injected: bool
    study_fault_detected: bool
    reservation_request_count: int
    reservation_grant_count: int
    reservation_denial_count: int
    fallback_skip_count: int
    invalid_reservation_count: int
    budget_safe: bool
    zero_budget_overrun: bool
    denied_zero_spend: bool
    denied_no_dispatch: bool
    binding_dimensions: tuple[str, ...]
    requested_attempts: int
    requested_cost_units: int
    requested_tool_calls: int
    requested_duration_ms: int
    capacity_attempts: int
    capacity_cost_units: int
    capacity_tool_calls: int
    capacity_duration_ms: int
    scoped_usage_attempts: int
    scoped_usage_cost_units: int
    scoped_usage_tool_calls: int
    scoped_usage_duration_ms: int
    budget_overrun_attempts: int
    budget_overrun_cost_units: int
    budget_overrun_tool_calls: int
    budget_overrun_duration_ms: int
    attempts_used: int
    cost_units: int
    tool_calls: int
    total_ms: int
    run_id: str = ""
    fault_arm: str = ""


@dataclass(frozen=True, slots=True)
class FaultPairMeasurement:
    """Control and fault outcomes for one seed, variant, and strategy."""

    seed: int
    variant: str
    strategy: str
    control: FaultArmMeasurement
    faulted: FaultArmMeasurement


@dataclass(frozen=True, slots=True)
class BudgetFaultPairMeasurement:
    """Exact-fit control and tight-budget outcomes for one matched case."""

    seed: int
    variant: str
    strategy: str
    control: BudgetFaultArmMeasurement
    faulted: BudgetFaultArmMeasurement


@dataclass(frozen=True, slots=True)
class FaultStrategyAggregate:
    """Paired fault effects for one orchestration preset."""

    strategy: str
    cases: int
    control_recovery_rate_pct: float
    fault_recovery_rate_pct: float
    fault_minus_control_recovery_rate_pp: float
    control_mean_confirmed_recovery_ms: float | None
    control_confirmed_recovery_samples: int
    fault_mean_confirmed_recovery_ms: float | None
    fault_confirmed_recovery_samples: int
    common_recovery_pairs: int
    mean_fault_minus_control_confirmed_recovery_ms: float | None
    control_mean_diagnosis_precision_pct: float
    fault_mean_diagnosis_precision_pct: float
    control_mean_diagnosis_recall_pct: float
    fault_mean_diagnosis_recall_pct: float
    control_mean_required_action_recall_pct: float
    fault_mean_required_action_recall_pct: float
    fault_minus_control_required_action_recall_pp: float
    control_policy_safe_rate_pct: float
    fault_policy_safe_rate_pct: float
    fault_minus_control_policy_safe_rate_pp: float
    fault_injection_rate_pct: float
    fault_detection_rate_pct: float
    fault_repair_rate_pct: float
    control_mean_cost_units: float
    fault_mean_cost_units: float
    fault_minus_control_mean_cost_units: float
    control_mean_tool_calls: float
    fault_mean_tool_calls: float
    fault_minus_control_mean_tool_calls: float


@dataclass(frozen=True, slots=True)
class WorkerFaultStrategyAggregate(FaultStrategyAggregate):
    """Aggregate lifecycle and evidence effects for terminal worker loss."""

    fault_target_terminal_failure_rate_pct: float = 0.0
    fault_reassignment_rate_pct: float = 0.0
    fault_fallback_success_rate_pct: float = 0.0
    fault_containment_rate_pct: float = 0.0
    fault_safe_degradation_rate_pct: float = 0.0
    control_mean_required_evidence_tag_coverage_pct: float = 100.0
    fault_mean_required_evidence_tag_coverage_pct: float = 100.0
    fault_minus_control_required_evidence_tag_coverage_pp: float = 0.0
    fault_missing_required_evidence_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetFaultStrategyAggregate:
    """Aggregate safety, admission, recovery, and resource effects."""

    strategy: str
    cases: int
    control_recovery_rate_pct: float
    fault_recovery_rate_pct: float
    fault_minus_control_recovery_rate_pp: float
    control_safe_degradation_rate_pct: float
    fault_safe_degradation_rate_pct: float
    control_policy_safe_rate_pct: float
    fault_policy_safe_rate_pct: float
    control_budget_safe_rate_pct: float
    fault_budget_safe_rate_pct: float
    control_zero_budget_overrun_rate_pct: float
    fault_zero_budget_overrun_rate_pct: float
    control_shared_worker_injection_rate_pct: float
    fault_shared_worker_injection_rate_pct: float
    control_shared_worker_detection_rate_pct: float
    fault_shared_worker_detection_rate_pct: float
    control_shared_worker_terminal_failure_rate_pct: float
    fault_shared_worker_terminal_failure_rate_pct: float
    fault_study_injection_rate_pct: float
    fault_study_detection_rate_pct: float
    control_fallback_success_rate_pct: float
    fault_fallback_success_rate_pct: float
    control_containment_rate_pct: float
    fault_containment_rate_pct: float
    control_mean_reservation_request_count: float
    fault_mean_reservation_request_count: float
    control_mean_reservation_grant_count: float
    fault_mean_reservation_grant_count: float
    control_mean_reservation_denial_count: float
    fault_mean_reservation_denial_count: float
    control_mean_fallback_skip_count: float
    fault_mean_fallback_skip_count: float
    control_denied_zero_spend_rate_pct: float
    fault_denied_zero_spend_rate_pct: float
    control_denied_no_dispatch_rate_pct: float
    fault_denied_no_dispatch_rate_pct: float
    control_mean_requested_cost_units: float
    fault_mean_requested_cost_units: float
    control_mean_capacity_cost_units: float
    fault_mean_capacity_cost_units: float
    control_mean_scoped_usage_cost_units: float
    fault_mean_scoped_usage_cost_units: float
    control_mean_attempts_used: float
    fault_mean_attempts_used: float
    fault_minus_control_mean_attempts_used: float
    control_mean_cost_units: float
    fault_mean_cost_units: float
    fault_minus_control_mean_cost_units: float
    control_mean_tool_calls: float
    fault_mean_tool_calls: float
    fault_minus_control_mean_tool_calls: float
    control_mean_total_ms: float
    fault_mean_total_ms: float
    fault_minus_control_mean_total_ms: float
    control_mean_confirmed_recovery_ms: float | None
    control_confirmed_recovery_samples: int
    fault_mean_confirmed_recovery_ms: float | None
    fault_confirmed_recovery_samples: int
    common_recovery_pairs: int
    mean_fault_minus_control_confirmed_recovery_ms: float | None


@dataclass(frozen=True, slots=True)
class CriticEffectAggregate:
    """Matched causal contrast for the isolated independent-critic toggle."""

    without_critic_strategy: str
    with_critic_strategy: str
    cases: int
    control_without_critic_recovery_rate_pct: float
    control_with_critic_recovery_rate_pct: float
    control_with_minus_without_recovery_rate_pp: float
    fault_without_critic_recovery_rate_pct: float
    fault_with_critic_recovery_rate_pct: float
    fault_with_minus_without_recovery_rate_pp: float
    recovery_difference_in_differences_pp: float
    fault_rescues: int
    fault_regressions: int
    fault_net_rescues: int
    fault_both_recovered: int
    fault_neither_recovered: int
    fault_without_critic_mean_required_action_recall_pct: float
    fault_with_critic_mean_required_action_recall_pct: float
    fault_with_minus_without_required_action_recall_pp: float
    fault_without_critic_policy_safe_rate_pct: float
    fault_with_critic_policy_safe_rate_pct: float
    fault_with_minus_without_policy_safe_rate_pp: float
    fault_without_critic_mean_cost_units: float
    fault_with_critic_mean_cost_units: float
    fault_with_minus_without_mean_cost_units: float
    fault_without_critic_mean_tool_calls: float
    fault_with_critic_mean_tool_calls: float
    fault_with_minus_without_mean_tool_calls: float
    fault_common_recovery_pairs: int
    mean_fault_with_minus_without_confirmed_recovery_ms: float | None


@dataclass(frozen=True, slots=True)
class FallbackEffectAggregate:
    """Matched causal contrast for the isolated reassignment toggle."""

    without_fallback_strategy: str
    with_fallback_strategy: str
    cases: int
    control_without_fallback_recovery_rate_pct: float
    control_with_fallback_recovery_rate_pct: float
    control_with_minus_without_recovery_rate_pp: float
    fault_without_fallback_recovery_rate_pct: float
    fault_with_fallback_recovery_rate_pct: float
    fault_with_minus_without_recovery_rate_pp: float
    recovery_difference_in_differences_pp: float
    fault_rescues: int
    fault_regressions: int
    fault_net_rescues: int
    fault_both_recovered: int
    fault_neither_recovered: int
    fault_without_fallback_containment_rate_pct: float
    fault_with_fallback_containment_rate_pct: float
    fault_with_minus_without_containment_rate_pp: float
    fault_without_fallback_safe_degradation_rate_pct: float
    fault_with_fallback_safe_degradation_rate_pct: float
    fault_with_minus_without_safe_degradation_rate_pp: float
    fault_without_fallback_mean_required_evidence_tag_coverage_pct: float
    fault_with_fallback_mean_required_evidence_tag_coverage_pct: float
    fault_with_minus_without_required_evidence_tag_coverage_pp: float
    fault_without_fallback_policy_safe_rate_pct: float
    fault_with_fallback_policy_safe_rate_pct: float
    fault_with_minus_without_policy_safe_rate_pp: float
    fault_without_fallback_mean_cost_units: float
    fault_with_fallback_mean_cost_units: float
    fault_with_minus_without_mean_cost_units: float
    fault_without_fallback_mean_tool_calls: float
    fault_with_fallback_mean_tool_calls: float
    fault_with_minus_without_mean_tool_calls: float
    fault_common_recovery_pairs: int
    mean_fault_with_minus_without_confirmed_recovery_ms: float | None


@dataclass(frozen=True, slots=True)
class BudgetFallbackEffectAggregate:
    """Difference-in-differences for fallback benefit under budget pressure."""

    without_fallback_strategy: str
    with_fallback_strategy: str
    cases: int
    exact_fit_without_fallback_recovery_rate_pct: float
    exact_fit_with_fallback_recovery_rate_pct: float
    exact_fit_fallback_benefit_pp: float
    tight_budget_without_fallback_recovery_rate_pct: float
    tight_budget_with_fallback_recovery_rate_pct: float
    tight_budget_fallback_benefit_pp: float
    fallback_benefit_difference_in_differences_pp: float
    exact_fit_rescues: int
    tight_budget_rescues: int
    lost_rescues: int
    exact_fit_regressions: int
    tight_budget_regressions: int
    exact_fit_with_fallback_success_rate_pct: float
    tight_budget_with_fallback_success_rate_pct: float
    exact_fit_with_fallback_containment_rate_pct: float
    tight_budget_with_fallback_containment_rate_pct: float
    exact_fit_with_fallback_safe_degradation_rate_pct: float
    tight_budget_with_fallback_safe_degradation_rate_pct: float
    exact_fit_with_fallback_budget_safe_rate_pct: float
    tight_budget_with_fallback_budget_safe_rate_pct: float
    exact_fit_with_fallback_grant_rate_pct: float
    tight_budget_with_fallback_denial_rate_pct: float
    tight_budget_with_fallback_skip_rate_pct: float
    tight_budget_with_fallback_zero_spend_rate_pct: float
    tight_budget_with_fallback_no_dispatch_rate_pct: float
    tight_minus_exact_without_fallback_mean_attempts_used: float
    tight_minus_exact_without_fallback_mean_cost_units: float
    tight_minus_exact_without_fallback_mean_tool_calls: float
    tight_minus_exact_without_fallback_mean_total_ms: float
    tight_minus_exact_with_fallback_mean_attempts_used: float
    tight_minus_exact_with_fallback_mean_cost_units: float
    tight_minus_exact_with_fallback_mean_tool_calls: float
    tight_minus_exact_with_fallback_mean_total_ms: float


@dataclass(frozen=True, slots=True)
class FaultExperimentReport:
    """Stable paired control/fault matrix over all five presets."""

    start_seed: int
    count: int
    variant_filter: str | None
    fault_name: str
    preset_order: tuple[str, ...]
    condition_order: tuple[str, ...]
    pairs: tuple[FaultPairMeasurement, ...]
    aggregates: tuple[FaultStrategyAggregate, ...]
    variant_counts: tuple[tuple[str, int], ...]
    critic_effect: CriticEffectAggregate


@dataclass(frozen=True, slots=True)
class WorkerFaultExperimentReport:
    """Stable paired worker-loss matrix with an isolated fallback contrast."""

    start_seed: int
    count: int
    variant_filter: str | None
    fault_name: str
    preset_order: tuple[str, ...]
    condition_order: tuple[str, ...]
    pairs: tuple[FaultPairMeasurement, ...]
    aggregates: tuple[WorkerFaultStrategyAggregate, ...]
    variant_counts: tuple[tuple[str, int], ...]
    fallback_effect: FallbackEffectAggregate
    critic_effect: None = None


@dataclass(frozen=True, slots=True)
class BudgetFaultExperimentReport:
    """Exact-fit versus tight fallback-budget matrix with a shared worker loss."""

    start_seed: int
    count: int
    variant_filter: str | None
    fault_name: str
    preset_order: tuple[str, ...]
    condition_order: tuple[str, ...]
    pairs: tuple[BudgetFaultPairMeasurement, ...]
    aggregates: tuple[BudgetFaultStrategyAggregate, ...]
    variant_counts: tuple[tuple[str, int], ...]
    fallback_effect: BudgetFallbackEffectAggregate
    critic_effect: None = None


def _metadata(result: SimulationResult) -> dict[str, str]:
    return dict(result.metadata)


def _mean_optional(values: Iterable[int | None]) -> tuple[float | None, int]:
    present = tuple(float(value) for value in values if value is not None)
    if not present:
        return None, 0
    return round(fmean(present), 2), len(present)


def _measure(seed: int, variant_name: str | None) -> PairMeasurement:
    orchestrated, baseline = run_comparison(seed, variant_name)
    orchestrated_metrics = derive_run_metrics(orchestrated)
    baseline_metrics = derive_run_metrics(baseline)
    variant = _metadata(orchestrated)["variant"]
    return PairMeasurement(
        seed=seed,
        variant=variant,
        orchestrated_success=orchestrated.status is TaskStatus.SUCCEEDED,
        baseline_success=baseline.status is TaskStatus.SUCCEEDED,
        orchestrated_score=orchestrated.score.total if orchestrated.score else 0.0,
        baseline_score=baseline.score.total if baseline.score else 0.0,
        orchestrated_last_action_ms=orchestrated_metrics.last_action_ms,
        baseline_last_action_ms=baseline_metrics.last_action_ms,
        orchestrated_first_slo_pass_ms=orchestrated_metrics.first_slo_pass_ms,
        baseline_first_slo_pass_ms=baseline_metrics.first_slo_pass_ms,
        orchestrated_confirmed_recovery_ms=(
            orchestrated_metrics.confirmed_recovery_ms
        ),
        baseline_confirmed_recovery_ms=baseline_metrics.confirmed_recovery_ms,
        orchestrated_total_ms=orchestrated.ended_at_ms - orchestrated.started_at_ms,
        baseline_total_ms=baseline.ended_at_ms - baseline.started_at_ms,
        orchestrated_cost_units=orchestrated.cost_units_used,
        baseline_cost_units=baseline.cost_units_used,
        orchestrated_tool_calls=orchestrated.tool_calls_used,
        baseline_tool_calls=baseline.tool_calls_used,
    )


def _aggregate(
    pairs: tuple[PairMeasurement, ...],
    *,
    orchestrated: bool,
) -> StrategyAggregate:
    prefix = "orchestrated" if orchestrated else "baseline"

    def values(suffix: str) -> tuple[float, ...]:
        return tuple(float(getattr(pair, f"{prefix}_{suffix}")) for pair in pairs)

    def optional_values(suffix: str) -> tuple[int | None, ...]:
        return tuple(getattr(pair, f"{prefix}_{suffix}") for pair in pairs)

    successes = values("success")
    scores = values("score")
    mean_last_action, last_action_samples = _mean_optional(
        optional_values("last_action_ms")
    )
    mean_first_slo, first_slo_samples = _mean_optional(
        optional_values("first_slo_pass_ms")
    )
    mean_confirmed, confirmed_samples = _mean_optional(
        optional_values("confirmed_recovery_ms")
    )
    return StrategyAggregate(
        runs=len(pairs),
        success_rate=round(100.0 * fmean(successes), 2),
        mean_score=round(fmean(scores), 2),
        minimum_score=round(min(scores), 2),
        mean_last_action_ms=mean_last_action,
        last_action_samples=last_action_samples,
        mean_first_slo_pass_ms=mean_first_slo,
        first_slo_pass_samples=first_slo_samples,
        mean_confirmed_recovery_ms=mean_confirmed,
        confirmed_recovery_samples=confirmed_samples,
        mean_total_ms=round(fmean(values("total_ms")), 2),
        mean_cost_units=round(fmean(values("cost_units")), 2),
        mean_tool_calls=round(fmean(values("tool_calls")), 2),
    )


def _variant_aggregates(
    pairs: tuple[PairMeasurement, ...],
) -> tuple[VariantAggregate, ...]:
    variants = sorted({pair.variant for pair in pairs})
    aggregates: list[VariantAggregate] = []
    for variant in variants:
        group = tuple(pair for pair in pairs if pair.variant == variant)
        aggregates.append(
            VariantAggregate(
                variant=variant,
                cases=len(group),
                orchestrated_mean_score=round(
                    fmean(pair.orchestrated_score for pair in group), 2
                ),
                baseline_mean_score=round(
                    fmean(pair.baseline_score for pair in group), 2
                ),
                mean_score_delta=round(fmean(pair.score_delta for pair in group), 2),
                mean_action_time_advantage_ms=_mean_optional(
                    pair.action_time_advantage_ms for pair in group
                )[0],
            )
        )
    return tuple(aggregates)


def run_experiment(
    *,
    start_seed: int = 0,
    count: int = 30,
    variant_name: str | None = None,
) -> ExperimentReport:
    """Run a deterministic paired matrix over consecutive seeds."""

    if not isinstance(start_seed, int):
        raise TypeError("start_seed must be an integer")
    if not isinstance(count, int):
        raise TypeError("count must be an integer")
    if count < 1:
        raise ValueError("count must be at least 1")

    pairs = tuple(
        _measure(seed, variant_name)
        for seed in range(start_seed, start_seed + count)
    )
    variant_counts = tuple(
        (variant, sum(pair.variant == variant for pair in pairs))
        for variant in sorted({pair.variant for pair in pairs})
    )
    orchestrated_wins = sum(pair.score_delta > 0 for pair in pairs)
    baseline_wins = sum(pair.score_delta < 0 for pair in pairs)
    ties = len(pairs) - orchestrated_wins - baseline_wins
    return ExperimentReport(
        start_seed=start_seed,
        count=count,
        variant_filter=variant_name,
        pairs=pairs,
        orchestrated=_aggregate(pairs, orchestrated=True),
        baseline=_aggregate(pairs, orchestrated=False),
        orchestrated_wins=orchestrated_wins,
        baseline_wins=baseline_wins,
        ties=ties,
        variant_counts=variant_counts,
        by_variant=_variant_aggregates(pairs),
    )


def _measure_ablation(
    seed: int,
    variant_name: str | None,
    strategy: str,
) -> AblationMeasurement:
    result = run_preset(strategy, seed, variant_name)
    metrics = derive_run_metrics(result)
    return AblationMeasurement(
        seed=seed,
        variant=_metadata(result)["variant"],
        strategy=strategy,
        success=result.status is TaskStatus.SUCCEEDED,
        score=result.score.total if result.score else 0.0,
        last_action_ms=metrics.last_action_ms,
        first_slo_pass_ms=metrics.first_slo_pass_ms,
        confirmed_recovery_ms=metrics.confirmed_recovery_ms,
        total_ms=result.ended_at_ms - result.started_at_ms,
        cost_units=result.cost_units_used,
        tool_calls=result.tool_calls_used,
    )


def _aggregate_ablation(
    strategy: str,
    runs: tuple[AblationMeasurement, ...],
) -> AblationAggregate:
    group = tuple(run for run in runs if run.strategy == strategy)
    mean_last_action, last_action_samples = _mean_optional(
        run.last_action_ms for run in group
    )
    mean_first_slo, first_slo_samples = _mean_optional(
        run.first_slo_pass_ms for run in group
    )
    mean_confirmed, confirmed_samples = _mean_optional(
        run.confirmed_recovery_ms for run in group
    )
    return AblationAggregate(
        strategy=strategy,
        runs=len(group),
        success_rate=round(100.0 * fmean(run.success for run in group), 2),
        mean_score=round(fmean(run.score for run in group), 2),
        minimum_score=round(min(run.score for run in group), 2),
        mean_last_action_ms=mean_last_action,
        last_action_samples=last_action_samples,
        mean_first_slo_pass_ms=mean_first_slo,
        first_slo_pass_samples=first_slo_samples,
        mean_confirmed_recovery_ms=mean_confirmed,
        confirmed_recovery_samples=confirmed_samples,
        mean_total_ms=round(fmean(run.total_ms for run in group), 2),
        mean_cost_units=round(fmean(run.cost_units for run in group), 2),
        mean_tool_calls=round(fmean(run.tool_calls for run in group), 2),
    )


def run_ablation_experiment(
    *,
    start_seed: int = 0,
    count: int = 30,
    variant_name: str | None = None,
) -> AblationReport:
    """Run all five presets on every case in deterministic seed-major order."""

    if not isinstance(start_seed, int):
        raise TypeError("start_seed must be an integer")
    if not isinstance(count, int):
        raise TypeError("count must be an integer")
    if count < 1:
        raise ValueError("count must be at least 1")

    preset_order = tuple(preset.name for preset in ABLATION_PRESETS)
    runs = tuple(
        _measure_ablation(seed, variant_name, strategy)
        for seed in range(start_seed, start_seed + count)
        for strategy in preset_order
    )
    variants = sorted({run.variant for run in runs})
    variant_counts = tuple(
        (
            variant,
            len({run.seed for run in runs if run.variant == variant}),
        )
        for variant in variants
    )
    return AblationReport(
        start_seed=start_seed,
        count=count,
        variant_filter=variant_name,
        preset_order=preset_order,
        runs=runs,
        aggregates=tuple(
            _aggregate_ablation(strategy, runs) for strategy in preset_order
        ),
        variant_counts=variant_counts,
    )


def _percentage(values: Iterable[bool]) -> float:
    observed = tuple(values)
    return round(100.0 * fmean(observed), 2)


def _mean(values: Iterable[float | int]) -> float:
    return round(fmean(tuple(values)), 2)


def _diagnosis_quality(
    predicted: frozenset[str],
    expected: frozenset[str],
) -> tuple[float, float]:
    correct = len(predicted & expected)
    precision = correct / len(predicted) if predicted else float(not expected)
    recall = correct / len(expected) if expected else float(not predicted)
    return round(100.0 * precision, 2), round(100.0 * recall, 2)


def _required_evidence_quality(
    result: SimulationResult,
    scenario: Scenario,
) -> tuple[float, tuple[str, ...]]:
    """Measure trusted coverage of tags needed to approve required actions."""

    required_tags = frozenset(
        tag
        for policy in scenario.action_policies
        if policy.action_id in scenario.required_actions
        for tag in policy.required_evidence_tags
    )
    committed_items = frozenset(
        (dict(event.metadata).get("evidence_id"), event.task_id)
        for event in result.trace
        if event.kind is EventKind.EVIDENCE_COMMITTED
    )
    result_items = frozenset(
        (evidence_id, task.task_id)
        for task in result.task_results
        for evidence_id in task.evidence_ids
    )
    committed_tags = frozenset(
        tag.strip()
        for item in result.evidence
        if item.trusted
        and (item.evidence_id, item.task_id) in committed_items
        and (item.evidence_id, item.task_id) in result_items
        for tag in dict(item.metadata).get("tags", "").split(",")
        if tag.strip()
    )
    missing = tuple(sorted(required_tags - committed_tags))
    coverage = (
        100.0 * len(required_tags & committed_tags) / len(required_tags)
        if required_tags
        else 100.0
    )
    return round(coverage, 2), missing


def _measure_fault_arm(
    result: SimulationResult,
    scenario: Scenario,
) -> FaultArmMeasurement:
    """Measure one arm without trusting result status or summary metadata."""

    metrics = derive_run_metrics(result)
    predicted_causes = frozenset(metrics.diagnosis_causes)
    precision, recall = _diagnosis_quality(
        predicted_causes,
        scenario.private_truth,
    )
    executed_actions = frozenset(metrics.executed_action_ids)
    required_actions = scenario.required_actions
    recovered = metrics.recovered and required_actions.issubset(executed_actions)
    action_recall = (
        100.0 * len(executed_actions & required_actions) / len(required_actions)
        if required_actions
        else 100.0
    )
    allowed_actions = {
        policy.action_id for policy in scenario.action_policies if policy.allowed
    }
    out_of_policy = sum(
        action_id not in allowed_actions for action_id in metrics.executed_action_ids
    )
    policy_violations = metrics.approval_violations + out_of_policy
    base_values = dict(
        recovered=recovered,
        confirmed_recovery_ms=(
            metrics.confirmed_recovery_ms if recovered else None
        ),
        diagnosis_precision_pct=precision,
        diagnosis_recall_pct=recall,
        required_action_recall_pct=round(action_recall, 2),
        policy_safe=policy_violations == 0,
        policy_violations=policy_violations,
        fault_injected=metrics.fault_injected,
        fault_detected=metrics.fault_detected,
        fault_repaired=metrics.fault_repaired,
        fault_target_action_ids=metrics.fault_target_action_ids,
        cost_units=result.cost_units_used,
        tool_calls=result.tool_calls_used,
        run_id=result.run_id,
        fault_arm=scenario.fault_arm,
    )
    if scenario.fault_study_name != PERMANENT_WORKER_FAILURE.name:
        return FaultArmMeasurement(**base_values)

    evidence_coverage, missing_evidence = _required_evidence_quality(
        result,
        scenario,
    )
    policy_safe = policy_violations == 0
    safe_degraded = (
        not recovered
        and policy_safe
        and metrics.oversold_seats == 0
        and precision == 100.0
        and recall == 100.0
        and bool(missing_evidence)
        and evidence_coverage < 100.0
    )
    return WorkerFaultArmMeasurement(
        **base_values,
        fault_target_task_ids=metrics.fault_target_task_ids,
        target_terminal_failure=metrics.target_terminal_failure,
        reassignment_attempted=metrics.reassignment_attempted,
        fallback_succeeded=metrics.fallback_succeeded,
        fault_contained=metrics.fault_contained,
        fallback_task_ids=metrics.fallback_task_ids,
        safe_degraded=safe_degraded,
        required_evidence_tag_coverage_pct=evidence_coverage,
        missing_required_evidence_tags=missing_evidence,
    )


def _measure_fault_pair(
    seed: int,
    variant_name: str | None,
    strategy: str,
    fault_name: str,
) -> FaultPairMeasurement:
    scenario = build_scenario(seed, variant_name, NO_FAULT.name)
    # Pass the resolved variant into both arms so the fault is the only input
    # intervention even when the caller lets the seed select the case family.
    resolved_variant = scenario.variant_name
    control_scenario = build_scenario(
        seed,
        resolved_variant,
        control_for_fault=fault_name,
    )
    fault_scenario = build_scenario(seed, resolved_variant, fault_name)
    control = run_preset(
        strategy,
        seed,
        resolved_variant,
        control_for_fault=fault_name,
    )
    faulted = run_preset(strategy, seed, resolved_variant, fault_name)
    return FaultPairMeasurement(
        seed=seed,
        variant=resolved_variant,
        strategy=strategy,
        control=_measure_fault_arm(control, control_scenario),
        faulted=_measure_fault_arm(faulted, fault_scenario),
    )


def _measure_budget_fault_arm(
    result: SimulationResult,
    scenario: Scenario,
) -> BudgetFaultArmMeasurement:
    """Measure the budget intervention separately from the shared worker loss."""

    metrics = derive_run_metrics(result)
    executed_actions = frozenset(metrics.executed_action_ids)
    recovered = (
        metrics.recovered
        and scenario.required_actions.issubset(executed_actions)
    )
    allowed_actions = {
        policy.action_id for policy in scenario.action_policies if policy.allowed
    }
    out_of_policy = sum(
        action_id not in allowed_actions for action_id in metrics.executed_action_ids
    )
    policy_violations = metrics.approval_violations + out_of_policy
    policy_safe = policy_violations == 0
    precision, recall = _diagnosis_quality(
        frozenset(metrics.diagnosis_causes),
        scenario.private_truth,
    )
    evidence_coverage, missing_evidence = _required_evidence_quality(
        result,
        scenario,
    )
    safe_degraded = (
        not recovered
        and policy_safe
        and metrics.oversold_seats == 0
        and precision == 100.0
        and recall == 100.0
        and bool(missing_evidence)
        and evidence_coverage < 100.0
    )
    facts = metrics.budget_reservations

    def vector_total(prefix: str, dimension: str) -> int:
        return sum(
            int(getattr(fact, f"{prefix}_{dimension}")) for fact in facts
        )

    overrun_attempts = sum(
        max(fact.scoped_usage_attempts - fact.capacity_attempts, 0)
        for fact in facts
    )
    overrun_cost = sum(
        max(fact.scoped_usage_cost_units - fact.capacity_cost_units, 0)
        for fact in facts
    )
    overrun_calls = sum(
        max(fact.scoped_usage_tool_calls - fact.capacity_tool_calls, 0)
        for fact in facts
    )
    overrun_duration = sum(
        max(fact.scoped_usage_duration_ms - fact.capacity_duration_ms, 0)
        for fact in facts
    )
    denial_facts = tuple(fact for fact in facts if fact.decision == "denied")
    shared_ids = frozenset(
        metadata.get("fault_id", "")
        for event in result.trace
        for metadata in (dict(event.metadata),)
        if event.kind is EventKind.FAULT_INJECTED
        and metadata.get("fault_name") == PERMANENT_WORKER_FAILURE.name
        and metadata.get("fault_role") == "shared_condition"
        and metadata.get("fault_id") in metrics.injected_fault_ids
    )
    shared_detected_ids = frozenset(
        metadata.get("fault_id", "")
        for event in result.trace
        for metadata in (dict(event.metadata),)
        if event.kind is EventKind.FAULT_DETECTED
        and metadata.get("fault_name") == PERMANENT_WORKER_FAILURE.name
        and metadata.get("fault_role") == "shared_condition"
        and metadata.get("fault_id") in metrics.detected_fault_ids
    )
    return BudgetFaultArmMeasurement(
        recovered=recovered,
        confirmed_recovery_ms=(
            metrics.confirmed_recovery_ms if recovered else None
        ),
        policy_safe=policy_safe,
        policy_violations=policy_violations,
        safe_degraded=safe_degraded,
        required_evidence_tag_coverage_pct=evidence_coverage,
        missing_required_evidence_tags=missing_evidence,
        shared_worker_fault_injected=bool(shared_ids),
        shared_worker_fault_detected=bool(shared_detected_ids),
        shared_worker_terminal_failure=bool(
            shared_ids.intersection(metrics.terminal_fault_ids)
        ),
        fallback_succeeded=metrics.fallback_succeeded,
        fault_contained=metrics.fault_contained,
        study_fault_injected=metrics.study_fault_injected,
        study_fault_detected=metrics.study_fault_detected,
        reservation_request_count=len(facts),
        reservation_grant_count=sum(fact.decision == "granted" for fact in facts),
        reservation_denial_count=len(denial_facts),
        fallback_skip_count=sum(fact.skipped for fact in facts),
        invalid_reservation_count=sum(not fact.valid for fact in facts),
        budget_safe=metrics.budget_safe,
        zero_budget_overrun=(
            overrun_attempts == 0
            and overrun_cost == 0
            and overrun_calls == 0
            and overrun_duration == 0
        ),
        denied_zero_spend=all(fact.zero_spend for fact in denial_facts),
        denied_no_dispatch=all(fact.no_dispatch for fact in denial_facts),
        binding_dimensions=tuple(sorted({
            dimension
            for fact in facts
            for dimension in fact.binding_dimensions
        })),
        requested_attempts=vector_total("requested", "attempts"),
        requested_cost_units=vector_total("requested", "cost_units"),
        requested_tool_calls=vector_total("requested", "tool_calls"),
        requested_duration_ms=vector_total("requested", "duration_ms"),
        capacity_attempts=vector_total("capacity", "attempts"),
        capacity_cost_units=vector_total("capacity", "cost_units"),
        capacity_tool_calls=vector_total("capacity", "tool_calls"),
        capacity_duration_ms=vector_total("capacity", "duration_ms"),
        scoped_usage_attempts=vector_total("scoped_usage", "attempts"),
        scoped_usage_cost_units=vector_total("scoped_usage", "cost_units"),
        scoped_usage_tool_calls=vector_total("scoped_usage", "tool_calls"),
        scoped_usage_duration_ms=vector_total("scoped_usage", "duration_ms"),
        budget_overrun_attempts=overrun_attempts,
        budget_overrun_cost_units=overrun_cost,
        budget_overrun_tool_calls=overrun_calls,
        budget_overrun_duration_ms=overrun_duration,
        attempts_used=result.attempts_used,
        cost_units=result.cost_units_used,
        tool_calls=result.tool_calls_used,
        total_ms=result.ended_at_ms - result.started_at_ms,
        run_id=result.run_id,
        fault_arm=scenario.fault_arm,
    )


def _measure_budget_fault_pair(
    seed: int,
    variant_name: str | None,
    strategy: str,
) -> BudgetFaultPairMeasurement:
    selected = build_scenario(seed, variant_name, NO_FAULT.name)
    resolved_variant = selected.variant_name
    control_scenario = build_scenario(
        seed,
        resolved_variant,
        control_for_fault=FALLBACK_BUDGET_EXHAUSTION.name,
    )
    fault_scenario = build_scenario(
        seed,
        resolved_variant,
        FALLBACK_BUDGET_EXHAUSTION.name,
    )
    control = run_preset(
        strategy,
        seed,
        resolved_variant,
        control_for_fault=FALLBACK_BUDGET_EXHAUSTION.name,
    )
    faulted = run_preset(
        strategy,
        seed,
        resolved_variant,
        FALLBACK_BUDGET_EXHAUSTION.name,
    )
    return BudgetFaultPairMeasurement(
        seed=seed,
        variant=resolved_variant,
        strategy=strategy,
        control=_measure_budget_fault_arm(control, control_scenario),
        faulted=_measure_budget_fault_arm(faulted, fault_scenario),
    )


def _aggregate_budget_fault_strategy(
    strategy: str,
    pairs: tuple[BudgetFaultPairMeasurement, ...],
) -> BudgetFaultStrategyAggregate:
    group = tuple(pair for pair in pairs if pair.strategy == strategy)

    def control_mean(field: str) -> float:
        return _mean(getattr(pair.control, field) for pair in group)

    def fault_mean(field: str) -> float:
        return _mean(getattr(pair.faulted, field) for pair in group)

    def control_rate(field: str) -> float:
        return _percentage(getattr(pair.control, field) for pair in group)

    def fault_rate(field: str) -> float:
        return _percentage(getattr(pair.faulted, field) for pair in group)

    control_recovery = control_rate("recovered")
    fault_recovery = fault_rate("recovered")
    control_attempts = control_mean("attempts_used")
    fault_attempts = fault_mean("attempts_used")
    control_cost = control_mean("cost_units")
    fault_cost = fault_mean("cost_units")
    control_calls = control_mean("tool_calls")
    fault_calls = fault_mean("tool_calls")
    control_total = control_mean("total_ms")
    fault_total = fault_mean("total_ms")
    control_confirmed, control_confirmed_samples = _mean_optional(
        pair.control.confirmed_recovery_ms for pair in group
    )
    fault_confirmed, fault_confirmed_samples = _mean_optional(
        pair.faulted.confirmed_recovery_ms for pair in group
    )
    common_deltas = tuple(
        pair.faulted.confirmed_recovery_ms - pair.control.confirmed_recovery_ms
        for pair in group
        if pair.faulted.confirmed_recovery_ms is not None
        and pair.control.confirmed_recovery_ms is not None
    )
    return BudgetFaultStrategyAggregate(
        strategy=strategy,
        cases=len(group),
        control_recovery_rate_pct=control_recovery,
        fault_recovery_rate_pct=fault_recovery,
        fault_minus_control_recovery_rate_pp=round(
            fault_recovery - control_recovery, 2
        ),
        control_safe_degradation_rate_pct=control_rate("safe_degraded"),
        fault_safe_degradation_rate_pct=fault_rate("safe_degraded"),
        control_policy_safe_rate_pct=control_rate("policy_safe"),
        fault_policy_safe_rate_pct=fault_rate("policy_safe"),
        control_budget_safe_rate_pct=control_rate("budget_safe"),
        fault_budget_safe_rate_pct=fault_rate("budget_safe"),
        control_zero_budget_overrun_rate_pct=control_rate(
            "zero_budget_overrun"
        ),
        fault_zero_budget_overrun_rate_pct=fault_rate("zero_budget_overrun"),
        control_shared_worker_injection_rate_pct=control_rate(
            "shared_worker_fault_injected"
        ),
        fault_shared_worker_injection_rate_pct=fault_rate(
            "shared_worker_fault_injected"
        ),
        control_shared_worker_detection_rate_pct=control_rate(
            "shared_worker_fault_detected"
        ),
        fault_shared_worker_detection_rate_pct=fault_rate(
            "shared_worker_fault_detected"
        ),
        control_shared_worker_terminal_failure_rate_pct=control_rate(
            "shared_worker_terminal_failure"
        ),
        fault_shared_worker_terminal_failure_rate_pct=fault_rate(
            "shared_worker_terminal_failure"
        ),
        fault_study_injection_rate_pct=fault_rate("study_fault_injected"),
        fault_study_detection_rate_pct=fault_rate("study_fault_detected"),
        control_fallback_success_rate_pct=control_rate("fallback_succeeded"),
        fault_fallback_success_rate_pct=fault_rate("fallback_succeeded"),
        control_containment_rate_pct=control_rate("fault_contained"),
        fault_containment_rate_pct=fault_rate("fault_contained"),
        control_mean_reservation_request_count=control_mean(
            "reservation_request_count"
        ),
        fault_mean_reservation_request_count=fault_mean(
            "reservation_request_count"
        ),
        control_mean_reservation_grant_count=control_mean(
            "reservation_grant_count"
        ),
        fault_mean_reservation_grant_count=fault_mean(
            "reservation_grant_count"
        ),
        control_mean_reservation_denial_count=control_mean(
            "reservation_denial_count"
        ),
        fault_mean_reservation_denial_count=fault_mean(
            "reservation_denial_count"
        ),
        control_mean_fallback_skip_count=control_mean("fallback_skip_count"),
        fault_mean_fallback_skip_count=fault_mean("fallback_skip_count"),
        control_denied_zero_spend_rate_pct=control_rate("denied_zero_spend"),
        fault_denied_zero_spend_rate_pct=fault_rate("denied_zero_spend"),
        control_denied_no_dispatch_rate_pct=control_rate("denied_no_dispatch"),
        fault_denied_no_dispatch_rate_pct=fault_rate("denied_no_dispatch"),
        control_mean_requested_cost_units=control_mean("requested_cost_units"),
        fault_mean_requested_cost_units=fault_mean("requested_cost_units"),
        control_mean_capacity_cost_units=control_mean("capacity_cost_units"),
        fault_mean_capacity_cost_units=fault_mean("capacity_cost_units"),
        control_mean_scoped_usage_cost_units=control_mean(
            "scoped_usage_cost_units"
        ),
        fault_mean_scoped_usage_cost_units=fault_mean(
            "scoped_usage_cost_units"
        ),
        control_mean_attempts_used=control_attempts,
        fault_mean_attempts_used=fault_attempts,
        fault_minus_control_mean_attempts_used=round(
            fault_attempts - control_attempts, 2
        ),
        control_mean_cost_units=control_cost,
        fault_mean_cost_units=fault_cost,
        fault_minus_control_mean_cost_units=round(fault_cost - control_cost, 2),
        control_mean_tool_calls=control_calls,
        fault_mean_tool_calls=fault_calls,
        fault_minus_control_mean_tool_calls=round(fault_calls - control_calls, 2),
        control_mean_total_ms=control_total,
        fault_mean_total_ms=fault_total,
        fault_minus_control_mean_total_ms=round(fault_total - control_total, 2),
        control_mean_confirmed_recovery_ms=control_confirmed,
        control_confirmed_recovery_samples=control_confirmed_samples,
        fault_mean_confirmed_recovery_ms=fault_confirmed,
        fault_confirmed_recovery_samples=fault_confirmed_samples,
        common_recovery_pairs=len(common_deltas),
        mean_fault_minus_control_confirmed_recovery_ms=(
            round(fmean(common_deltas), 2) if common_deltas else None
        ),
    )


def _aggregate_fault_strategy(
    strategy: str,
    pairs: tuple[FaultPairMeasurement, ...],
) -> FaultStrategyAggregate:
    group = tuple(pair for pair in pairs if pair.strategy == strategy)
    control_recovery_rate = _percentage(pair.control.recovered for pair in group)
    fault_recovery_rate = _percentage(pair.faulted.recovered for pair in group)
    control_confirmed, control_confirmed_samples = _mean_optional(
        pair.control.confirmed_recovery_ms for pair in group
    )
    fault_confirmed, fault_confirmed_samples = _mean_optional(
        pair.faulted.confirmed_recovery_ms for pair in group
    )
    common_deltas = tuple(
        pair.faulted.confirmed_recovery_ms - pair.control.confirmed_recovery_ms
        for pair in group
        if pair.faulted.confirmed_recovery_ms is not None
        and pair.control.confirmed_recovery_ms is not None
    )
    common_delta = round(fmean(common_deltas), 2) if common_deltas else None
    control_action_recall = _mean(
        pair.control.required_action_recall_pct for pair in group
    )
    fault_action_recall = _mean(
        pair.faulted.required_action_recall_pct for pair in group
    )
    control_policy_safe = _percentage(pair.control.policy_safe for pair in group)
    fault_policy_safe = _percentage(pair.faulted.policy_safe for pair in group)
    control_cost = _mean(pair.control.cost_units for pair in group)
    fault_cost = _mean(pair.faulted.cost_units for pair in group)
    control_calls = _mean(pair.control.tool_calls for pair in group)
    fault_calls = _mean(pair.faulted.tool_calls for pair in group)
    base_values = dict(
        strategy=strategy,
        cases=len(group),
        control_recovery_rate_pct=control_recovery_rate,
        fault_recovery_rate_pct=fault_recovery_rate,
        fault_minus_control_recovery_rate_pp=round(
            fault_recovery_rate - control_recovery_rate, 2
        ),
        control_mean_confirmed_recovery_ms=control_confirmed,
        control_confirmed_recovery_samples=control_confirmed_samples,
        fault_mean_confirmed_recovery_ms=fault_confirmed,
        fault_confirmed_recovery_samples=fault_confirmed_samples,
        common_recovery_pairs=len(common_deltas),
        mean_fault_minus_control_confirmed_recovery_ms=common_delta,
        control_mean_diagnosis_precision_pct=_mean(
            pair.control.diagnosis_precision_pct for pair in group
        ),
        fault_mean_diagnosis_precision_pct=_mean(
            pair.faulted.diagnosis_precision_pct for pair in group
        ),
        control_mean_diagnosis_recall_pct=_mean(
            pair.control.diagnosis_recall_pct for pair in group
        ),
        fault_mean_diagnosis_recall_pct=_mean(
            pair.faulted.diagnosis_recall_pct for pair in group
        ),
        control_mean_required_action_recall_pct=control_action_recall,
        fault_mean_required_action_recall_pct=fault_action_recall,
        fault_minus_control_required_action_recall_pp=round(
            fault_action_recall - control_action_recall, 2
        ),
        control_policy_safe_rate_pct=control_policy_safe,
        fault_policy_safe_rate_pct=fault_policy_safe,
        fault_minus_control_policy_safe_rate_pp=round(
            fault_policy_safe - control_policy_safe, 2
        ),
        fault_injection_rate_pct=_percentage(
            pair.faulted.fault_injected for pair in group
        ),
        fault_detection_rate_pct=_percentage(
            pair.faulted.fault_detected for pair in group
        ),
        fault_repair_rate_pct=_percentage(
            pair.faulted.fault_repaired for pair in group
        ),
        control_mean_cost_units=control_cost,
        fault_mean_cost_units=fault_cost,
        fault_minus_control_mean_cost_units=round(fault_cost - control_cost, 2),
        control_mean_tool_calls=control_calls,
        fault_mean_tool_calls=fault_calls,
        fault_minus_control_mean_tool_calls=round(fault_calls - control_calls, 2),
    )
    if not all(
        isinstance(pair.control, WorkerFaultArmMeasurement)
        and isinstance(pair.faulted, WorkerFaultArmMeasurement)
        for pair in group
    ):
        return FaultStrategyAggregate(**base_values)

    worker_controls = tuple(
        pair.control
        for pair in group
        if isinstance(pair.control, WorkerFaultArmMeasurement)
    )
    worker_faults = tuple(
        pair.faulted
        for pair in group
        if isinstance(pair.faulted, WorkerFaultArmMeasurement)
    )
    control_evidence = _mean(
        arm.required_evidence_tag_coverage_pct for arm in worker_controls
    )
    fault_evidence = _mean(
        arm.required_evidence_tag_coverage_pct for arm in worker_faults
    )
    missing_tags = tuple(sorted({
        tag
        for arm in worker_faults
        for tag in arm.missing_required_evidence_tags
    }))
    return WorkerFaultStrategyAggregate(
        **base_values,
        fault_target_terminal_failure_rate_pct=_percentage(
            arm.target_terminal_failure for arm in worker_faults
        ),
        fault_reassignment_rate_pct=_percentage(
            arm.reassignment_attempted for arm in worker_faults
        ),
        fault_fallback_success_rate_pct=_percentage(
            arm.fallback_succeeded for arm in worker_faults
        ),
        fault_containment_rate_pct=_percentage(
            arm.fault_contained for arm in worker_faults
        ),
        fault_safe_degradation_rate_pct=_percentage(
            arm.safe_degraded for arm in worker_faults
        ),
        control_mean_required_evidence_tag_coverage_pct=control_evidence,
        fault_mean_required_evidence_tag_coverage_pct=fault_evidence,
        fault_minus_control_required_evidence_tag_coverage_pp=round(
            fault_evidence - control_evidence,
            2,
        ),
        fault_missing_required_evidence_tags=missing_tags,
    )


def _critic_effect(
    pairs: tuple[FaultPairMeasurement, ...],
) -> CriticEffectAggregate:
    without_name = "specialists_no_critic"
    with_name = "specialists_with_critic"
    without = tuple(pair for pair in pairs if pair.strategy == without_name)
    with_by_case = {
        (pair.seed, pair.variant): pair
        for pair in pairs
        if pair.strategy == with_name
    }
    matched = tuple(
        (pair, with_by_case[(pair.seed, pair.variant)]) for pair in without
    )

    control_without_rate = _percentage(
        left.control.recovered for left, _ in matched
    )
    control_with_rate = _percentage(
        right.control.recovered for _, right in matched
    )
    fault_without_rate = _percentage(
        left.faulted.recovered for left, _ in matched
    )
    fault_with_rate = _percentage(
        right.faulted.recovered for _, right in matched
    )
    control_lift = round(control_with_rate - control_without_rate, 2)
    fault_lift = round(fault_with_rate - fault_without_rate, 2)
    rescues = sum(
        not left.faulted.recovered and right.faulted.recovered
        for left, right in matched
    )
    regressions = sum(
        left.faulted.recovered and not right.faulted.recovered
        for left, right in matched
    )
    common_deltas = tuple(
        right.faulted.confirmed_recovery_ms - left.faulted.confirmed_recovery_ms
        for left, right in matched
        if left.faulted.confirmed_recovery_ms is not None
        and right.faulted.confirmed_recovery_ms is not None
    )
    without_action_recall = _mean(
        left.faulted.required_action_recall_pct for left, _ in matched
    )
    with_action_recall = _mean(
        right.faulted.required_action_recall_pct for _, right in matched
    )
    without_policy_safe = _percentage(
        left.faulted.policy_safe for left, _ in matched
    )
    with_policy_safe = _percentage(
        right.faulted.policy_safe for _, right in matched
    )
    without_cost = _mean(left.faulted.cost_units for left, _ in matched)
    with_cost = _mean(right.faulted.cost_units for _, right in matched)
    without_calls = _mean(left.faulted.tool_calls for left, _ in matched)
    with_calls = _mean(right.faulted.tool_calls for _, right in matched)
    return CriticEffectAggregate(
        without_critic_strategy=without_name,
        with_critic_strategy=with_name,
        cases=len(matched),
        control_without_critic_recovery_rate_pct=control_without_rate,
        control_with_critic_recovery_rate_pct=control_with_rate,
        control_with_minus_without_recovery_rate_pp=control_lift,
        fault_without_critic_recovery_rate_pct=fault_without_rate,
        fault_with_critic_recovery_rate_pct=fault_with_rate,
        fault_with_minus_without_recovery_rate_pp=fault_lift,
        recovery_difference_in_differences_pp=round(
            fault_lift - control_lift, 2
        ),
        fault_rescues=rescues,
        fault_regressions=regressions,
        fault_net_rescues=rescues - regressions,
        fault_both_recovered=sum(
            left.faulted.recovered and right.faulted.recovered
            for left, right in matched
        ),
        fault_neither_recovered=sum(
            not left.faulted.recovered and not right.faulted.recovered
            for left, right in matched
        ),
        fault_without_critic_mean_required_action_recall_pct=(
            without_action_recall
        ),
        fault_with_critic_mean_required_action_recall_pct=with_action_recall,
        fault_with_minus_without_required_action_recall_pp=round(
            with_action_recall - without_action_recall, 2
        ),
        fault_without_critic_policy_safe_rate_pct=without_policy_safe,
        fault_with_critic_policy_safe_rate_pct=with_policy_safe,
        fault_with_minus_without_policy_safe_rate_pp=round(
            with_policy_safe - without_policy_safe, 2
        ),
        fault_without_critic_mean_cost_units=without_cost,
        fault_with_critic_mean_cost_units=with_cost,
        fault_with_minus_without_mean_cost_units=round(with_cost - without_cost, 2),
        fault_without_critic_mean_tool_calls=without_calls,
        fault_with_critic_mean_tool_calls=with_calls,
        fault_with_minus_without_mean_tool_calls=round(
            with_calls - without_calls, 2
        ),
        fault_common_recovery_pairs=len(common_deltas),
        mean_fault_with_minus_without_confirmed_recovery_ms=(
            round(fmean(common_deltas), 2) if common_deltas else None
        ),
    )


def _worker_arm(arm: FaultArmMeasurement) -> WorkerFaultArmMeasurement:
    if not isinstance(arm, WorkerFaultArmMeasurement):
        raise TypeError("worker fault effect requires worker arm measurements")
    return arm


def _fallback_effect(
    pairs: tuple[FaultPairMeasurement, ...],
) -> FallbackEffectAggregate:
    """Isolate fallback reassignment with otherwise-identical specialist presets."""

    without_name = "specialists_with_critic"
    with_name = "specialists_with_fallback"
    without = tuple(pair for pair in pairs if pair.strategy == without_name)
    with_by_case = {
        (pair.seed, pair.variant): pair
        for pair in pairs
        if pair.strategy == with_name
    }
    matched = tuple(
        (pair, with_by_case[(pair.seed, pair.variant)]) for pair in without
    )

    control_without_rate = _percentage(
        left.control.recovered for left, _ in matched
    )
    control_with_rate = _percentage(
        right.control.recovered for _, right in matched
    )
    fault_without_rate = _percentage(
        left.faulted.recovered for left, _ in matched
    )
    fault_with_rate = _percentage(
        right.faulted.recovered for _, right in matched
    )
    control_lift = round(control_with_rate - control_without_rate, 2)
    fault_lift = round(fault_with_rate - fault_without_rate, 2)
    without_fault_arms = tuple(_worker_arm(left.faulted) for left, _ in matched)
    with_fault_arms = tuple(_worker_arm(right.faulted) for _, right in matched)
    without_containment = _percentage(
        arm.fault_contained for arm in without_fault_arms
    )
    with_containment = _percentage(
        arm.fault_contained for arm in with_fault_arms
    )
    without_safe_degradation = _percentage(
        arm.safe_degraded for arm in without_fault_arms
    )
    with_safe_degradation = _percentage(
        arm.safe_degraded for arm in with_fault_arms
    )
    without_evidence = _mean(
        arm.required_evidence_tag_coverage_pct for arm in without_fault_arms
    )
    with_evidence = _mean(
        arm.required_evidence_tag_coverage_pct for arm in with_fault_arms
    )
    without_policy_safe = _percentage(
        left.faulted.policy_safe for left, _ in matched
    )
    with_policy_safe = _percentage(
        right.faulted.policy_safe for _, right in matched
    )
    without_cost = _mean(left.faulted.cost_units for left, _ in matched)
    with_cost = _mean(right.faulted.cost_units for _, right in matched)
    without_calls = _mean(left.faulted.tool_calls for left, _ in matched)
    with_calls = _mean(right.faulted.tool_calls for _, right in matched)
    common_deltas = tuple(
        right.faulted.confirmed_recovery_ms - left.faulted.confirmed_recovery_ms
        for left, right in matched
        if left.faulted.confirmed_recovery_ms is not None
        and right.faulted.confirmed_recovery_ms is not None
    )
    rescues = sum(
        not left.faulted.recovered and right.faulted.recovered
        for left, right in matched
    )
    regressions = sum(
        left.faulted.recovered and not right.faulted.recovered
        for left, right in matched
    )
    return FallbackEffectAggregate(
        without_fallback_strategy=without_name,
        with_fallback_strategy=with_name,
        cases=len(matched),
        control_without_fallback_recovery_rate_pct=control_without_rate,
        control_with_fallback_recovery_rate_pct=control_with_rate,
        control_with_minus_without_recovery_rate_pp=control_lift,
        fault_without_fallback_recovery_rate_pct=fault_without_rate,
        fault_with_fallback_recovery_rate_pct=fault_with_rate,
        fault_with_minus_without_recovery_rate_pp=fault_lift,
        recovery_difference_in_differences_pp=round(
            fault_lift - control_lift,
            2,
        ),
        fault_rescues=rescues,
        fault_regressions=regressions,
        fault_net_rescues=rescues - regressions,
        fault_both_recovered=sum(
            left.faulted.recovered and right.faulted.recovered
            for left, right in matched
        ),
        fault_neither_recovered=sum(
            not left.faulted.recovered and not right.faulted.recovered
            for left, right in matched
        ),
        fault_without_fallback_containment_rate_pct=without_containment,
        fault_with_fallback_containment_rate_pct=with_containment,
        fault_with_minus_without_containment_rate_pp=round(
            with_containment - without_containment,
            2,
        ),
        fault_without_fallback_safe_degradation_rate_pct=(
            without_safe_degradation
        ),
        fault_with_fallback_safe_degradation_rate_pct=with_safe_degradation,
        fault_with_minus_without_safe_degradation_rate_pp=round(
            with_safe_degradation - without_safe_degradation,
            2,
        ),
        fault_without_fallback_mean_required_evidence_tag_coverage_pct=(
            without_evidence
        ),
        fault_with_fallback_mean_required_evidence_tag_coverage_pct=with_evidence,
        fault_with_minus_without_required_evidence_tag_coverage_pp=round(
            with_evidence - without_evidence,
            2,
        ),
        fault_without_fallback_policy_safe_rate_pct=without_policy_safe,
        fault_with_fallback_policy_safe_rate_pct=with_policy_safe,
        fault_with_minus_without_policy_safe_rate_pp=round(
            with_policy_safe - without_policy_safe,
            2,
        ),
        fault_without_fallback_mean_cost_units=without_cost,
        fault_with_fallback_mean_cost_units=with_cost,
        fault_with_minus_without_mean_cost_units=round(with_cost - without_cost, 2),
        fault_without_fallback_mean_tool_calls=without_calls,
        fault_with_fallback_mean_tool_calls=with_calls,
        fault_with_minus_without_mean_tool_calls=round(
            with_calls - without_calls,
            2,
        ),
        fault_common_recovery_pairs=len(common_deltas),
        mean_fault_with_minus_without_confirmed_recovery_ms=(
            round(fmean(common_deltas), 2) if common_deltas else None
        ),
    )


def _budget_fallback_effect(
    pairs: tuple[BudgetFaultPairMeasurement, ...],
) -> BudgetFallbackEffectAggregate:
    """Isolate how the budget intervention changes fallback's causal benefit."""

    without_name, with_name = BUDGET_FAULT_PRESET_NAMES
    without = tuple(pair for pair in pairs if pair.strategy == without_name)
    with_by_case = {
        (pair.seed, pair.variant): pair
        for pair in pairs
        if pair.strategy == with_name
    }
    matched = tuple(
        (pair, with_by_case[(pair.seed, pair.variant)]) for pair in without
    )
    exact_without = _percentage(
        left.control.recovered for left, _ in matched
    )
    exact_with = _percentage(
        right.control.recovered for _, right in matched
    )
    tight_without = _percentage(
        left.faulted.recovered for left, _ in matched
    )
    tight_with = _percentage(
        right.faulted.recovered for _, right in matched
    )
    exact_benefit = round(exact_with - exact_without, 2)
    tight_benefit = round(tight_with - tight_without, 2)
    exact_rescues = sum(
        not left.control.recovered and right.control.recovered
        for left, right in matched
    )
    tight_rescues = sum(
        not left.faulted.recovered and right.faulted.recovered
        for left, right in matched
    )
    exact_regressions = sum(
        left.control.recovered and not right.control.recovered
        for left, right in matched
    )
    tight_regressions = sum(
        left.faulted.recovered and not right.faulted.recovered
        for left, right in matched
    )

    def resource_delta(
        strategy_side: int,
        field: str,
    ) -> float:
        return _mean(
            getattr((left, right)[strategy_side].faulted, field)
            - getattr((left, right)[strategy_side].control, field)
            for left, right in matched
        )

    return BudgetFallbackEffectAggregate(
        without_fallback_strategy=without_name,
        with_fallback_strategy=with_name,
        cases=len(matched),
        exact_fit_without_fallback_recovery_rate_pct=exact_without,
        exact_fit_with_fallback_recovery_rate_pct=exact_with,
        exact_fit_fallback_benefit_pp=exact_benefit,
        tight_budget_without_fallback_recovery_rate_pct=tight_without,
        tight_budget_with_fallback_recovery_rate_pct=tight_with,
        tight_budget_fallback_benefit_pp=tight_benefit,
        fallback_benefit_difference_in_differences_pp=round(
            tight_benefit - exact_benefit,
            2,
        ),
        exact_fit_rescues=exact_rescues,
        tight_budget_rescues=tight_rescues,
        lost_rescues=exact_rescues - tight_rescues,
        exact_fit_regressions=exact_regressions,
        tight_budget_regressions=tight_regressions,
        exact_fit_with_fallback_success_rate_pct=_percentage(
            right.control.fallback_succeeded for _, right in matched
        ),
        tight_budget_with_fallback_success_rate_pct=_percentage(
            right.faulted.fallback_succeeded for _, right in matched
        ),
        exact_fit_with_fallback_containment_rate_pct=_percentage(
            right.control.fault_contained for _, right in matched
        ),
        tight_budget_with_fallback_containment_rate_pct=_percentage(
            right.faulted.fault_contained for _, right in matched
        ),
        exact_fit_with_fallback_safe_degradation_rate_pct=_percentage(
            right.control.safe_degraded for _, right in matched
        ),
        tight_budget_with_fallback_safe_degradation_rate_pct=_percentage(
            right.faulted.safe_degraded for _, right in matched
        ),
        exact_fit_with_fallback_budget_safe_rate_pct=_percentage(
            right.control.budget_safe for _, right in matched
        ),
        tight_budget_with_fallback_budget_safe_rate_pct=_percentage(
            right.faulted.budget_safe for _, right in matched
        ),
        exact_fit_with_fallback_grant_rate_pct=_percentage(
            right.control.reservation_grant_count == 1
            for _, right in matched
        ),
        tight_budget_with_fallback_denial_rate_pct=_percentage(
            right.faulted.reservation_denial_count == 1
            for _, right in matched
        ),
        tight_budget_with_fallback_skip_rate_pct=_percentage(
            right.faulted.fallback_skip_count == 1
            for _, right in matched
        ),
        tight_budget_with_fallback_zero_spend_rate_pct=_percentage(
            right.faulted.reservation_denial_count == 1
            and right.faulted.denied_zero_spend
            for _, right in matched
        ),
        tight_budget_with_fallback_no_dispatch_rate_pct=_percentage(
            right.faulted.reservation_denial_count == 1
            and right.faulted.denied_no_dispatch
            for _, right in matched
        ),
        tight_minus_exact_without_fallback_mean_attempts_used=resource_delta(
            0, "attempts_used"
        ),
        tight_minus_exact_without_fallback_mean_cost_units=resource_delta(
            0, "cost_units"
        ),
        tight_minus_exact_without_fallback_mean_tool_calls=resource_delta(
            0, "tool_calls"
        ),
        tight_minus_exact_without_fallback_mean_total_ms=resource_delta(
            0, "total_ms"
        ),
        tight_minus_exact_with_fallback_mean_attempts_used=resource_delta(
            1, "attempts_used"
        ),
        tight_minus_exact_with_fallback_mean_cost_units=resource_delta(
            1, "cost_units"
        ),
        tight_minus_exact_with_fallback_mean_tool_calls=resource_delta(
            1, "tool_calls"
        ),
        tight_minus_exact_with_fallback_mean_total_ms=resource_delta(
            1, "total_ms"
        ),
    )


def run_fault_experiment(
    *,
    start_seed: int = 0,
    count: int = 30,
    variant_name: str | None = None,
    fault_name: str = PLANNING_OMISSION.name,
) -> FaultExperimentReport | WorkerFaultExperimentReport | BudgetFaultExperimentReport:
    """Run paired matched-control/fault arms for every preset and seeded case."""

    if not isinstance(start_seed, int):
        raise TypeError("start_seed must be an integer")
    if not isinstance(count, int):
        raise TypeError("count must be an integer")
    if count < 1:
        raise ValueError("count must be at least 1")
    if not isinstance(fault_name, str):
        raise TypeError("fault_name must be a string")
    if fault_name not in INJECTABLE_FAULT_NAMES:
        choices = ", ".join(INJECTABLE_FAULT_NAMES)
        raise ValueError(
            f"fault_name must be an injectable fault; choose one of: {choices}"
        )

    if fault_name == FALLBACK_BUDGET_EXHAUSTION.name:
        budget_pairs = tuple(
            _measure_budget_fault_pair(seed, variant_name, strategy)
            for seed in range(start_seed, start_seed + count)
            for strategy in BUDGET_FAULT_PRESET_NAMES
        )
        budget_variants = sorted({pair.variant for pair in budget_pairs})
        return BudgetFaultExperimentReport(
            start_seed=start_seed,
            count=count,
            variant_filter=variant_name,
            fault_name=fault_name,
            preset_order=BUDGET_FAULT_PRESET_NAMES,
            condition_order=("exact_fit_control", fault_name),
            pairs=budget_pairs,
            aggregates=tuple(
                _aggregate_budget_fault_strategy(strategy, budget_pairs)
                for strategy in BUDGET_FAULT_PRESET_NAMES
            ),
            variant_counts=tuple(
                (
                    variant,
                    len({
                        pair.seed
                        for pair in budget_pairs
                        if pair.variant == variant
                    }),
                )
                for variant in budget_variants
            ),
            fallback_effect=_budget_fallback_effect(budget_pairs),
        )

    presets = (
        WORKER_FAILURE_PRESETS
        if fault_name == PERMANENT_WORKER_FAILURE.name
        else ABLATION_PRESETS
    )
    preset_order = tuple(preset.name for preset in presets)
    pairs = tuple(
        _measure_fault_pair(seed, variant_name, strategy, fault_name)
        for seed in range(start_seed, start_seed + count)
        for strategy in preset_order
    )
    variants = sorted({pair.variant for pair in pairs})
    variant_counts = tuple(
        (
            variant,
            len({pair.seed for pair in pairs if pair.variant == variant}),
        )
        for variant in variants
    )
    common_values = dict(
        start_seed=start_seed,
        count=count,
        variant_filter=variant_name,
        fault_name=fault_name,
        preset_order=preset_order,
        condition_order=("matched_control", fault_name),
        pairs=pairs,
        variant_counts=variant_counts,
    )
    if fault_name == PERMANENT_WORKER_FAILURE.name:
        worker_aggregates = tuple(
            aggregate
            for strategy in preset_order
            for aggregate in (_aggregate_fault_strategy(strategy, pairs),)
            if isinstance(aggregate, WorkerFaultStrategyAggregate)
        )
        if len(worker_aggregates) != len(preset_order):
            raise RuntimeError("worker fault matrix produced non-worker aggregates")
        return WorkerFaultExperimentReport(
            **common_values,
            aggregates=worker_aggregates,
            fallback_effect=_fallback_effect(pairs),
        )
    return FaultExperimentReport(
        **common_values,
        aggregates=tuple(
            _aggregate_fault_strategy(strategy, pairs)
            for strategy in preset_order
        ),
        critic_effect=_critic_effect(pairs),
    )


def run_budget_fault_experiment(
    *,
    start_seed: int = 0,
    count: int = 30,
    variant_name: str | None = None,
) -> BudgetFaultExperimentReport:
    """Run the exact-fit versus tight fallback-budget teaching study."""

    report = run_fault_experiment(
        start_seed=start_seed,
        count=count,
        variant_name=variant_name,
        fault_name=FALLBACK_BUDGET_EXHAUSTION.name,
    )
    if not isinstance(report, BudgetFaultExperimentReport):
        raise RuntimeError("fallback budget experiment returned the wrong report type")
    return report
