from __future__ import annotations

import asyncio

import pytest
from jsonschema import Draft202012Validator

from taskc import CompilerConfig, ProviderBudgetConfig, TaskCompiler
from taskc.budget import ProviderBudget
from taskc.contracts import CapabilityCatalog, load_catalog_objects
from taskc.models import (
    BudgetExceededError,
    CapabilityContract,
    ConditionExpression,
    ProviderError,
    TruthValue,
)
from taskc.models.capability import evaluate_condition
from taskc.providers import ModelInterpretationProvider
from taskc.session import InMemorySessionStore
from taskc.versions import SemVer


def _contract(version: str = "1.0.0") -> dict:
    return {
        "schema_version": "0.2",
        "id": "example.process",
        "version": version,
        "description": "Process an example record.",
        "examples": ["Process this record"],
        "negative_examples": ["Write a poem"],
        "required_inputs": {"record": {"type": "string", "description": "Record."}},
        "deliverables": ["result"],
    }


def test_semver_official_prerelease_order_and_build_precedence() -> None:
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    assert [item.raw for item in sorted(SemVer.parse(value) for value in reversed(ordered))] == ordered
    assert SemVer.parse("1.0.0+build.1").same_precedence(SemVer.parse("1.0.0+build.2"))


def test_catalog_prerelease_opt_in_and_equal_build_precedence() -> None:
    catalog = load_catalog_objects([_contract("1.0.0"), _contract("2.0.0-alpha.1")])
    assert catalog.resolve("example.process").version == "1.0.0"
    assert catalog.resolve("example.process", allow_prerelease=True).version == "2.0.0-alpha.1"
    ambiguous = CapabilityCatalog.build([
        CapabilityContract.model_validate(_contract("1.0.0+build.1")),
        CapabilityContract.model_validate(_contract("1.0.0+build.2")),
    ])
    with pytest.raises(Exception):
        ambiguous.resolve("example.process")


def test_three_value_condition_logic() -> None:
    equals = ConditionExpression.model_validate({"field": "a", "equals": 1})
    assert evaluate_condition(equals, {}) is TruthValue.UNKNOWN
    assert evaluate_condition(equals, {"a": 1}) is TruthValue.TRUE
    assert evaluate_condition(equals, {"a": 2}) is TruthValue.FALSE
    any_expression = ConditionExpression.model_validate({
        "any": [{"field": "a", "equals": 1}, {"field": "b", "equals": 2}]
    })
    assert evaluate_condition(any_expression, {"a": 0}) is TruthValue.UNKNOWN


def test_compilation_result_schema_rejects_status_only_ready() -> None:
    from taskc.models import CompilationResult

    errors = list(Draft202012Validator(CompilationResult.model_json_schema()).iter_errors({"status": "ready"}))
    assert errors


def test_session_store_compare_and_swap(fix_bug_contract: dict) -> None:
    store = InMemorySessionStore()
    compiler = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    compiler.compile("Fix checkout", {"repository": "current"})
    session = compiler.get_session()
    session.revision = 1
    store.put(session, expected_revision=0)
    session.revision = 2
    with pytest.raises(Exception):
        store.put(session, expected_revision=0)


class _AlwaysInvalidModel:
    provider_id = "always-invalid"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {"candidates": [{"invalid": True}]}


def test_provider_retry_obeys_global_network_budget() -> None:
    compiler = TaskCompiler.from_contracts(
        [_contract()],
        interpretation_provider=ModelInterpretationProvider(_AlwaysInvalidModel(), retries=3),
        config=CompilerConfig(
            provider_budget=ProviderBudgetConfig(max_network_attempts=1)
        ),
    )
    with pytest.raises(BudgetExceededError):
        compiler.compile("Process this record", {"record": "one"})


class _TimeoutProvider:
    provider_id = "timeout"
    config_version = "test"
    score_semantics = "uncalibrated"

    async def interpret(self, request, catalog, *, max_candidates, budget):
        raise TimeoutError("provider timeout")


def test_provider_timeout_is_not_domain_unsupported() -> None:
    compiler = TaskCompiler.from_contracts([_contract()], interpretation_provider=_TimeoutProvider())
    with pytest.raises(ProviderError) as raised:
        compiler.compile("Process this record", {"record": "one"})
    assert raised.value.error.category == "provider_failure"

