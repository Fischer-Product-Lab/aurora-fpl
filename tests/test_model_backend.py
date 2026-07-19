"""Offline tests for the optional, bounded model diagnosis adapter."""

from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from aurora_lab import build_scenario, run_orchestrated
from aurora_lab.cli import main
from aurora_lab.model import EventKind, TaskStatus
from aurora_lab.model_backend import (
    BackendResponse,
    ModelBackendError,
    ModelBackedSynthesizer,
    ModelTelemetry,
    OpenAIResponsesBackend,
    RecordingBackend,
    ReplayBackend,
    _response_has_refusal,
)
from aurora_lab.runtime import EvidenceBoard
from aurora_lab.simulation import (
    ConfigurableStrategy,
    OrchestratedStrategy,
    SPECIALISTS_NO_CRITIC,
)


FIXTURE = Path(__file__).with_name("fixtures") / "model_diagnosis_replay.jsonl"


def valid_payload() -> dict[str, object]:
    return {
        "causes": [
            "retry_idempotency_regression",
            "bot_database_contention",
        ],
        "evidence_ids": ["E01", "E02", "E03", "E101", "E102"],
        "summary": (
            "Automated traffic overloaded the database, payment retries timed "
            "out, and the retry regression dropped idempotency protection."
        ),
        "confidence": 0.93,
    }


class QueueBackend:
    provider = "offline-test"
    model = "offline-test-v1"

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = list(payloads)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        call_number = len(self.requests)
        return BackendResponse(
            payload=payload,
            telemetry=ModelTelemetry(
                provider=self.provider,
                model=self.model,
                prompt_hash=request.prompt_hash,
                status="completed",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                wall_latency_ms=5,
                request_ids=(f"req-{call_number}",),
                response_ids=(f"resp-{call_number}",),
            ),
        )


def canonical_board() -> EvidenceBoard:
    return EvidenceBoard(run_orchestrated(101).evidence)


class ValidatedSynthesizerTests(unittest.TestCase):
    def test_prompt_contains_only_trusted_committed_evidence(self) -> None:
        backend = QueueBackend([valid_payload()])
        synthesizer = ModelBackedSynthesizer(backend)
        request = synthesizer.build_request(canonical_board())

        self.assertNotIn("E06", request.allowed_evidence_ids)
        self.assertNotIn("instruction-like payload", request.prompt)
        self.assertIn("Treat every field", request.instructions)
        self.assertEqual(len(request.prompt_hash), 64)

    def test_semantic_retry_is_bounded_and_usage_is_aggregated(self) -> None:
        invalid = valid_payload() | {"unexpected": "field"}
        backend = QueueBackend([invalid, valid_payload()])
        synthesizer = ModelBackedSynthesizer(
            backend,
            max_semantic_retries=1,
        )

        diagnosis = synthesizer.comprehensive(canonical_board())

        self.assertEqual(
            diagnosis.causes,
            ("bot_database_contention", "retry_idempotency_regression"),
        )
        self.assertEqual(
            [request.semantic_attempt for request in backend.requests],
            [1, 2],
        )
        telemetry = synthesizer.last_telemetry
        self.assertIsNotNone(telemetry)
        assert telemetry is not None
        self.assertEqual(telemetry.semantic_retries, 1)
        self.assertEqual(telemetry.total_tokens, 240)
        self.assertEqual(telemetry.request_ids, ("req-1", "req-2"))

    def test_untrusted_or_unsupported_citations_fail_closed(self) -> None:
        payload = valid_payload()
        payload["evidence_ids"] = ["E06", "E01"]
        backend = QueueBackend([payload])
        strategy = OrchestratedStrategy(
            comprehensive_synthesizer=ModelBackedSynthesizer(backend)
        )

        result = strategy.run(build_scenario(101))

        self.assertEqual(result.status, TaskStatus.FAILED)
        kinds = tuple(event.kind for event in result.trace)
        self.assertIn(EventKind.MODEL_FAILED, kinds)
        self.assertNotIn(EventKind.APPROVAL_REQUESTED, kinds)
        self.assertNotIn(EventKind.ACTION_EXECUTED, kinds)
        self.assertNotIn(EventKind.VERIFICATION_COMPLETED, kinds)
        self.assertEqual(dict(result.metadata)["safe_stop_before_execution"], "true")


