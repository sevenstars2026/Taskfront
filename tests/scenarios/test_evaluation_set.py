from __future__ import annotations

import json
from pathlib import Path

from taskc import TaskCompiler


def test_public_evaluation_set() -> None:
    root = Path(__file__).parents[2] / "examples" / "evaluation"
    contracts = root / "contracts"
    paths = {
        "fix_bug": [contracts / "fix_bug.yaml"],
        "optimize_build": [contracts / "optimize_build.yaml"],
        "performance": [contracts / "performance.yaml"],
        "multi": [contracts],
    }
    scenarios = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    assert len(scenarios) >= 20
    failures = []
    for scenario in scenarios:
        result = TaskCompiler.from_contract_paths(paths[scenario["catalog"]]).compile(
            scenario["request"], scenario["context"]
        )
        if result.status != scenario["expected_status"]:
            failures.append((scenario["id"], result.status, scenario["expected_status"]))
    assert failures == []

