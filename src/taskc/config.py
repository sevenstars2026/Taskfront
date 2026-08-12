from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator

from .models.common import StrictModel


class ProviderBudgetConfig(StrictModel):
    max_logical_model_calls: int = Field(default=2, ge=0, le=10)
    max_network_attempts: int = Field(default=3, ge=0, le=20)
    max_provider_time_seconds: float = Field(default=60.0, gt=0, le=600)
    max_response_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)


class QuestionWeights(StrictModel):
    candidate_elimination: float = Field(default=0.40, ge=0)
    blocking_gap: float = Field(default=0.35, ge=0)
    dependency: float = Field(default=0.15, ge=0)
    user_cost: float = Field(default=0.07, ge=0)
    sensitivity: float = Field(default=0.03, ge=0)

    @model_validator(mode="after")
    def require_benefit_weight(self) -> "QuestionWeights":
        if self.candidate_elimination + self.blocking_gap + self.dependency == 0:
            raise ValueError("at least one benefit weight must be positive")
        return self


class CompilerConfig(StrictModel):
    max_candidates: int = Field(default=5, ge=1, le=50)
    max_questions_per_round: int = Field(default=1, ge=1, le=20)
    max_request_length: int = Field(default=20_000, ge=1, le=1_000_000)
    max_context_fields: int = Field(default=200, ge=0, le=10_000)
    max_collection_length: int = Field(default=1_000, ge=1, le=100_000)
    min_static_match_score: float = Field(default=0.05, ge=0, le=1)
    allow_string_to_number: bool = False
    allow_prerelease: bool = False
    auto_select_candidate: bool = False
    auto_select_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    auto_select_margin: float = Field(default=0.20, ge=0.0, le=1.0)
    allow_nondeterministic_extensions: bool = False
    telemetry_strict: bool = False
    provider_budget: ProviderBudgetConfig = ProviderBudgetConfig()
    question_weights: QuestionWeights = QuestionWeights()

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["CompilerConfig", "ProviderBudgetConfig", "QuestionWeights"]
