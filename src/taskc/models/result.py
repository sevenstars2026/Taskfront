from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import GetJsonSchemaHandler, JsonSchemaValue
from pydantic_core import CoreSchema

from .clarification import ClarificationQuestion
from .common import JsonValue, StrictModel
from .diagnostics import Diagnostic, OperationError
from .intent import CandidateIntent, DispatchReadyIntent
from .trace import ExplainTrace


class Gap(StrictModel):
    code: str
    kind: Literal["missing", "ambiguous", "conflicting", "unverifiable", "constraint"]
    field_paths: list[str] = Field(default_factory=list)
    message: str
    blocking: bool
    candidate_values: list[JsonValue] = Field(default_factory=list)
    suggested_resolution: Literal[
        "ask_user", "use_context", "apply_default", "choose_candidate", "reject"
    ]

    @property
    def field_path(self) -> str | None:
        return self.field_paths[0] if self.field_paths else None


def _nonnull() -> JsonSchemaValue:
    return {"not": {"type": "null"}}


class CompilationResult(StrictModel):
    schema_version: Literal["0.2"]
    result_id: str
    session_id: str
    revision: int = Field(ge=0)
    catalog_digest: str
    reproducible: bool = True
    status: Literal[
        "ready", "needs_clarification", "ambiguous", "conflicting", "unsupported"
    ]
    candidates: list[CandidateIntent] = Field(default_factory=list)
    selected_candidate: CandidateIntent | None = None
    gaps: list[Gap] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    intent: DispatchReadyIntent | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    decision_codes: list[str] = Field(default_factory=list)
    trace: ExplainTrace | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "CompilationResult":
        blocking = [gap for gap in self.gaps if gap.blocking]
        viable = [item for item in self.candidates if item.viability == "viable"]
        if self.status == "ready":
            if self.intent is None or self.selected_candidate is None or blocking or self.questions:
                raise ValueError("ready requires intent and selected candidate and forbids blockers/questions")
        elif self.intent is not None:
            raise ValueError("only ready results may contain intent")
        if self.status == "needs_clarification":
            if self.selected_candidate is None or not blocking or not self.questions:
                raise ValueError("needs_clarification requires a candidate, blocker, and question")
        if self.status == "ambiguous":
            if len(viable) < 2 or self.selected_candidate is not None or not self.questions:
                raise ValueError("ambiguous requires two viable candidates and a question")
            if not any(gap.kind == "ambiguous" and gap.blocking for gap in self.gaps):
                raise ValueError("ambiguous requires a blocking ambiguity gap")
        if self.status == "conflicting":
            if self.selected_candidate is None or not self.questions:
                raise ValueError("conflicting requires a selected candidate and resolution question")
            if not any(gap.kind == "conflicting" and gap.blocking for gap in self.gaps):
                raise ValueError("conflicting requires a blocking conflict")
        if self.status == "unsupported":
            if viable or self.selected_candidate is not None or self.questions or not self.decision_codes:
                raise ValueError("unsupported requires no viable candidate and at least one decision code")
        if self.selected_candidate is not None:
            ref = (self.selected_candidate.capability_id, self.selected_candidate.capability_version)
            if not any((item.capability_id, item.capability_version) == ref for item in self.candidates):
                raise ValueError("selected candidate must appear in candidates")
        if self.intent is not None and self.selected_candidate is not None:
            if (
                self.intent.capability_id != self.selected_candidate.capability_id
                or self.intent.capability_version != self.selected_candidate.capability_version
                or self.intent.result_id != self.result_id
                or self.intent.catalog_digest != self.catalog_digest
            ):
                raise ValueError("intent must match the selected candidate and result")
        if any(question.result_id != self.result_id or question.revision != self.revision for question in self.questions):
            raise ValueError("questions must reference the containing result and revision")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        blocking_gap = {
            "type": "object",
            "properties": {"blocking": {"const": True}},
            "required": ["blocking"],
        }
        viable_candidate = {
            "type": "object",
            "properties": {"viability": {"const": "viable"}},
            "required": ["viability"],
        }
        conflicting_gap = {
            "type": "object",
            "properties": {
                "kind": {"const": "conflicting"},
                "blocking": {"const": True},
            },
            "required": ["kind", "blocking"],
        }
        branches: list[JsonSchemaValue] = [
            {
                "properties": {
                    "status": {"const": "ready"},
                    "selected_candidate": _nonnull(),
                    "intent": _nonnull(),
                    "questions": {"maxItems": 0},
                    "gaps": {"not": {"contains": blocking_gap}},
                },
                "required": ["status", "selected_candidate", "intent", "questions", "gaps"],
            },
            {
                "properties": {
                    "status": {"const": "needs_clarification"},
                    "selected_candidate": _nonnull(),
                    "intent": {"type": "null"},
                    "questions": {"minItems": 1},
                    "gaps": {"contains": blocking_gap},
                },
                "required": ["status", "selected_candidate", "questions", "gaps"],
            },
            {
                "properties": {
                    "status": {"const": "ambiguous"},
                    "selected_candidate": {"type": "null"},
                    "intent": {"type": "null"},
                    "questions": {"minItems": 1},
                    "candidates": {"contains": viable_candidate, "minContains": 2},
                },
                "required": ["status", "questions", "candidates"],
            },
            {
                "properties": {
                    "status": {"const": "conflicting"},
                    "selected_candidate": _nonnull(),
                    "intent": {"type": "null"},
                    "questions": {"minItems": 1},
                    "gaps": {"contains": conflicting_gap},
                },
                "required": ["status", "selected_candidate", "questions", "gaps"],
            },
            {
                "properties": {
                    "status": {"const": "unsupported"},
                    "selected_candidate": {"type": "null"},
                    "intent": {"type": "null"},
                    "questions": {"maxItems": 0},
                    "candidates": {"not": {"contains": viable_candidate}},
                    "decision_codes": {"minItems": 1},
                },
                "required": [
                    "status", "questions", "candidates", "decision_codes",
                ],
            },
        ]
        schema.setdefault("allOf", []).append({"oneOf": branches})
        return schema


class CompileEnvelope(StrictModel):
    schema_version: Literal["0.2"]
    kind: Literal["result", "error"]
    serialization_profile: Literal["internal", "public", "session"] = "internal"
    result: CompilationResult | None = None
    error: OperationError | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "CompileEnvelope":
        if self.kind == "result" and (self.result is None or self.error is not None):
            raise ValueError("result envelope must contain only result")
        if self.kind == "error" and (self.error is None or self.result is not None):
            raise ValueError("error envelope must contain only error")
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
                        "properties": {
                            "kind": {"const": "result"},
                            "result": _nonnull(),
                            "error": {"type": "null"},
                        },
                        "required": ["kind", "result"],
                    },
                    {
                        "properties": {
                            "kind": {"const": "error"},
                            "result": {"type": "null"},
                            "error": _nonnull(),
                        },
                        "required": ["kind", "error"],
                    },
                ]
            }
        )
        return schema


__all__ = ["CompilationResult", "CompileEnvelope", "Gap"]
