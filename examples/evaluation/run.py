from __future__ import annotations

import json
from pathlib import Path

from taskc import TaskCompiler

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE / "contracts"
PATHS = {
    "fix_bug": [CONTRACTS / "fix_bug.yaml"],
    "optimize_build": [CONTRACTS / "optimize_build.yaml"],
    "performance": [CONTRACTS / "performance.yaml"],
    "multi": [CONTRACTS],
}

scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
passed = 0
for scenario in scenarios:
    compiler = TaskCompiler.from_contract_paths(PATHS[scenario["catalog"]])
    result = compiler.compile(scenario["request"], scenario["context"])
    ok = result.status == scenario["expected_status"]
    passed += int(ok)
    print(f"{'PASS' if ok else 'FAIL'} {scenario['id']} {result.status}")
print(f"{passed}/{len(scenarios)} scenarios passed")

