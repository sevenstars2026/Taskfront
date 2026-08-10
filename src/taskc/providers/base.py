from __future__ import annotations

from typing import Protocol

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
    async def render(self, *, text: str, field_path: str, sensitive: bool) -> str: ...


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
    "CandidateProvider", "ExtractionProvider", "QuestionRenderer", "StructuredModelProvider"
]

