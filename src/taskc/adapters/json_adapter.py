from __future__ import annotations

import json

from taskc.models import CompilationResult


def result_to_json(result: CompilationResult, *, indent: int | None = 2) -> str:
    return json.dumps(
        result.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


__all__ = ["result_to_json"]

