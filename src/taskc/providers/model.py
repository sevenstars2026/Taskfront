from __future__ import annotations

import json
import re
import uuid

from pydantic import Field, ValidationError

from taskc.budget import ProviderBudget
from taskc.config import ProviderBudgetConfig
from taskc.contracts import CapabilityCatalog
from taskc.models import (
    CandidateDraft,
    CapabilityContract,
    Diagnostic,
    ExtractionResult,
    MatchEvidence,
    NormalizedRequest,
    ProviderError,
    SourcedValue,
)
from taskc.models.common import StrictModel

from .base import StructuredModelProvider


class _CandidateResponse(StrictModel):
    candidates: list[CandidateDraft] = Field(max_length=50)


class _ExtractionResponse(StrictModel):
    inputs: dict[str, SourcedValue] = Field(default_factory=dict, max_length=200)
    unresolved_terms: list[str] = Field(default_factory=list, max_length=200)


def _public_contract_payload(contract: CapabilityContract) -> dict:
    payload = contract.model_dump(mode="json", by_alias=True, exclude_none=True)
    for section in ("required_inputs", "optional_inputs"):
        for spec in payload.get(section, {}).values():
            if spec.get("classification", "public") != "public":
                spec.pop("default", None)
    return payload


def _redact_classified_assignments(
    text: str, contracts
) -> str:
    field_names = {
        field_name
        for contract in contracts
        for field_name, spec in contract.all_inputs.items()
        if spec.classification != "public"
    }
    redacted = text
    for field_name in sorted(field_names, key=len, reverse=True):
        redacted = re.sub(
            rf"(?i)(\b{re.escape(field_name)}\s*[:=]\s*)[^,;\n]+",
            rf"\1[REDACTED]",
            redacted,
        )
    return redacted


def _provider_validation_error(exc: ValidationError) -> ProviderError:
    diagnostic = Diagnostic(
        code="E-PROVIDER-SCHEMA",
        severity="error",
        phase="provider_validation",
        message="Provider response failed strict schema validation.",
    )
    return ProviderError(diagnostic.message, [diagnostic])


def _validate_candidates(
    parsed: _CandidateResponse,
    catalog: CapabilityCatalog,
    limit: int,
) -> list[CandidateDraft]:
    if len(parsed.candidates) > limit:
        diagnostic = Diagnostic(
            code="E-PROVIDER-CANDIDATE-LIMIT",
            severity="error",
            phase="provider_validation",
            message="Provider returned more candidates than requested.",
        )
        raise ProviderError(diagnostic.message, [diagnostic], retryable=False)
    valid: list[CandidateDraft] = []
    for candidate in parsed.candidates:
        contract = catalog.get(candidate.capability_id, candidate.capability_version)
        if contract is None:
            diagnostic = Diagnostic(
                code="E-PROVIDER-UNKNOWN-CAPABILITY",
                severity="error",
                phase="provider_validation",
                message="Provider returned a capability or exact version outside the catalog.",
                contract_ref=f"{candidate.capability_id}@{candidate.capability_version}",
            )
            raise ProviderError(diagnostic.message, [diagnostic])
        unknown = set(candidate.inputs) - set(contract.all_inputs)
        if unknown:
            diagnostic = Diagnostic(
                code="E-PROVIDER-UNKNOWN-FIELD",
                severity="error",
                phase="provider_validation",
                message="Provider candidate returned undeclared capability inputs.",
                field_path=sorted(unknown)[0],
                contract_ref=f"{contract.id}@{contract.version}",
            )
            raise ProviderError(diagnostic.message, [diagnostic])
        inputs = {
            field_name: sourced.model_copy(
                update={
                    "source": "model_inference",
                    "source_ref": "provider",
                    "classification": contract.all_inputs[field_name].classification,
                }
            )
            for field_name, sourced in candidate.inputs.items()
        }
        valid.append(candidate.model_copy(update={"inputs": inputs}))
    return valid


