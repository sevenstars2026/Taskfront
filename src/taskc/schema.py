from __future__ import annotations

import json
from pathlib import Path

from taskc.models import CapabilityContract, CompilationResult, DispatchReadyIntent


SCHEMAS = {
    "capability-contract-v0.1.schema.json": CapabilityContract,
    "compilation-result-v0.1.schema.json": CompilationResult,
    "dispatch-ready-intent-v0.1.schema.json": DispatchReadyIntent,
}


def export_schemas(directory: str | Path) -> list[Path]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMAS.items():
        path = target / filename
        payload = model.model_json_schema(mode="validation")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


__all__ = ["SCHEMAS", "export_schemas"]

