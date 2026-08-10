from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from taskc.models import CompilationResult, DispatchReadyIntent, Gap, SourcedValue


def test_ready_requires_intent_and_selected_candidate() -> None:
    with pytest.raises(ValidationError):
        CompilationResult(status="ready")


def test_non_ready_rejects_intent() -> None:
    intent = DispatchReadyIntent(
        intent_id="fixed",
        capability_id="repository.fix_bug",
        capability_version="1.0.0",
        inputs={},
        deliverables=["patch"],
        compiled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(ValidationError):
        CompilationResult(status="needs_clarification", intent=intent)


def test_unsupported_requires_unsupported_gap() -> None:
    with pytest.raises(ValidationError):
        CompilationResult(status="unsupported")


def test_unknown_result_fields_are_rejected() -> None:
    gap = Gap(
        code="GAP-UNSUPPORTED",
        kind="unsupported",
        field_path=None,
        message="No match",
        blocking=True,
        suggested_resolution="reject",
    )
    with pytest.raises(ValidationError):
        CompilationResult.model_validate({"status": "unsupported", "gaps": [gap], "permission": True})


def test_non_finite_json_numbers_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourcedValue(value=float("nan"), source="user")