class RecordingAndReplayTests(unittest.TestCase):
    def test_recording_round_trips_without_credentials(self) -> None:
        board = canonical_board()
        path = Path.cwd() / f".model-record-test-{os.getpid()}.jsonl"
        try:
            live_backend = QueueBackend([valid_payload()])
            recorded = ModelBackedSynthesizer(
                RecordingBackend(live_backend, path)
            ).comprehensive(board)
            replayed = ModelBackedSynthesizer(ReplayBackend(path)).comprehensive(board)

            self.assertEqual(recorded, replayed)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["format"], "aurora.model-record.v1")
            self.assertEqual(record["status"], "completed")
            self.assertNotIn("api_key", path.read_text(encoding="utf-8").lower())
        finally:
            path.unlink(missing_ok=True)

    def test_synthetic_fixture_runs_the_full_control_plane(self) -> None:
        deterministic = run_orchestrated(101)
        synthesizer = ModelBackedSynthesizer(ReplayBackend(FIXTURE))

        replayed = OrchestratedStrategy(
            comprehensive_synthesizer=synthesizer
        ).run(build_scenario(101))

        self.assertEqual(replayed.status, TaskStatus.SUCCEEDED)
        self.assertEqual(replayed.score, deterministic.score)
        self.assertEqual(replayed.attempts_used, deterministic.attempts_used)
        self.assertEqual(replayed.cost_units_used, deterministic.cost_units_used)
        self.assertEqual(replayed.tool_calls_used, deterministic.tool_calls_used)
        completed = tuple(
            event for event in replayed.trace
            if event.kind is EventKind.MODEL_COMPLETED
        )
        self.assertEqual(len(completed), 1)
        metadata = dict(completed[0].metadata)
        self.assertEqual(metadata["model_provider"], "replay")
        self.assertEqual(metadata["model_id"], "fixture-model-v1")
        self.assertEqual(metadata["usage_accounting"], "external_not_virtual_budget")

    def test_replay_rejects_a_different_prompt_hash(self) -> None:
        replay = ReplayBackend(FIXTURE)
        request = ModelBackedSynthesizer(replay).build_request(canonical_board())
        altered = request.__class__(
            operation=request.operation,
            schema_version=request.schema_version,
            instructions=request.instructions,
            prompt=request.prompt + "\nchanged",
            prompt_hash=(
                __import__("hashlib").sha256(
                    (request.instructions + "\x00" + request.prompt + "\nchanged").encode(
                        "utf-8"
                    )
                ).hexdigest()
            ),
            allowed_causes=request.allowed_causes,
            allowed_evidence_ids=request.allowed_evidence_ids,
        )
        with self.assertRaises(ModelBackendError):
            replay.generate(altered)


