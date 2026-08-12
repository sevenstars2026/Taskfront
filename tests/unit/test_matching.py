from __future__ import annotations

from taskc.compiler.matching import analyze_candidate
from taskc.config import CompilerConfig
from taskc.models import CandidateDraft, CapabilityContract, ExtractionResult, SourcedValue


def _draft(contract: CapabilityContract) -> CandidateDraft:
    return CandidateDraft(
        capability_id=contract.id,
        capability_version=contract.version,
        score=1.0,
        evidence=[{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
    )


def test_source_priority_prefers_user_over_context(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    extraction = ExtractionResult(
        values={
            "repository": [
                SourcedValue(value="context", source="application_context"),
                SourcedValue(value="user", source="user_request"),
            ],
            "problem_description": [SourcedValue(value="broken", source="user_request")],
        }
    )
    analysis = analyze_candidate(
        contract,
        _draft(contract),
        extraction,
        CompilerConfig(),
    )
    assert analysis.candidate.inputs["repository"].value == "user"
    assert not analysis.gaps


def test_equal_priority_values_create_conflict(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    extraction = ExtractionResult(
        values={
            "repository": [SourcedValue(value="repo", source="user_request")],
            "problem_description": [
                SourcedValue(value="first", source="clarification_answer"),
                SourcedValue(value="second", source="clarification_answer"),
            ],
        }
    )
    analysis = analyze_candidate(
        contract,
        _draft(contract),
        extraction,
        CompilerConfig(),
    )
    assert analysis.gaps[0].kind == "conflicting"
    assert analysis.gaps[0].candidate_values == ["first", "second"]


def test_string_to_integer_is_not_coerced_by_default() -> None:
    contract = CapabilityContract.model_validate(
        {
            "schema_version": "0.2",
            "id": "example.count",
            "version": "1.0.0",
            "description": "Count example.",
            "required_inputs": {"count": {"type": "integer", "description": "Count"}},
            "deliverables": ["result"],
        }
    )
    analysis = analyze_candidate(
        contract,
        _draft(contract),
        ExtractionResult(values={"count": [SourcedValue(value="7", source="user_request")]}),
        CompilerConfig(),
    )
    assert any(gap.code == "GAP-UNVERIFIABLE-TYPE" for gap in analysis.gaps)


def test_model_inference_cannot_satisfy_explicit_field(fix_bug_contract: dict) -> None:
    contract = CapabilityContract.model_validate(fix_bug_contract)
    analysis = analyze_candidate(
        contract,
        _draft(contract),
        ExtractionResult(
            values={
                "repository": [SourcedValue(value="guessed", source="model_inference")],
                "problem_description": [SourcedValue(value="broken", source="model_inference")],
            }
        ),
        CompilerConfig(),
    )
    assert any(gap.code == "GAP-UNVERIFIABLE-SOURCE" for gap in analysis.gaps)
