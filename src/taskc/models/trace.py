from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import JsonValue, StrictModel


class TraceStep(StrictModel):
    phase: str
    reason_code: str
    capability_ref: str | None = None
    field_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ExplainTrace(StrictModel):
    trace_id: str
    created_at: datetime
    reproducible: bool = True
    steps: list[TraceStep] = Field(default_factory=list)


__all__ = ["ExplainTrace", "TraceStep"]
