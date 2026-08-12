from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import GetJsonSchemaHandler, JsonSchemaValue
from pydantic_core import CoreSchema

from .common import JsonObject, JsonValue, StrictModel
from .intent import NormalizedRequest


class ClarificationQuestion(StrictModel):
    id: str
    result_id: str
    revision: int = Field(ge=0)
    text: str
    targets: list[str]
    answer_type: Literal[
        "text", "single_choice", "multi_choice", "boolean", "integer", "number", "json"
    ]
    choices: list[JsonValue] = Field(default_factory=list)
    priority: float
    reason_code: str


class ClarificationAnswer(StrictModel):
    question_id: str
    result_id: str
    revision: int = Field(ge=0)
    value: JsonValue | None = None
    value_ref: str | None = None
    redacted: bool = False

    @model_validator(mode="after")
    def exactly_one_value(self) -> "ClarificationAnswer":
        has_value = "value" in self.model_fields_set
        has_ref = self.value_ref is not None
        if self.redacted:
            if has_value or has_ref:
                raise ValueError("redacted answers cannot contain value or value_ref")
            return self
        if has_value == has_ref:
            raise ValueError("exactly one of value or value_ref is required")
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
                        "properties": {"redacted": {"const": False}},
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


class AnswerRecord(StrictModel):
    answer: ClarificationAnswer
    targets: list[str]
    classification: Literal["public", "sensitive", "secret"] = "public"
    accepted_at: datetime


class CompilationSession(StrictModel):
    schema_version: Literal["0.2"]
    session_id: str
    revision: int = Field(ge=0)
    request: NormalizedRequest
    catalog_digest: str
    config_digest: str
    provider_fingerprints: list[str]
    contract_fingerprints: dict[str, str]
    contract_snapshots: dict[str, JsonObject] = Field(default_factory=dict)
    extension_fingerprints: list[str] = Field(default_factory=list)
    answers: list[AnswerRecord]
    last_result: "CompilationResult"
    created_at: datetime
    updated_at: datetime


from .result import CompilationResult  # noqa: E402

CompilationSession.model_rebuild()


__all__ = [
    "AnswerRecord",
    "ClarificationAnswer",
    "ClarificationQuestion",
    "CompilationSession",
]
