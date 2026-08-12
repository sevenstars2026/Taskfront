from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from taskc.models.common import JsonValue, StrictModel


class EvaluationScenario(StrictModel):
    schema_version: Literal["0.2"]
    id: str
    request: str
    context: dict[str, JsonValue] = Field(default_factory=dict)
    expected_capabilities: list[str] = Field(default_factory=list)
    expected_status: Literal[
        "ready", "needs_clarification", "ambiguous", "conflicting", "unsupported"
    ]
    required_gap_targets: list[str] = Field(default_factory=list)
    forbid_ready: bool = False
    answer_script: list[dict[str, Any]] = Field(default_factory=list)
    expected_error_category: Literal[
        "invalid_contract", "invalid_input", "provider_failure", "budget_exceeded",
        "stale_session", "internal_error"
    ] | None = None


__all__ = ["EvaluationScenario"]
