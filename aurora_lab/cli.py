"""Command-line interface for the Aurora orchestration lab."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Sequence

from .experiments import (
    run_ablation_experiment,
    run_experiment,
    run_fault_experiment,
)
from .faults import FAULT_NAMES, INJECTABLE_FAULT_NAMES, NO_FAULT, PLANNING_OMISSION
from .model_backend import (
    ModelBackedSynthesizer,
    OpenAIResponsesBackend,
    RecordingBackend,
    ReplayBackend,
)
from .reporting import (
    format_ablation,
    format_comparison,
    format_experiment,
    format_fault_experiment,
    format_result,
    to_primitive,
    write_result_json,
)
from .scenario import CANONICAL_VARIANT_BY_SEED, VARIANT_NAMES, build_scenario
from .showcase import DEFAULT_SHOWCASE_OUTPUT, write_showcase_bundle
from .simulation import (
    ABLATION_PRESETS,
    WORKER_FAILURE_PRESETS,
    OrchestratedStrategy,
    SingleAgentStrategy,
    run_comparison,
    strategy_for,
)


def _add_case_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--seed",
        type=int,
        default=101,
        help="deterministic case seed (default: 101)",
    )
    parser.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help="pin a case variant instead of selecting it from the seed",
    )
    parser.add_argument(
        "--fault",
        choices=FAULT_NAMES,
        default="none",
        help="apply a named deterministic fault intervention (default: none)",
    )
    parser.add_argument(
        "--fault-control",
        action="store_true",
        help=(
            "run the matched identity-control arm for --fault; "
            "requires an injectable fault"
        ),
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value cannot be negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be finite and positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora-lab",
        description="Deterministic teaching simulator for agent orchestration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare",
        help="compare orchestrated and sequential single-agent strategies",
    )
    _add_case_arguments(compare)
    compare.add_argument(
        "--trace",
        choices=("summary", "full", "none"),
        default="summary",
        help="trace detail to print (default: summary)",
    )
    compare.add_argument(
        "--json",
        metavar="PATH",
        help="write both structured run artifacts to one JSON file",
    )

    run = subparsers.add_parser("run", help="run one strategy")
    _add_case_arguments(run)
    run.add_argument(
        "--strategy",
        choices=(
            "orchestrated",
            "single-agent",
            *(preset.name for preset in WORKER_FAILURE_PRESETS),
        ),
        default="orchestrated",
    )
    run.add_argument(
        "--trace",
        choices=("summary", "full", "none"),
        default="summary",
    )
    run.add_argument("--json", metavar="PATH", help="write the structured result")
    run.add_argument(
        "--agent-backend",
        choices=("deterministic", "openai", "replay"),
        default="deterministic",
        help=(
            "comprehensive-diagnosis backend; every other stage stays "
            "deterministic (default: deterministic)"
        ),
    )
    run.add_argument(
        "--model",
        help=(
            "explicit model ID (required for openai; optional replay assertion); "
            "there is no model default"
        ),
    )
    run.add_argument(
        "--model-record",
        metavar="PATH",
        help="required live recording path for openai, or input record for replay",
    )
    run.add_argument(
        "--model-timeout-seconds",
        "--model-timeout",
        dest="model_timeout_seconds",
        type=_positive_float,
        default=30.0,
        help="OpenAI wall-clock timeout in seconds (default: 30)",
    )
    run.add_argument(
        "--model-max-output-tokens",
        "--model-output-tokens",
        dest="model_max_output_tokens",
        type=_positive_int,
        default=600,
        help="OpenAI output-token ceiling (default: 600)",
    )
    run.add_argument(
        "--model-transport-retries",
        "--model-retries",
        dest="model_transport_retries",
        type=_nonnegative_int,
        choices=(0, 1),
        default=0,
        help=(
            "explicit app-level transport retries: zero or one; "
            "SDK retries stay disabled"
        ),
    )
    run.add_argument(
        "--model-semantic-retries",
        type=_nonnegative_int,
        choices=(0, 1),
        default=0,
        help=(
            "zero or one retry after invalid or incomplete structured output "
            "(default: 0)"
        ),
    )

    subparsers.add_parser("variants", help="list the three teaching cases")

    matrix = subparsers.add_parser(
        "matrix",
        help="compare strategies over a deterministic range of seeds",
    )
    matrix.add_argument("--start-seed", type=int, default=0)
    matrix.add_argument("--count", type=_positive_int, default=30)
    matrix.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help="pin every run to one variant",
    )
    matrix.add_argument(
        "--show-runs",
        action="store_true",
        help="include every paired seed below the aggregates",
    )
    matrix.add_argument("--json", metavar="PATH", help="write the experiment report")

    ablation = subparsers.add_parser(
        "ablation",
        help="compare five orchestration feature presets over a seed range",
    )
    ablation.add_argument("--start-seed", type=int, default=0)
    ablation.add_argument("--count", type=_positive_int, default=30)
    ablation.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help="pin every run to one variant",
    )
    ablation.add_argument(
        "--show-runs",
        action="store_true",
        help="include every seed/preset run below the aggregates",
    )
    ablation.add_argument("--json", metavar="PATH", help="write the ablation report")

    fault_matrix = subparsers.add_parser(
        "fault-matrix",
        help=(
            "compare paired matched-control and faulted runs across the fault's presets"
        ),
    )
    fault_matrix.add_argument("--start-seed", type=int, default=0)
    fault_matrix.add_argument("--count", type=_positive_int, default=30)
    fault_matrix.add_argument(
        "--variant",
        choices=VARIANT_NAMES,
        help="pin every paired case to one variant",
    )
    fault_matrix.add_argument(
        "--fault",
        choices=INJECTABLE_FAULT_NAMES,
        default=PLANNING_OMISSION.name,
        help="fault intervention paired against its matched identity control",
    )
    fault_matrix.add_argument(
        "--show-runs",
        action="store_true",
        help="include every seed/preset control-fault pair",
    )
    fault_matrix.add_argument(
        "--json",
        metavar="PATH",
        help="write the paired fault experiment report",
    )

    showcase = subparsers.add_parser(
        "showcase",
        help="generate the static Aurora Run Explorer data bundle",
    )
    showcase.add_argument(
        "--output",
        metavar="PATH",
        default=str(DEFAULT_SHOWCASE_OUTPUT),
        help=(
            "bundle directory "
            f"(default: {DEFAULT_SHOWCASE_OUTPUT.as_posix()})"
        ),
    )
    return parser


def _write_comparison_json(path: str, orchestrated: object, baseline: object) -> Path:
    destination = Path(path)
    payload = {
        "orchestrated": to_primitive(orchestrated),
        "single_agent": to_primitive(baseline),
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination.resolve()


def _write_experiment_json(path: str, report: object) -> Path:
    destination = Path(path)
    destination.write_text(
        json.dumps(to_primitive(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination.resolve()


def _model_synthesizer_for_run(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    independent_critic: bool,
) -> ModelBackedSynthesizer | None:
    """Validate model-mode gates and construct an offline-safe adapter."""

    if args.agent_backend == "deterministic":
        if args.model is not None or args.model_record is not None:
            parser.error(
                "--model and --model-record require --agent-backend openai or replay"
            )
        return None
    if args.fault != NO_FAULT.name or args.fault_control:
        parser.error("model backends are disabled for fault and matched-control runs")
    if not independent_critic:
        parser.error("model backends require a strategy with an independent critic")
    if not args.model_record:
        parser.error(
            "--model-record is required (live calls must be recorded; replay needs input)"
        )

    if args.agent_backend == "openai":
        if not args.model:
            parser.error("--model is required with --agent-backend openai")
        backend = RecordingBackend(
            OpenAIResponsesBackend(
                model=args.model,
                timeout_seconds=args.model_timeout_seconds,
                max_output_tokens=args.model_max_output_tokens,
                max_transport_retries=args.model_transport_retries,
            ),
            args.model_record,
        )
    else:
        if args.model_transport_retries:
            parser.error("transport retries apply only to --agent-backend openai")
        backend = ReplayBackend(
            args.model_record,
            expected_model=args.model,
        )
    return ModelBackedSynthesizer(
        backend,
        max_semantic_retries=args.model_semantic_retries,
    )


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)

    if (
        args.command in {"run", "compare"}
        and args.fault_control
        and args.fault == NO_FAULT.name
    ):
        parser.error("--fault-control requires an injectable --fault")

    if args.command == "variants":
        seed_for_variant = {
            variant: seed for seed, variant in CANONICAL_VARIANT_BY_SEED.items()
        }
        for variant in VARIANT_NAMES:
            scenario = build_scenario(seed_for_variant[variant], variant)
            print(f"{variant:<32} seed {scenario.seed:<3}  {scenario.variant.summary}")
        return 0

    if args.command == "showcase":
        bundle = write_showcase_bundle(args.output)
        print(
            "Showcase bundle: "
            f"{bundle.manifest_path} ({len(bundle.artifact_paths)} artifacts)"
        )
        return 0

    if args.command == "matrix":
        report = run_experiment(
            start_seed=args.start_seed,
            count=args.count,
            variant_name=args.variant,
        )
        print(format_experiment(report, show_runs=args.show_runs))
        if args.json:
            destination = _write_experiment_json(args.json, report)
            print(f"\nStructured experiment: {destination}")
        return 0

    if args.command == "ablation":
        report = run_ablation_experiment(
            start_seed=args.start_seed,
            count=args.count,
            variant_name=args.variant,
        )
        print(format_ablation(report, show_runs=args.show_runs))
        if args.json:
            destination = _write_experiment_json(args.json, report)
            print(f"\nStructured ablation: {destination}")
        return 0

    if args.command == "fault-matrix":
        report = run_fault_experiment(
            start_seed=args.start_seed,
            count=args.count,
            variant_name=args.variant,
            fault_name=args.fault,
        )
        print(format_fault_experiment(report, show_runs=args.show_runs))
        if args.json:
            destination = _write_experiment_json(args.json, report)
            print(f"\nStructured fault experiment: {destination}")
        return 0

    if args.command == "compare":
        control_for_fault = args.fault if args.fault_control else None
        orchestrated, baseline = run_comparison(
            args.seed,
            args.variant,
            NO_FAULT.name if control_for_fault else args.fault,
            control_for_fault=control_for_fault,
        )
        print(
            format_comparison(
                orchestrated,
                baseline,
                trace_mode=args.trace,
            )
        )
        if args.json:
            destination = _write_comparison_json(args.json, orchestrated, baseline)
            print(f"\nStructured comparison: {destination}")
        return 0

    control_for_fault = args.fault if args.fault_control else None
    scenario = build_scenario(
        args.seed,
        args.variant,
        NO_FAULT.name if control_for_fault else args.fault,
        control_for_fault=control_for_fault,
    )
    if args.strategy == "orchestrated":
        model_synthesizer = _model_synthesizer_for_run(
            parser,
            args,
            independent_critic=True,
        )
        strategy = OrchestratedStrategy(
            comprehensive_synthesizer=model_synthesizer
        )
    elif args.strategy == "single-agent":
        model_synthesizer = _model_synthesizer_for_run(
            parser,
            args,
            independent_critic=False,
        )
        assert model_synthesizer is None
        strategy = SingleAgentStrategy()
    else:
        deterministic_strategy = strategy_for(args.strategy)
        model_synthesizer = _model_synthesizer_for_run(
            parser,
            args,
            independent_critic=deterministic_strategy.config.independent_critic,
        )
        strategy = strategy_for(
            args.strategy,
            comprehensive_synthesizer=model_synthesizer,
        )
    result = strategy.run(scenario)
    print(format_result(result, include_trace=args.trace))
    if args.json:
        destination = write_result_json(args.json, result)
        print(f"\nStructured result: {destination}")
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
