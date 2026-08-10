from __future__ import annotations

from taskc import CompilerConfig, TaskCompiler
from taskc.providers import ModelExtractionProvider


class _InferenceOnlyModel:
    provider_id = "inference-only"
    config_version = "test"

    async def generate_json(self, **kwargs):
        return {
            "inputs": {
                "repository": {"value": "guessed", "source": "model_inference"},
                "problem_description": {"value": "guessed failure", "source": "model_inference"},
            },
            "unresolved_terms": [],
        }


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
    assert [question.id for question in result.questions] == ["q-problem-description"]


def test_continue_can_reach_ready(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    compiler.compile("Fix checkout", {"repository": "current"})
    result = compiler.continue_(
        compiler.last_session_id,
        [{"question_id": "q-problem-description", "value": "Checkout returns 500"}],
    )
    assert result.status == "ready"


def test_two_answers_to_same_question_create_conflict(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    compiler.compile("Fix checkout", {"repository": "current"})
    result = compiler.continue_(
        compiler.last_session_id,
        [
            {"question_id": "q-problem-description", "value": "First"},
            {"question_id": "q-problem-description", "value": "Second"},
        ],
    )
    assert result.status == "conflicting"
    assert result.gaps[0].kind == "conflicting"

    resolved = compiler.continue_(
        compiler.last_session_id,
        [{"question_id": "q-problem-description", "value": "First"}],
    )
    assert resolved.status == "ready"
    assert resolved.intent.inputs["problem_description"].value == "First"
    assert len(compiler.get_session().answers) == 3


def test_conditional_requirement_is_computed(build_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([build_contract])
    result = compiler.compile(
        "Make build faster",
        {"repository": "current", "optimization_goal": "ci_build"},
    )
    assert result.status == "needs_clarification"
    assert any(gap.code == "GAP-MISSING-CONDITIONAL" for gap in result.gaps)
    assert result.questions[0].id == "q-ci-provider"


def test_explicit_default_is_applied(build_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([build_contract])
    result = compiler.compile(
        "Make build faster",
        {"repository": "current", "optimization_goal": "local_build"},
    )
    assert result.status == "ready"
    assert result.intent.inputs["measure"].source == "explicit_default"


def test_multiple_candidates_are_not_silently_selected(
    fix_bug_contract: dict, build_contract: dict
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract, build_contract])
    result = compiler.compile("Fix and improve repository build", {"repository": "current"})
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2
    assert result.questions[0].id == "q-capability"


def test_capability_clarification_selects_candidate(
    fix_bug_contract: dict, build_contract: dict
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract, build_contract])
    compiler.compile("Fix and improve repository build", {"repository": "current"})
    result = compiler.continue_(
        compiler.last_session_id,
        [{"question_id": "q-capability", "value": "repository.optimize_build"}],
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
        extraction_provider=ModelExtractionProvider(_InferenceOnlyModel()),
    )
    result = compiler.compile("Fix checkout")
    assert result.status == "needs_clarification"
    assert any(gap.code == "GAP-UNVERIFIABLE-EXPLICIT" for gap in result.gaps)
