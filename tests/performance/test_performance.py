from __future__ import annotations

import math
import time

from taskc import CompilerConfig, TaskCompiler
from taskc.compiler.normalize import normalize_request
from taskc.compiler.questions import plan_questions
from taskc.contracts import load_catalog_objects
from taskc.models import CapabilityContract, Gap


def _p95(durations: list[float]) -> float:
    return sorted(durations)[math.ceil(len(durations) * 0.95) - 1]


def _contracts(count: int) -> list[dict]:
    return [
        {
            "schema_version": "0.2",
            "id": f"benchmark.capability_{index}",
            "version": "1.0.0",
            "description": f"Handle benchmark task {index}.",
            "examples": [f"Handle benchmark task {index}"],
            "negative_examples": ["Write a poem"],
            "required_inputs": {
                "value": {"type": "string", "description": "Value."}
            },
            "deliverables": ["result"],
        }
        for index in range(count)
    ]


def test_one_hundred_contracts_validate_within_v02_target() -> None:
    contracts = _contracts(100)
    durations = []
    for _ in range(30):
        started = time.perf_counter()
        load_catalog_objects(contracts)
        durations.append(time.perf_counter() - started)
    assert _p95(durations) < 0.250


def test_five_candidate_hot_compile_within_v02_target() -> None:
    compiler = TaskCompiler.from_contracts(_contracts(5))
    durations = []
    for _ in range(30):
        started = time.perf_counter()
        compiler.compile("Handle benchmark task", {"value": "x"})
        durations.append(time.perf_counter() - started)
    assert _p95(durations) < 0.050


def test_question_planning_within_v02_target() -> None:
    contract = CapabilityContract.model_validate(_contracts(1)[0])
    gaps = [
        Gap(
            code="GAP-MISSING-REQUIRED",
            kind="missing",
            field_paths=["inputs.value"],
            message="Value is missing.",
            blocking=True,
            suggested_resolution="ask_user",
        )
    ]
    durations = []
    for _ in range(30):
        started = time.perf_counter()
        plan_questions(
            gaps,
            contract,
            CompilerConfig(),
            result_id="result",
            revision=0,
        )
        durations.append(time.perf_counter() - started)
    assert _p95(durations) < 0.020


def test_large_context_normalization_within_v02_target() -> None:
    config = CompilerConfig(max_request_length=120_000)
    context = {"blob": "x" * 100_000}
    durations = []
    for _ in range(30):
        started = time.perf_counter()
        normalize_request("Benchmark", context, config)
        durations.append(time.perf_counter() - started)
    assert _p95(durations) < 0.030
