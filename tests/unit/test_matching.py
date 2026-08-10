from __future__ import annotations

from taskc.compiler.matching import analyze_candidate
from taskc.config import CompilerConfig
from taskc.models import CandidateDraft, CapabilityContract, ExtractionResult, SourcedValue


def test_source_priority_prefers_user_over_context(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    extraction = ExtractionResult(
        values={
            "repository": [
                SourcedValue(value="context", source="application_context"),
                SourcedValue(value="user", source="user"),
            ],
            "problem_description": [SourcedValue(value="broken", source="user")],
        }
    )
    analysis = analyze_candidate(
        contract,
        CandidateDraft(
            capability_id=contract.id,
            capability_version=contract.version,
            score=1,
            match_reasons=["test"],
        ),
        extraction,
        CompilerConfig(),
    )
    assert analysis.candidate.inputs["repository"].value == "user"
    assert not analysis.gaps


def test_equal_priority_values_create_conflict(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    extraction = ExtractionResult(
        values={
            "repository": [SourcedValue(value="repo", source="user")],
            "problem_description": [
                SourcedValue(value="first", source="clarification_answer"),
                SourcedValue(value="second", source="clarification_answer"),
            ],
        }
    )
    analysis = analyze_candidate(
        contract,
        CandidateDraft(capability_id=contract.id, score=1, match_reasons=["test"]),
        extraction,
        CompilerConfig(),
    )
    assert analysis.gaps[0].kind == "conflicting"
    assert analysis.gaps[0].candidate_values == ["first", "second"]


def test_string_to_integer_is_not_coerced_by_default() -> None:
    contract = CapabilityContract.model_validate(
        {
            "id": "example.count",
            "version": "1.0.0",
            "description": "Count example.",
            "required_inputs": {"count": {"type": "integer", "description": "Count"}},
            "deliverables": ["result"],
        }
    )
    analysis = analyze_candidate(
        contract,
        CandidateDraft(capability_id=contract.id, score=1, match_reasons=["test"]),
        ExtractionResult(values={"count": [SourcedValue(value="7", source="user")]}),
        CompilerConfig(),
    )
    assert any(gap.code == "GAP-UNVERIFIABLE-TYPE" for gap in analysis.gaps)


def test_model_inference_cannot_satisfy_explicit_field(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    analysis = analyze_candidate(
        contract,
        CandidateDraft(capability_id=contract.id, score=1, match_reasons=["test"]),
        ExtractionResult(
            values={
                "repository": [SourcedValue(value="guessed", source="model_inference")],
                "problem_description": [SourcedValue(value="broken", source="model_inference")],
            }
        ),
        CompilerConfig(),
    )
    assert any(gap.code == "GAP-UNVERIFIABLE-EXPLICIT" for gap in analysis.gaps)

