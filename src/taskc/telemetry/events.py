from __future__ import annotations

from datetime import datetime
from typing import Literal

from taskc.models.common import JsonValue, StrictModel


class TelemetryEvent(StrictModel):
    name: Literal[
        "compilation.started", "contracts.validated", "provider.requested",
        "provider.cache_hit", "provider.completed", "provider.failed", "candidates.generated",
        "fields.extracted", "fields.resolved", "constraints.evaluated", "gaps.computed",
        "questions.planned", "compilation.ready", "compilation.blocked", "compilation.failed",
    ]
    occurred_at: datetime
    request_id: str
    session_id: str | None = None
    duration_ms: float | None = None
    capability_id: str | None = None
    candidate_count: int | None = None
    gap_counts: dict[str, int] = {}
    question_count: int | None = None
    token_usage: int | None = None
    error_code: str | None = None
    metadata: dict[str, JsonValue] = {}
