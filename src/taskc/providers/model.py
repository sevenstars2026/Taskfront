from __future__ import annotations

import uuid

from pydantic import Field, ValidationError

from taskc.contracts import CapabilityCatalog
from taskc.models import (
    CandidateDraft,
    CapabilityContract,
    ExtractionResult,
    NormalizedRequest,
    ProviderError,
    SourcedValue,
)
from taskc.models.common import StrictModel
from taskc.models.diagnostics import Diagnostic

from .base import StructuredModelProvider


class _CandidateResponse(StrictModel):
    candidates: list[CandidateDraft] = Field(max_length=50)


class _ExtractionResponse(StrictModel):
    inputs: dict[str, SourcedValue] = Field(default_factory=dict, max_length=200)
    unresolved_terms: list[str] = Field(default_factory=list, max_length=200)


def _provider_validation_error(exc: ValidationError) -> ProviderError:
    diagnostic = Diagnostic(
        code="E-PROVIDER-SCHEMA",
        severity="error",
        message="Provider response failed strict schema validation.",
    )
    return ProviderError(diagnostic.message, [diagnostic])


class ModelCandidateProvider:
    config_version = "0.1"

    def __init__(self, model: StructuredModelProvider, *, retries: int = 1):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.model = model
        self.retries = retries
        self.provider_id = f"model-candidate:{model.provider_id}"

    async def generate(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        limit: int,
    ) -> list[CandidateDraft]:
        parsed: _CandidateResponse | None = None
        last_failure: Exception | None = None
        for _attempt in range(self.retries + 1):
            try:
                response = await self.model.generate_json(
                    system_instruction=(
                        "Select up to the requested limit from the supplied capability catalog. "
                        "Do not invent capabilities or fields. Preserve uncertainty."
                    ),
                    payload={
                        "request": request.text,
                        "limit": limit,
                        "capabilities": [
                            {
                                "id": item.id,
                                "version": item.version,
                                "description": item.description,
                                "examples": item.examples,
                                "negative_examples": item.negative_examples,
                            }
                            for item in catalog.contracts
                        ],
                    },
                    response_schema=_CandidateResponse.model_json_schema(),
                    request_id=str(uuid.uuid4()),
                )
                parsed = _CandidateResponse.model_validate(response)
                break
            except (ProviderError, ValidationError) as exc:
                last_failure = exc
        if parsed is None:
            if isinstance(last_failure, ProviderError):
                raise last_failure
            assert isinstance(last_failure, ValidationError)
            raise _provider_validation_error(last_failure) from last_failure
        valid: list[CandidateDraft] = []
        unknown_ids: list[str] = []
        for candidate in parsed.candidates[:limit]:
            contract = catalog.get(candidate.capability_id, candidate.capability_version)
            if contract is None:
                unknown_ids.append(candidate.capability_id)
                continue
            inputs = {
                field_name: sourced.model_copy(
                    update={"source": "model_inference", "source_ref": "provider"}
                )
                for field_name, sourced in candidate.inputs.items()
            }
            valid.append(candidate.model_copy(update={"inputs": inputs}))
        if not valid and unknown_ids:
            diagnostic = Diagnostic(
                code="E-PROVIDER-UNKNOWN-CAPABILITY",
                severity="error",
                message="Provider returned only capabilities outside the supplied catalog.",
            )
            raise ProviderError(diagnostic.message, [diagnostic])
        return valid


class ModelExtractionProvider:
    config_version = "0.1"

    def __init__(self, model: StructuredModelProvider, *, retries: int = 1):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.model = model
        self.retries = retries
        self.provider_id = f"model-extraction:{model.provider_id}"

    async def extract(
        self,
        request: NormalizedRequest,
        contract: CapabilityContract,
        candidate: CandidateDraft,
    ) -> ExtractionResult:
        parsed: _ExtractionResponse | None = None
        last_failure: Exception | None = None
        for _attempt in range(self.retries + 1):
            try:
                response = await self.model.generate_json(
                    system_instruction=(
                        "Extract only declared capability inputs. Values derived from request text must use "
                        "source model_inference. Put undeclared concepts in unresolved_terms."
                    ),
                    payload={
                        "request": request.text,
                        "capability": contract.model_dump(mode="json", by_alias=True, exclude_none=True),
                        "candidate": candidate.model_dump(mode="json", exclude_none=True),
                    },
                    response_schema=_ExtractionResponse.model_json_schema(),
                    request_id=str(uuid.uuid4()),
                )
                parsed = _ExtractionResponse.model_validate(response)
                break
            except (ProviderError, ValidationError) as exc:
                last_failure = exc
        if parsed is None:
            if isinstance(last_failure, ProviderError):
                raise last_failure
            assert isinstance(last_failure, ValidationError)
            raise _provider_validation_error(last_failure) from last_failure
        unknown = set(parsed.inputs) - set(contract.all_inputs)
        if unknown:
            diagnostic = Diagnostic(
                code="E-PROVIDER-UNKNOWN-FIELD",
                severity="error",
                message="Provider returned undeclared capability inputs.",
                field_path=sorted(unknown)[0],
                contract_id=contract.id,
            )
            raise ProviderError(diagnostic.message, [diagnostic])
        values = {
            field_name: [
                sourced.model_copy(update={"source": "model_inference", "source_ref": "provider"})
            ]
            for field_name, sourced in parsed.inputs.items()
        }
        return ExtractionResult(values=values, unresolved_terms=parsed.unresolved_terms)


__all__ = ["ModelCandidateProvider", "ModelExtractionProvider"]
