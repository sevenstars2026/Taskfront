from __future__ import annotations

import json
from pathlib import Path

from taskc import TaskCompiler

HERE = Path(__file__).resolve().parent

compiler = TaskCompiler.from_contract_paths([HERE / "capability.yaml"])
context = json.loads((HERE / "context.json").read_text(encoding="utf-8"))
result = compiler.compile("Fix the checkout failure", context=context)
print(result.model_dump_json(indent=2))

