from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from taskc import TaskCompiler
from taskc.adapters import envelope_to_json, result_to_json
from taskc.evaluation import EvaluationScenario
from taskc.models import CompileEnvelope
from taskc.schema import SCHEMAS


def _assert_valid(schema: dict, payload: dict) -> None:
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


def _assert_invalid(schema: dict, payload: dict) -> None:
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_checked_in_schemas_match_canonical_generation() -> None:
    root = Path(__file__).parents[2] / "schemas"
    for filename, model in SCHEMAS.items():
        checked_in = json.loads((root / filename).read_text(encoding="utf-8"))
        assert checked_in == model.model_json_schema(mode="validation")


def test_all_v02_schemas_have_positive_and_negative_examples(
    fix_bug_contract: dict,
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    result = compiler.compile(
        "Fix checkout",
        {"repository": "current", "problem_description": "Checkout fails"},
    )
    session = compiler.get_session()
    scenario = EvaluationScenario(
        schema_version="0.2",
        id="SCHEMA-1",
        request="Fix checkout",
        expected_capabilities=["repository.fix_bug@1.0.0"],
        expected_status="ready",
    )
    payloads = {
        "capability-contract-v0.2.schema.json": fix_bug_contract,
        "compilation-result-v0.2.schema.json": json.loads(result_to_json(result)),
        "compile-envelope-v0.2.schema.json": json.loads(
            envelope_to_json(
                CompileEnvelope(
                    schema_version="0.2",
                    kind="result", serialization_profile="public", result=result
                )
            )
        ),
        "dispatch-ready-intent-v0.2.schema.json": result.intent.model_dump(
            mode="json", exclude_none=True
        ),
        "compilation-session-v0.2.schema.json": session.model_dump(
            mode="json", exclude_none=True
        ),
        "evaluation-scenario-v0.2.schema.json": scenario.model_dump(
            mode="json", exclude_none=True
        ),
    }
    negatives = {
        "capability-contract-v0.2.schema.json": {
            **fix_bug_contract,
            "schema_version": "0.1",
        },
        "compilation-result-v0.2.schema.json": {"status": "ready"},
        "compile-envelope-v0.2.schema.json": {
            "schema_version": "0.2",
            "kind": "result",
        },
        "dispatch-ready-intent-v0.2.schema.json": {
            key: value
            for key, value in result.intent.model_dump(mode="json", exclude_none=True).items()
            if key != "result_id"
        },
        "compilation-session-v0.2.schema.json": {
            key: value
            for key, value in session.model_dump(mode="json", exclude_none=True).items()
            if key != "revision"
        },
        "evaluation-scenario-v0.2.schema.json": {
            **scenario.model_dump(mode="json", exclude_none=True),
            "expected_status": "invalid",
        },
    }
    for filename, payload in payloads.items():
        schema = SCHEMAS[filename].model_json_schema(mode="validation")
        _assert_valid(schema, payload)
        _assert_invalid(schema, negatives[filename])
