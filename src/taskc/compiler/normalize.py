from __future__ import annotations

import unicodedata
import math
from typing import Any

from taskc.config import CompilerConfig
from taskc.models import InvalidInputError, NormalizedRequest, SourcedValue
from taskc.models.common import JsonValue
from taskc.models.diagnostics import Diagnostic


def _validate_json_value(
    value: Any,
    *,
    depth: int = 0,
    max_collection_length: int = 1_000,
    max_string_length: int = 20_000,
) -> JsonValue:
    if depth > 30:
        raise ValueError("JSON value nesting exceeds the safety limit")
    if isinstance(value, str) and len(value) > max_string_length:
        raise ValueError("JSON string exceeds the configured length limit")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, (int, float)) and not isinstance(value, complex):
        return value
    if isinstance(value, list):
        if len(value) > max_collection_length:
            raise ValueError("JSON array exceeds the configured length limit")
        return [
            _validate_json_value(
                item,
                depth=depth + 1,
                max_collection_length=max_collection_length,
                max_string_length=max_string_length,
            )
            for item in value
        ]
    if isinstance(value, dict):
        if len(value) > max_collection_length:
            raise ValueError("JSON object exceeds the configured field limit")
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return {
            key: _validate_json_value(
                item,
                depth=depth + 1,
                max_collection_length=max_collection_length,
                max_string_length=max_string_length,
            )
            for key, item in value.items()
        }
    raise ValueError(f"unsupported context value type: {type(value).__name__}")


def normalize_request(
    request: str,
    context: dict[str, Any] | None,
    config: CompilerConfig,
) -> NormalizedRequest:
    if not isinstance(request, str):
        raise InvalidInputError(
            "Request must be a string.",
            [Diagnostic(code="E-INPUT-001", severity="error", message="Request must be a string.")],
        )
    normalized = unicodedata.normalize("NFC", request).strip()
    if not normalized:
        raise InvalidInputError(
            "Request must not be blank.",
            [Diagnostic(code="E-INPUT-002", severity="error", message="Request must not be blank.")],
        )
    if len(normalized) > config.max_request_length:
        raise InvalidInputError(
            "Request exceeds the configured length limit.",
            [
                Diagnostic(
                    code="E-INPUT-003",
                    severity="error",
                    message="Request exceeds the configured length limit.",
                )
            ],
        )
    context = context or {}
    if not isinstance(context, dict) or len(context) > config.max_context_fields:
        raise InvalidInputError(
            "Context must be an object within the configured field limit.",
            [
                Diagnostic(
                    code="E-INPUT-004",
                    severity="error",
                    message="Context must be an object within the configured field limit.",
                )
            ],
        )
    try:
        sourced_context = {
            field_name: SourcedValue(
                value=_validate_json_value(
                    value,
                    max_collection_length=config.max_collection_length,
                    max_string_length=config.max_request_length,
                ),
                source="application_context",
                source_ref=f"context:{field_name}",
                confidence=1.0,
            )
            for field_name, value in sorted(context.items())
        }
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(
            "Context contains a non-JSON or unsafe value.",
            [
                Diagnostic(
                    code="E-INPUT-005",
                    severity="error",
                    message="Context contains a non-JSON or unsafe value.",
                )
            ],
        ) from exc
    return NormalizedRequest(raw_text=request, text=normalized, context=sourced_context)


__all__ = ["normalize_request"]
