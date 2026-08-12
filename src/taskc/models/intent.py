from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import GetJsonSchemaHandler, JsonSchemaValue
from pydantic_core import CoreSchema

from .common import JsonValue, StrictModel


SourceKind = Literal[
    "user_request",
    "application_context",
    "clarification_answer",
    "contract_default",
    "model_inference",
]
DataClassification = Literal["public", "sensitive", "secret"]


class CapabilityRef(StrictModel):
    id: str
    version: str


class SourcedValue(StrictModel):
    value: JsonValue | None = None
    value_ref: str | None = None
    source: SourceKind
    source_ref: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    classification: DataClassification = "public"
    redacted: bool = False

    @model_validator(mode="after")
    def validate_value_shape(self) -> "SourcedValue":
        has_value = "value" in self.model_fields_set
        has_ref = self.value_ref is not None
        if self.redacted:
            if has_value or has_ref:
                raise ValueError("redacted values cannot contain value or value_ref")
            return self
        if has_value == has_ref:
            raise ValueError("exactly one of value or value_ref is required")
        if self.classification == "secret" and not has_ref:
            raise ValueError("secret values must use value_ref")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        schema.setdefault("allOf", []).append(
            {
                "oneOf": [
                    {
                        "properties": {"redacted": {"const": True}},
                        "required": ["redacted"],
                        "not": {"anyOf": [{"required": ["value"]}, {"required": ["value_ref"]}]},
                    },
                    {
                        "properties": {
                            "redacted": {"const": False},
                            "classification": {"not": {"const": "secret"}},
                        },
                        "required": ["value"],
                        "not": {"required": ["value_ref"]},
                    },
                    {
                        "properties": {"redacted": {"const": False}},
                        "required": ["value_ref"],
                        "not": {"required": ["value"]},
                    },
                ]
            }
        )
        return schema


class NormalizedRequest(StrictModel):
    raw_text: str
    text: str
    context: dict[str, SourcedValue] = Field(default_factory=dict)
    answers: dict[str, list[SourcedValue]] = Field(default_factory=dict)
    requested_capability: CapabilityRef | None = None


class MatchEvidence(StrictModel):
    code: str
    summary: str
    strength: float = Field(ge=0.0, le=1.0)


class CandidateDraft(StrictModel):
    capability_id: str
    capability_version: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: list[MatchEvidence]
    inputs: dict[str, SourcedValue] = Field(default_factory=dict)
    unresolved_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence(self) -> "CandidateDraft":
        if not self.evidence:
            raise ValueError("candidate must contain matching evidence")
        return self


class ExtractionResult(StrictModel):
    values: dict[str, list[SourcedValue]] = Field(default_factory=dict)
    unresolved_terms: list[str] = Field(default_factory=list)


class CandidateIntent(StrictModel):
    capability_id: str
    capability_version: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: list[MatchEvidence]
    inputs: dict[str, SourcedValue]
    unresolved_terms: list[str] = Field(default_factory=list)
    viability: Literal["viable", "rejected"] = "viable"


class DispatchReadyIntent(StrictModel):
    schema_version: Literal["0.2"]
    intent_id: str
    result_id: str
    capability_id: str
    capability_version: str
    catalog_digest: str
    inputs: dict[str, SourcedValue]
    deliverables: list[str]
    compiled_at: datetime


__all__ = [
    "CandidateDraft",
    "CandidateIntent",
    "CapabilityRef",
    "DataClassification",
    "DispatchReadyIntent",
    "ExtractionResult",
    "MatchEvidence",
    "NormalizedRequest",
    "SourceKind",
    "SourcedValue",
]
