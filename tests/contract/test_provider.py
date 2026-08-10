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
)


class InvalidModel:
    provider_id = "invalid"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {
            "candidates": [
                {
                    "capability_id": "repository.fix_bug",
                    "score": 1.0,
                    "match_reasons": ["test"],
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
                    "score": 1.0,
                    "match_reasons": ["test"],
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
                    "match_reasons": ["retry succeeded"],
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
                    "source": "user",
                    "source_ref": "untrusted",
                }
            }
        )
    )
    result = asyncio.run(
        provider.extract(
            NormalizedRequest(raw_text="Fix bug", text="Fix bug"),
            contract,
            CandidateDraft(capability_id=contract.id, score=1.0, match_reasons=["test"]),
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
                CandidateDraft(capability_id=contract.id, score=1.0, match_reasons=["test"]),
            )
        )
    assert raised.value.diagnostics[0].code == "E-PROVIDER-UNKNOWN-FIELD"
