from __future__ import annotations

from typing import Protocol

from taskc.budget import ProviderBudget
from taskc.contracts import CapabilityCatalog
from taskc.models import CandidateDraft, CapabilityContract, ExtractionResult, NormalizedRequest
from taskc.models.common import JsonObject


class CandidateProvider(Protocol):
    provider_id: str
    config_version: str

    async def generate(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        limit: int,
    ) -> list[CandidateDraft]: ...


class ExtractionProvider(Protocol):
    provider_id: str
    config_version: str

    async def extract(
        self,
        request: NormalizedRequest,
        contract: CapabilityContract,
        candidate: CandidateDraft,
    ) -> ExtractionResult: ...


class QuestionRenderer(Protocol):
    async def render(self, *, text: str, field_path: str, classification: str) -> str: ...


class InterpretationProvider(Protocol):
    provider_id: str
    config_version: str
    score_semantics: str

    async def interpret(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        *,
        max_candidates: int,
        budget: ProviderBudget,
    ) -> list[CandidateDraft]: ...


class StructuredModelProvider(Protocol):
    provider_id: str
    config_version: str

    async def generate_json(
        self,
        *,
        system_instruction: str,
        payload: JsonObject,
        response_schema: JsonObject,
        request_id: str,
    ) -> JsonObject: ...


__all__ = [
    "CandidateProvider", "ExtractionProvider", "InterpretationProvider", "QuestionRenderer",
    "StructuredModelProvider"
]
