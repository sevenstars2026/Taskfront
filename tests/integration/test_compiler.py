from __future__ import annotations

import pytest

from taskc import CompilerConfig, TaskCompiler
from taskc.models import InvalidInputError
from taskc.providers import ModelInterpretationProvider


class _InferenceOnlyModel:
    provider_id = "inference-only"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {
            "candidates": [{
                "capability_id": "repository.fix_bug",
                "capability_version": "1.0.0",
                "score": 1.0,
                "evidence": [{"code": "MATCH-MODEL", "summary": "Model match", "strength": 1.0}],
                "inputs": {
                    "repository": {"value": "guessed", "source": "model_inference"},
                    "problem_description": {"value": "guessed failure", "source": "model_inference"},
                },
            }]
        }


class _CalibratedProvider:
    provider_id = "calibrated-test"
    config_version = "test"
    score_semantics = "calibrated"

    async def interpret(self, request, catalog, *, max_candidates, budget):
        from taskc.models import CandidateDraft, MatchEvidence

        return [
            CandidateDraft(
                capability_id="repository.optimize_build",
                capability_version="1.0.0",
                score=0.99,
                evidence=[MatchEvidence(code="MATCH-TEST", summary="Strong", strength=0.99)],
            ),
            CandidateDraft(
                capability_id="repository.fix_bug",
                capability_version="1.0.0",
                score=0.1,
                evidence=[MatchEvidence(code="MATCH-TEST", summary="Weak", strength=0.1)],
            ),
        ]


def test_complete_single_capability_is_ready(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    result = compiler.compile(
        "Fix checkout",
        {"repository": "current", "problem_description": "Checkout returns 500"},
    )
    assert result.status == "ready"
    assert result.intent is not None
    assert result.intent.capability_id == "repository.fix_bug"


def test_missing_field_generates_minimal_question(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    result = compiler.compile("Fix checkout", {"repository": "current"})
    assert result.status == "needs_clarification"
    assert result.questions[0].targets == ["inputs.problem_description"]


def test_continue_can_reach_ready(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    initial = compiler.compile("Fix checkout", {"repository": "current"})
    question = initial.questions[0]
    result = compiler.continue_(
        compiler.last_session_id,
        0,
        [{
            "question_id": question.id,
            "result_id": question.result_id,
            "revision": question.revision,
            "value": "Checkout returns 500",
        }],
    )
    assert result.status == "ready"


def test_two_answers_to_same_question_are_invalid_input(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    initial = compiler.compile("Fix checkout", {"repository": "current"})
    question = initial.questions[0]
    answer = {
        "question_id": question.id,
        "result_id": question.result_id,
        "revision": question.revision,
        "value": "First",
    }
    with pytest.raises(InvalidInputError):
        compiler.continue_(compiler.last_session_id, 0, [answer, {**answer, "value": "Second"}])


def test_conditional_requirement_is_computed(build_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([build_contract])
    result = compiler.compile(
        "Make build faster",
        {"repository": "current", "optimization_goal": "ci_build"},
    )
    assert result.status == "needs_clarification"
    assert any(gap.code == "GAP-MISSING-CONDITIONAL" for gap in result.gaps)
    assert result.questions[0].targets == ["inputs.ci_provider"]


def test_contract_default_is_applied(build_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([build_contract])
    result = compiler.compile(
        "Make build faster",
        {"repository": "current", "optimization_goal": "local_build"},
    )
    assert result.status == "ready"
    assert result.intent.inputs["measure"].source == "contract_default"


def test_multiple_candidates_are_not_silently_selected(
    fix_bug_contract: dict, build_contract: dict
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract, build_contract])
    result = compiler.compile("Make build faster and fix checkout", {"repository": "current"})
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2
    assert result.questions[0].targets == ["capability"]


def test_capability_clarification_selects_candidate(
    fix_bug_contract: dict, build_contract: dict
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract, build_contract])
    initial = compiler.compile("Make build faster and fix checkout", {"repository": "current"})
    question = initial.questions[0]
    result = compiler.continue_(
        compiler.last_session_id,
        0,
        [{
            "question_id": question.id,
            "result_id": question.result_id,
            "revision": question.revision,
            "value": "repository.optimize_build@1.0.0",
        }],
    )
    assert result.status == "needs_clarification"
    assert result.selected_candidate.capability_id == "repository.optimize_build"


def test_unmatched_request_is_unsupported(fix_bug_contract: dict, build_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract, build_contract])
    result = compiler.compile("Translate a legal document into French")
    assert result.status == "unsupported"


def test_auto_selection_requires_explicit_configuration(
    fix_bug_contract: dict, build_contract: dict
) -> None:
    compiler = TaskCompiler.from_contracts(
        [fix_bug_contract, build_contract],
        config=CompilerConfig(
            auto_select_candidate=True,
            auto_select_threshold=0.01,
            auto_select_margin=0,
        ),
        interpretation_provider=_CalibratedProvider(),
    )
    result = compiler.compile(
        "Make the build faster",
        {"repository": "current", "optimization_goal": "local_build"},
    )
    assert result.status == "ready"
    assert result.intent.capability_id == "repository.optimize_build"


def test_model_guess_does_not_satisfy_explicit_input(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts(
        [fix_bug_contract],
        interpretation_provider=ModelInterpretationProvider(_InferenceOnlyModel()),
    )
    result = compiler.compile("Fix checkout")
    assert result.status == "needs_clarification"
    assert any(gap.code == "GAP-UNVERIFIABLE-SOURCE" for gap in result.gaps)