class ModelInterpretationProvider:
    config_version = "0.2"
    score_semantics = "uncalibrated"

    def __init__(self, model: StructuredModelProvider, *, retries: int = 1):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.model = model
        self.retries = retries
        self.provider_id = f"model-interpretation:{model.provider_id}"
        self.config_version = (
            f"0.2:model={model.provider_id}:{model.config_version}:retries={retries}"
        )

    async def interpret(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        *,
        max_candidates: int,
        budget: ProviderBudget,
    ) -> list[CandidateDraft]:
        budget.consume_logical_call()
        parsed: _CandidateResponse | None = None
        last_failure: Exception | None = None
        for _attempt in range(self.retries + 1):
            budget.consume_network_attempt()
            try:
                response = await self.model.generate_json(
                    system_instruction=(
                        "Select only exact capability versions from the supplied catalog and extract only "
                        "their declared inputs. Preserve uncertainty and provide structured evidence."
                    ),
                    payload={
                        "request": _redact_classified_assignments(
                            request.text, catalog.contracts
                        ),
                        "max_candidates": max_candidates,
                        "capabilities": [
                            _public_contract_payload(item)
                            for item in catalog.active_contracts()
                        ],
                    },
                    response_schema=_CandidateResponse.model_json_schema(),
                    request_id=str(uuid.uuid4()),
                )
                if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > budget.config.max_response_bytes:
                    diagnostic = Diagnostic(
                        code="E-PROVIDER-RESPONSE-SIZE",
                        severity="error",
                        phase="provider_validation",
                        message="Provider response exceeded the configured size limit.",
                    )
                    raise ProviderError(diagnostic.message, [diagnostic], retryable=False)
                parsed = _CandidateResponse.model_validate(response)
                break
            except (ProviderError, ValidationError) as exc:
                last_failure = exc
                if isinstance(exc, ProviderError) and not exc.error.retryable:
                    break
        if parsed is None:
            if isinstance(last_failure, ProviderError):
                raise last_failure
            assert isinstance(last_failure, ValidationError)
            raise _provider_validation_error(last_failure) from last_failure
        return _validate_candidates(parsed, catalog, max_candidates)


class ModelCandidateProvider:
    """Compatibility candidate-only provider for 0.1 host integrations."""

    config_version = "0.2"
    score_semantics = "uncalibrated"

    def __init__(self, model: StructuredModelProvider, *, retries: int = 1):
        self.interpreter = ModelInterpretationProvider(model, retries=retries)
        self.provider_id = f"model-candidate:{model.provider_id}"
        self.config_version = self.interpreter.config_version

    async def generate(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        limit: int,
    ) -> list[CandidateDraft]:
        budget = ProviderBudget(ProviderBudgetConfig())
        return await self.interpreter.interpret(
            request, catalog, max_candidates=limit, budget=budget
        )


class ModelExtractionProvider:
    """Compatibility extractor; new integrations should use ModelInterpretationProvider."""

    config_version = "0.2"

    def __init__(self, model: StructuredModelProvider, *, retries: int = 1):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.model = model
        self.retries = retries
        self.provider_id = f"model-extraction:{model.provider_id}"
        self.config_version = (
            f"0.2:model={model.provider_id}:{model.config_version}:retries={retries}"
        )

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
                    system_instruction="Extract only declared capability inputs.",
                    payload={
                        "request": _redact_classified_assignments(
                            request.text, (contract,)
                        ),
                        "capability": _public_contract_payload(contract),
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
                phase="provider_validation",
                message="Provider returned undeclared capability inputs.",
                field_path=sorted(unknown)[0],
                contract_ref=f"{contract.id}@{contract.version}",
            )
            raise ProviderError(diagnostic.message, [diagnostic])
        values = {
            field_name: [
                sourced.model_copy(
                    update={
                        "source": "model_inference",
                        "source_ref": "provider",
                        "classification": contract.all_inputs[field_name].classification,
                    }
                )
            ]
            for field_name, sourced in parsed.inputs.items()
        }
        return ExtractionResult(values=values, unresolved_terms=parsed.unresolved_terms)


__all__ = [
    "ModelCandidateProvider",
    "ModelExtractionProvider",
    "ModelInterpretationProvider",
]
