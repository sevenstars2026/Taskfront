from __future__ import annotations

from taskc import TaskCompiler
from taskc.evaluation import EvaluationScenario, evaluate_scenarios


def test_evaluation_runner_executes_answer_script(fix_bug_contract: dict) -> None:
    scenario = EvaluationScenario(
        schema_version="0.2",
        id="SCRIPT-1",
        request="Fix checkout",
        context={"repository": "current"},
        expected_capabilities=["repository.fix_bug@1.0.0"],
        expected_status="ready",
        required_gap_targets=["inputs.problem_description"],
        answer_script=[{
            "target": "inputs.problem_description",
            "value": "Checkout returns 500",
        }],
    )
    report = evaluate_scenarios(
        [scenario], lambda: TaskCompiler.from_contracts([fix_bug_contract])
    )
    assert report.failures == []
    assert report.average_clarification_turns == 1.0
    assert report.critical_gap_recall == 1.0
    assert report.candidate_recall_at_5 == 1.0
