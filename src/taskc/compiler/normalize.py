from __future__ import annotations

import math
import unicodedata
from typing import Any

from taskc.config import CompilerConfig
from taskc.models import CapabilityRef, Diagnostic, InvalidInputError, NormalizedRequest, SourcedValue
from taskc.models.common import JsonValue


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
        if not all(isinstance(key, str) and len(key) <= 128 for key in value):
            raise ValueError("JSON object keys must be strings of at most 128 characters")
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


def _input_error(code: str, message: str) -> InvalidInputError:
    return InvalidInputError(
        message,
        [Diagnostic(code=code, severity="error", phase="normalization", message=message)],
    )


def normalize_request(
    request: str,
    context: dict[str, Any] | None,
    config: CompilerConfig,
    requested_capability: CapabilityRef | dict[str, str] | None = None,
) -> NormalizedRequest:
    if not isinstance(request, str):
        raise _input_error("E-INPUT-001", "Request must be a string.")
    if len(request) > config.max_request_length:
        raise _input_error("E-INPUT-003", "Request exceeds the configured length limit.")
    normalized = unicodedata.normalize("NFC", request).strip()
    if not normalized:
        raise _input_error("E-INPUT-002", "Request must not be blank.")
    if len(normalized) > config.max_request_length:
        raise _input_error("E-INPUT-003", "Request exceeds the configured length limit.")
    context = context or {}
    if not isinstance(context, dict) or len(context) > config.max_context_fields:
        raise _input_error(
            "E-INPUT-004", "Context must be an object within the configured field limit."
        )
    try:
        sourced_context: dict[str, SourcedValue] = {}
        for field_name, value in sorted(context.items()):
            if not isinstance(field_name, str) or len(field_name) > 128:
                raise ValueError("context keys must be strings of at most 128 characters")
            if isinstance(value, dict) and set(value) == {"value_ref"} and isinstance(value["value_ref"], str):
                sourced_context[field_name] = SourcedValue(
                    value_ref=value["value_ref"],
                    source="application_context",
                    source_ref=f"context:{field_name}",
                    confidence=1.0,
                    classification="secret",
                )
            else:
                sourced_context[field_name] = SourcedValue(
                    value=_validate_json_value(
                        value,
                        max_collection_length=config.max_collection_length,
                        max_string_length=config.max_request_length,
                    ),
                    source="application_context",
                    source_ref=f"context:{field_name}",
                    confidence=1.0,
                )
    except (TypeError, ValueError) as exc:
        raise _input_error("E-INPUT-005", "Context contains a non-JSON or unsafe value.") from exc
    try:
        parsed_capability = (
            requested_capability
            if isinstance(requested_capability, CapabilityRef) or requested_capability is None
            else CapabilityRef.model_validate(requested_capability)
        )
    except ValueError as exc:
        raise _input_error("E-INPUT-CAPABILITY-REF", "Requested capability reference is invalid.") from exc
    return NormalizedRequest(
        raw_text=request,
        text=normalized,
        context=sourced_context,
        requested_capability=parsed_capability,
    )


__all__ = ["normalize_request"]
