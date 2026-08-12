from __future__ import annotations

from typing import Protocol

from taskc.models import ClarificationQuestion, Gap
from taskc.models.common import JsonValue


class ValueValidator(Protocol):
    def validate(self, value: JsonValue, *, options: dict[str, JsonValue]) -> bool: ...


class ConstraintEvaluator(Protocol):
    def evaluate(self, values: dict[str, JsonValue], *, options: dict[str, JsonValue]) -> bool | None: ...


class DefaultProvider(Protocol):
    def provide(self, *, field_name: str, context: dict[str, JsonValue]) -> JsonValue: ...


class QuestionRanker(Protocol):
    def rank(self, questions: list[ClarificationQuestion], gaps: list[Gap]) -> list[ClarificationQuestion]: ...


__all__ = ["ConstraintEvaluator", "DefaultProvider", "QuestionRanker", "ValueValidator"]