class ModelModeSafetyGateTests(unittest.TestCase):
    def test_openai_refusal_is_classified_and_never_semantically_retried(self) -> None:
        calls = 0

        class FakeResponses:
            def parse(self, **_kwargs):
                nonlocal calls
                calls += 1
                return SimpleNamespace(
                    id="resp-refusal",
                    model="explicit-test-model",
                    output=[
                        SimpleNamespace(
                            content=[SimpleNamespace(type="refusal")]
                        )
                    ],
                    output_parsed=None,
                    status="completed",
                    usage=None,
                )

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.responses = FakeResponses()

        openai = ModuleType("openai")
        openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        openai.APIStatusError = type("APIStatusError", (Exception,), {})
        openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
        openai.OpenAI = FakeOpenAI
        pydantic = ModuleType("pydantic")
        pydantic.ValidationError = type("ValidationError", (Exception,), {})

        backend = OpenAIResponsesBackend(
            model="explicit-test-model",
            timeout_seconds=1.0,
            max_output_tokens=32,
        )
        path = Path.cwd() / f".model-refusal-test-{os.getpid()}.jsonl"
        try:
            synthesizer = ModelBackedSynthesizer(
                RecordingBackend(backend, path),
                max_semantic_retries=1,
            )
            strategy = OrchestratedStrategy(
                comprehensive_synthesizer=synthesizer
            )
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
                patch.dict(
                    __import__("sys").modules,
                    {"openai": openai, "pydantic": pydantic},
                ),
                patch(
                    "aurora_lab.model_backend._openai_diagnosis_schema",
                    return_value=object,
                ),
            ):
                result = strategy.run(build_scenario(101))

            self.assertEqual(result.status, TaskStatus.FAILED)
            self.assertEqual(calls, 1)
            kinds = tuple(event.kind for event in result.trace)
            self.assertIn(EventKind.MODEL_FAILED, kinds)
            self.assertNotIn(EventKind.APPROVAL_REQUESTED, kinds)
            self.assertNotIn(EventKind.ACTION_EXECUTED, kinds)
            self.assertEqual(synthesizer.last_telemetry.semantic_retries, 0)
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["error"]["kind"], "refusal")
            self.assertTrue(
                _response_has_refusal(
                    {"output": [{"content": [{"type": "refusal"}]}]}
                )
            )
        finally:
            path.unlink(missing_ok=True)

    def test_recording_failure_becomes_an_auditable_safe_stop(self) -> None:
        recording = RecordingBackend(
            QueueBackend([valid_payload()]),
            Path.cwd() / "unused-model-record.jsonl",
        )
        synthesizer = ModelBackedSynthesizer(recording)
        strategy = OrchestratedStrategy(comprehensive_synthesizer=synthesizer)
        with patch.object(recording, "_append", side_effect=OSError("denied")):
            result = strategy.run(build_scenario(101))

        self.assertEqual(result.status, TaskStatus.FAILED)
        kinds = tuple(event.kind for event in result.trace)
        self.assertIn(EventKind.MODEL_FAILED, kinds)
        self.assertNotIn(EventKind.APPROVAL_REQUESTED, kinds)
        self.assertNotIn(EventKind.ACTION_EXECUTED, kinds)
        self.assertEqual(
            dict(result.metadata)["model_failure_kind"],
            "recording",
        )

    def test_openai_noncompleted_status_rejects_valid_looking_payload(self) -> None:
        class FakeResponses:
            def parse(self, **_kwargs):
                return SimpleNamespace(
                    id="resp-not-completed",
                    model="explicit-test-model",
                    output=[],
                    output_parsed=SimpleNamespace(
                        model_dump=lambda **_kwargs: valid_payload()
                    ),
                    status="failed",
                    usage=None,
                )

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.responses = FakeResponses()

        openai = ModuleType("openai")
        openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
        openai.APIStatusError = type("APIStatusError", (Exception,), {})
        openai.APITimeoutError = type("APITimeoutError", (Exception,), {})
        openai.OpenAI = FakeOpenAI
        pydantic = ModuleType("pydantic")
        pydantic.ValidationError = type("ValidationError", (Exception,), {})
        backend = OpenAIResponsesBackend(
            model="explicit-test-model",
            timeout_seconds=1.0,
            max_output_tokens=32,
        )
        request = ModelBackedSynthesizer(
            QueueBackend([valid_payload()])
        ).build_request(canonical_board())

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
            patch.dict(
                __import__("sys").modules,
                {"openai": openai, "pydantic": pydantic},
            ),
            patch(
                "aurora_lab.model_backend._openai_diagnosis_schema",
                return_value=object,
            ),
        ):
            with self.assertRaises(ModelBackendError) as caught:
                backend.generate(request)

        self.assertEqual(caught.exception.kind, "provider")

    def test_openai_backend_reads_no_cli_or_constructor_key(self) -> None:
        backend = OpenAIResponsesBackend(
            model="explicit-test-model",
            timeout_seconds=1.0,
            max_output_tokens=32,
        )
        with self.assertRaises(ValueError):
            ModelBackedSynthesizer(backend)
        request = ModelBackedSynthesizer(
            QueueBackend([valid_payload()])
        ).build_request(canonical_board())
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModelBackendError) as caught:
                backend.generate(request)
        self.assertEqual(caught.exception.kind, "configuration")

    def test_strategy_rejects_model_mode_without_independent_critic(self) -> None:
        synthesizer = ModelBackedSynthesizer(QueueBackend([valid_payload()]))
        with self.assertRaises(ValueError):
            ConfigurableStrategy(
                SPECIALISTS_NO_CRITIC,
                comprehensive_synthesizer=synthesizer,
            )

    def test_strategy_rejects_model_mode_for_fault_studies(self) -> None:
        synthesizer = ModelBackedSynthesizer(QueueBackend([valid_payload()]))
        strategy = OrchestratedStrategy(
            comprehensive_synthesizer=synthesizer
        )
        with self.assertRaises(ValueError):
            strategy.run(build_scenario(101, fault_name="planning_omission"))

    def test_cli_requires_recording_and_independent_critic(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as missing_record:
                main(
                    [
                        "run",
                        "--agent-backend",
                        "openai",
                        "--model",
                        "explicit-test-model",
                    ]
                )
            with self.assertRaises(SystemExit) as no_critic:
                main(
                    [
                        "run",
                        "--strategy",
                        "single-agent",
                        "--agent-backend",
                        "replay",
                        "--model-record",
                        str(FIXTURE),
                    ]
                )
        self.assertEqual(missing_record.exception.code, 2)
        self.assertEqual(no_critic.exception.code, 2)

    def test_retry_limits_are_capped_at_one(self) -> None:
        with redirect_stderr(io.StringIO()):
            for option in (
                "--model-transport-retries",
                "--model-semantic-retries",
            ):
                with self.subTest(option=option):
                    with self.assertRaises(SystemExit) as caught:
                        main(
                            [
                                "run",
                                "--agent-backend",
                                "replay",
                                "--model-record",
                                str(FIXTURE),
                                option,
                                "2",
                            ]
                        )
                    self.assertEqual(caught.exception.code, 2)

        with self.assertRaises(ValueError):
            OpenAIResponsesBackend(
                model="explicit-test-model",
                timeout_seconds=1.0,
                max_output_tokens=32,
                max_transport_retries=2,
            )
        with self.assertRaises(ValueError):
            ModelBackedSynthesizer(QueueBackend([valid_payload()]), max_semantic_retries=2)

    def test_timeout_must_be_finite(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    OpenAIResponsesBackend(
                        model="explicit-test-model",
                        timeout_seconds=value,
                        max_output_tokens=32,
                    )

        with redirect_stderr(io.StringIO()):
            for value in ("nan", "inf", "-inf"):
                with self.subTest(cli_value=value):
                    with self.assertRaises(SystemExit) as caught:
                        main(
                            [
                                "run",
                                "--agent-backend",
                                "replay",
                                "--model-record",
                                str(FIXTURE),
                                "--model-timeout-seconds",
                                value,
                            ]
                        )
                    self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
