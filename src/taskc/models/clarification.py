from __future__ import annotations

from datetime import datetime
from typing import Literal

from .common import JsonValue, StrictModel
from .intent import NormalizedRequest


class ClarificationQuestion(StrictModel):
    id: str
    text: str
    targets: list[str]
    answer_type: Literal["text", "single_choice", "multi_choice", "boolean", "number"]
    choices: list[JsonValue] = []
    priority: float
    reason: str


class ClarificationAnswer(StrictModel):
    question_id: str
    value: JsonValue


class CompilationSession(StrictModel):
    session_id: str
    request: NormalizedRequest
    catalog_digest: str
    answers: list[ClarificationAnswer]
    last_result: "CompilationResult"
    created_at: datetime
    updated_at: datetime


from .result import CompilationResult  # noqa: E402

CompilationSession.model_rebuild()

