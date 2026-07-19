"""AuroraTickets agent-orchestration teaching lab."""

from .experiments import (
    BUDGET_FAULT_PRESET_NAMES,
    run_ablation_experiment,
    run_budget_fault_experiment,
    run_experiment,
    run_fault_experiment,
)
from .faults import (
    FALLBACK_BUDGET_EXHAUSTION,
    FAULT_NAMES,
    PERMANENT_WORKER_FAILURE,
    PLANNING_OMISSION,
    FaultSpec,
    fault_for,
)
from .metrics import BudgetReservationFact, RunMetrics, derive_run_metrics
from .model_backend import (
    AgentBackend,
    BackendRequest,
    BackendResponse,
    ModelBackedSynthesizer,
    ModelTelemetry,
    OpenAIResponsesBackend,
    RecordingBackend,
    ReplayBackend,
)
from .scenario import VARIANT_NAMES, build_scenario
from .showcase import (
    DEFAULT_SHOWCASE_OUTPUT,
    SHOWCASE_SCHEMA_VERSION,
    ShowcaseBundle,
    write_showcase_bundle,
)
from .simulation import (
    ABLATION_PRESETS,
    WORKER_FAILURE_PRESETS,
    ExperimentConfig,
    run_comparison,
    run_orchestrated,
    run_preset,
    run_single_agent,
)

__all__ = [
    "ABLATION_PRESETS",
    "BUDGET_FAULT_PRESET_NAMES",
    "BudgetReservationFact",
    "BackendRequest",
    "BackendResponse",
    "DEFAULT_SHOWCASE_OUTPUT",
    "ExperimentConfig",
    "FAULT_NAMES",
    "FALLBACK_BUDGET_EXHAUSTION",
    "FaultSpec",
    "AgentBackend",
    "ModelBackedSynthesizer",
    "ModelTelemetry",
    "OpenAIResponsesBackend",
    "PERMANENT_WORKER_FAILURE",
    "PLANNING_OMISSION",
    "RunMetrics",
    "RecordingBackend",
    "ReplayBackend",
    "SHOWCASE_SCHEMA_VERSION",
    "ShowcaseBundle",
    "VARIANT_NAMES",
    "WORKER_FAILURE_PRESETS",
    "build_scenario",
    "derive_run_metrics",
    "fault_for",
    "run_ablation_experiment",
    "run_budget_fault_experiment",
    "run_comparison",
    "run_experiment",
    "run_fault_experiment",
    "run_orchestrated",
    "run_preset",
    "run_single_agent",
    "write_showcase_bundle",
]

__version__ = "0.1.0"
