from __future__ import annotations

from pydantic import Field

from .models.common import StrictModel


class QuestionWeights(StrictModel):
    candidate_elimination: float = 0.40
    blocking_gap: float = 0.35
    dependency: float = 0.15
    user_cost: float = 0.07
    sensitivity: float = 0.03


class CompilerConfig(StrictModel):
    max_candidates: int = Field(default=5, ge=1, le=50)
    max_questions_per_round: int = Field(default=1, ge=1, le=20)
    max_request_length: int = Field(default=20_000, ge=1, le=1_000_000)
    max_context_fields: int = Field(default=200, ge=0, le=10_000)
    max_collection_length: int = Field(default=1_000, ge=1, le=100_000)
    allow_string_to_number: bool = False
    auto_select_candidate: bool = False
    auto_select_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    auto_select_margin: float = Field(default=0.20, ge=0.0, le=1.0)
    explicit_sources: frozenset[str] = frozenset(
        {"user", "application_context", "clarification_answer", "explicit_default"}
    )
    provider_retries: int = Field(default=1, ge=0, le=5)
    question_weights: QuestionWeights = QuestionWeights()

