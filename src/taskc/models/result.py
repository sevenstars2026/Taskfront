from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .clarification import ClarificationQuestion
from .common import JsonValue, StrictModel
from .diagnostics import Diagnostic
from .intent import CandidateIntent, DispatchReadyIntent


class Gap(StrictModel):
    code: str
    kind: Literal[
        "missing", "ambiguous", "conflicting", "unverifiable", "unsupported", "defaultable"
    ]
    field_path: str | None
    message: str
    blocking: bool
    candidate_values: list[JsonValue] = []
    suggested_resolution: Literal[
        "ask_user", "use_context", "apply_default", "choose_candidate", "reject"
    ]


class CompilationResult(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    status: Literal[
        "ready", "needs_clarification", "ambiguous", "conflicting", "unsupported", "invalid_contract"
    ]
    candidates: list[CandidateIntent] = []
    selected_candidate: CandidateIntent | None = None
    gaps: list[Gap] = []
    questions: list[ClarificationQuestion] = []
    intent: DispatchReadyIntent | None = None
    diagnostics: list[Diagnostic] = []

    @model_validator(mode="after")
    def validate_state(self) -> "CompilationResult":
        blocking = any(gap.blocking for gap in self.gaps)
        if self.status == "ready":
            if self.intent is None or self.selected_candidate is None or blocking:
                raise ValueError("ready requires intent and selected_candidate and forbids blocking gaps")
        elif self.intent is not None:
            raise ValueError("only ready results may contain intent")
        if self.status == "ambiguous" and len(self.candidates) < 2:
            raise ValueError("ambiguous requires at least two candidates")
        if self.status == "unsupported" and not any(g.kind == "unsupported" for g in self.gaps):
            raise ValueError("unsupported requires an unsupported gap")
        if self.status == "conflicting" and not any(g.kind == "conflicting" and g.blocking for g in self.gaps):
            raise ValueError("conflicting requires a blocking conflict gap")
        return self

