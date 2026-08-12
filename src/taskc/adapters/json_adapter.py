from __future__ import annotations

import json
from typing import Literal

from taskc.models import CompilationResult, CompileEnvelope
from taskc.security import redact_result


def result_to_json(
    result: CompilationResult,
    *,
    indent: int | None = 2,
    profile: Literal["internal", "public"] = "public",
) -> str:
    projected = redact_result(result) if profile == "public" else result
    return json.dumps(
        projected.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


def envelope_to_json(envelope: CompileEnvelope, *, indent: int | None = 2) -> str:
    projected = envelope
    if envelope.result is not None and envelope.serialization_profile == "public":
        projected = envelope.model_copy(update={"result": redact_result(envelope.result)})
    return json.dumps(
        projected.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


__all__ = ["envelope_to_json", "result_to_json"]
