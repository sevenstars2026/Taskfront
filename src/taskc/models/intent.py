from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .common import JsonValue, StrictModel


class SourcedValue(StrictModel):
    value: JsonValue
    source: Literal[
        "user",
        "application_context",
        "clarification_answer",
        "explicit_default",
        "model_inference",
    ]
    source_ref: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class NormalizedRequest(StrictModel):
    raw_text: str
    text: str
    context: dict[str, SourcedValue] = {}
    answers: dict[str, list[SourcedValue]] = {}
    requested_capability: str | None = None


class CandidateDraft(StrictModel):
    capability_id: str
    capability_version: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    match_reasons: list[str]
    inputs: dict[str, SourcedValue] = {}
    unresolved_terms: list[str] = []


class ExtractionResult(StrictModel):
    values: dict[str, list[SourcedValue]] = {}
    unresolved_terms: list[str] = []


class CandidateIntent(StrictModel):
    capability_id: str
    capability_version: str
    score: float = Field(ge=0.0, le=1.0)
    match_reasons: list[str]
    inputs: dict[str, SourcedValue]
    unresolved_terms: list[str] = []


class DispatchReadyIntent(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    intent_id: str
    capability_id: str
    capability_version: str
    inputs: dict[str, SourcedValue]
    deliverables: list[str]
    compiled_at: datetime

    @field_validator("inputs")
    @classmethod
    def reject_reserved_input_names(cls, value: dict[str, SourcedValue]) -> dict[str, SourcedValue]:
        reserved = {
            "permission", "approval", "risk", "policy", "credential", "effect",
            "executable_code", "workflow_bytecode",
        }
        overlap = reserved & set(value)
        if overlap:
            raise ValueError(f"dispatch intent contains reserved fields: {sorted(overlap)}")
        return value

