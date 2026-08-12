from __future__ import annotations

import asyncio

import pytest

from taskc.contracts import load_catalog_objects
from taskc.models import CandidateDraft, CapabilityContract, NormalizedRequest, ProviderError
from taskc.providers import (
    CachedStructuredModelProvider,
    InMemoryProviderCache,
    ModelCandidateProvider,
    ModelExtractionProvider,
    StaticCandidateProvider,
    StaticInterpretationProvider,
    run_provider_conformance,
    ModelInterpretationProvider,
    OpenAICompatibleProvider,
)
from taskc.config import CompilerConfig


class InvalidModel:
    provider_id = "invalid"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {
            "candidates": [
                {
                    "capability_id": "repository.fix_bug",
                    "capability_version": "1.0.0",
                    "score": 1.0,
                    "evidence": [{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
                    "unexpected": True,
                }
            ]
        }


class UnknownCapabilityModel:
    provider_id = "unknown"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {
            "candidates": [
                {
                    "capability_id": "unknown.capability",
                    "capability_version": "1.0.0",
                    "score": 1.0,
                    "evidence": [{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
                }
            ]
        }


class CountingModel:
    provider_id = "counting"
    config_version = "test"

    def __init__(self):
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        return {"ok": True}


class FlakyCandidateModel:
    provider_id = "flaky"
    config_version = "test"

    def __init__(self):
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"candidates": [{"invalid": True}]}
        return {
            "candidates": [
                {
                    "capability_id": "repository.fix_bug",
                    "capability_version": "1.0.0",
                    "score": 1.0,
                    "evidence": [{"code": "MATCH-TEST", "summary": "retry succeeded", "strength": 1.0}],
                }
            ]
        }


class ExtractionModel:
    provider_id = "extraction"
    config_version = "test"

    def __init__(self, inputs):
        self.inputs = inputs

    async def generate_json(self, **kwargs):
        return {"inputs": self.inputs, "unresolved_terms": []}


def test_static_provider_only_returns_catalog_capability(fix_bug_contract: dict) -> None:
    catalog = load_catalog_objects([fix_bug_contract])
    candidates = asyncio.run(
        StaticCandidateProvider().generate(
            NormalizedRequest(raw_text="Fix bug", text="Fix bug"), catalog, 5
        )
    )
    assert [item.capability_id for item in candidates] == ["repository.fix_bug"]


def test_model_provider_rejects_extra_fields(fix_bug_contract: dict) -> None:
    catalog = load_catalog_objects([fix_bug_contract])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(
            ModelCandidateProvider(InvalidModel()).generate(
                NormalizedRequest(raw_text="Fix bug", text="Fix bug"), catalog, 5
            )
        )
    assert raised.value.diagnostics[0].code == "E-PROVIDER-SCHEMA"


def test_model_provider_rejects_unknown_capability(fix_bug_contract: dict) -> None:
    catalog = load_catalog_objects([fix_bug_contract])
    with pytest.raises(ProviderError) as raised:
        asyncio.run(
            ModelCandidateProvider(UnknownCapabilityModel()).generate(
                NormalizedRequest(raw_text="Fix bug", text="Fix bug"), catalog, 5
            )
        )
    assert raised.value.diagnostics[0].code == "E-PROVIDER-UNKNOWN-CAPABILITY"


def test_structured_provider_cache_reuses_raw_response() -> None:
    model = CountingModel()
    cached = CachedStructuredModelProvider(model, InMemoryProviderCache())

    async def exercise():
        kwargs = {
            "system_instruction": "test",
            "payload": {"request": "hello"},
            "response_schema": {"type": "object"},
            "request_id": "first",
        }
        first = await cached.generate_json(**kwargs)
        second = await cached.generate_json(**{**kwargs, "request_id": "second"})
        return first, second

    first, second = asyncio.run(exercise())
    assert first == second == {"ok": True}
    assert model.calls == 1


def test_model_provider_retries_schema_failure(fix_bug_contract: dict) -> None:
    catalog = load_catalog_objects([fix_bug_contract])
    model = FlakyCandidateModel()
    candidates = asyncio.run(
        ModelCandidateProvider(model, retries=1).generate(
            NormalizedRequest(raw_text="Fix bug", text="Fix bug"), catalog, 5
        )
    )
    assert candidates[0].capability_id == "repository.fix_bug"
    assert model.calls == 2


def test_model_extraction_forces_inference_provenance(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    provider = ModelExtractionProvider(
        ExtractionModel(
            {
                "repository": {
                    "value": "guessed",
                    "source": "user_request",
                    "source_ref": "untrusted",
                }
            }
        )
    )
    result = asyncio.run(
        provider.extract(
            NormalizedRequest(raw_text="Fix bug", text="Fix bug"),
            contract,
            CandidateDraft(
                capability_id=contract.id,
                capability_version=contract.version,
                score=1.0,
                evidence=[{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
            ),
        )
    )
    assert result.values["repository"][0].source == "model_inference"
    assert result.values["repository"][0].source_ref == "provider"


def test_model_extraction_rejects_undeclared_fields(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    provider = ModelExtractionProvider(
        ExtractionModel({"permission": {"value": True, "source": "model_inference"}})
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(
            provider.extract(
                NormalizedRequest(raw_text="Fix bug", text="Fix bug"),
                contract,
                CandidateDraft(
                    capability_id=contract.id,
                    capability_version=contract.version,
                    score=1.0,
                    evidence=[{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
                ),
            )
        )
    assert raised.value.diagnostics[0].code == "E-PROVIDER-UNKNOWN-FIELD"


def test_static_provider_passes_published_conformance_check(fix_bug_contract: dict) -> None:
    catalog = load_catalog_objects([fix_bug_contract])
    report = asyncio.run(
        run_provider_conformance(
            StaticInterpretationProvider(CompilerConfig()),
            NormalizedRequest(raw_text="Fix checkout", text="Fix checkout"),
            catalog,
        )
    )
    assert report.passed
    assert report.violations == []


class CapturingModel:
    provider_id = "capturing"
    config_version = "test"

    def __init__(self):
        self.payload = None

    async def generate_json(self, **kwargs):
        self.payload = kwargs["payload"]
        return {"candidates": []}


def test_model_provider_redacts_classified_assignments_and_defaults() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect account.",
        "examples": ["Connect account"],
        "required_inputs": {
            "token": {
                "type": "string",
                "description": "Sensitive token.",
                "classification": "sensitive",
                "default": "contract-sensitive-default",
            }
        },
        "deliverables": ["connection"],
    }
    model = CapturingModel()
    compiler = __import__("taskc").TaskCompiler.from_contracts(
        [contract], interpretation_provider=ModelInterpretationProvider(model)
    )
    result = compiler.compile("Connect account token=request-sensitive-value")
    assert result.status == "unsupported"
    serialized = str(model.payload)
    assert "request-sensitive-value" not in serialized
    assert "contract-sensitive-default" not in serialized
    assert "[REDACTED]" in serialized


def test_openai_compatible_provider_requires_https_and_hides_api_key() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(
            endpoint="http://example.com/v1/chat/completions",
            api_key="key-secret",
            model="test-model",
        )
    provider = OpenAICompatibleProvider(
        endpoint="https://example.com/v1/chat/completions",
        api_key="key-secret",
        model="test-model",
    )
    assert "key-secret" not in repr(provider)
