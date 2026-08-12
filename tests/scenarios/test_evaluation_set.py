from __future__ import annotations

import json
from pathlib import Path

from taskc import TaskCompiler
from taskc.contracts import load_catalog
from taskc.evaluation import EvaluationScenario, evaluate_scenarios


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


def test_v02_evaluation_metrics_meet_release_thresholds() -> None:
    root = Path(__file__).parents[2] / "examples" / "evaluation"
    scenarios = [
        EvaluationScenario.model_validate(item)
        for item in json.loads(
            (root / "scenarios-v0.2.json").read_text(encoding="utf-8")
        )
    ]
    assert len(scenarios) >= 60
    catalog = load_catalog([root / "contracts"])
    report = evaluate_scenarios(
        scenarios, lambda: TaskCompiler(catalog=catalog)
    )
    assert report.failures == []
    assert report.critical_gap_recall >= 0.90
    assert report.false_ready_rate < 0.02
    assert report.candidate_recall_at_5 >= 0.95
    assert report.duplicate_question_rate < 0.03
