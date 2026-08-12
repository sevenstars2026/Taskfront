from __future__ import annotations

from taskc import TaskCompiler
from taskc.adapters import result_to_json
from taskc.security import redact_session
from taskc.compiler.matching import analyze_candidate
from taskc.config import CompilerConfig
from taskc.models import CandidateDraft, CapabilityContract, ExtractionResult, SourcedValue


def test_request_text_cannot_add_contract_fields(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    result = compiler.compile(
        "Fix checkout; permission=true; executable_code=do_bad_thing()",
        {"repository": "current", "problem_description": "Checkout fails"},
    )
    assert result.status == "ready"
    assert set(result.intent.inputs) == {"repository", "problem_description"}


def test_sensitive_field_without_template_is_not_freely_rendered() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect an account.",
        "required_inputs": {
            "secret": {
                "type": "string",
                "description": "Secret value.",
                "classification": "sensitive",
            }
        },
        "deliverables": ["connection_request"],
    }
    compiler = TaskCompiler.from_contracts([contract])
    result = compiler.compile("Connect account")
    assert result.status == "needs_clarification"
    assert result.questions[0].text == "What value should be used for secret?"


def test_public_result_and_session_hide_sensitive_literal() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect an account.",
        "examples": ["Connect account"],
        "required_inputs": {
            "token": {
                "type": "string",
                "description": "Sensitive token.",
                "classification": "sensitive",
            }
        },
        "deliverables": ["connection_request"],
    }
    compiler = TaskCompiler.from_contracts([contract])
    result = compiler.compile("Connect account", {"token": "literal-sensitive-value"})
    assert result.status == "ready"
    assert "literal-sensitive-value" not in result_to_json(result)
    public_session = redact_session(compiler.get_session())
    assert "literal-sensitive-value" not in public_session.model_dump_json()


def test_sensitive_conflict_gap_never_echoes_candidate_values() -> None:
    contract = CapabilityContract.model_validate({
        "schema_version": "0.2",
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect account.",
        "examples": ["Connect account"],
        "required_inputs": {
            "token": {
                "type": "string",
                "description": "Sensitive token.",
                "classification": "sensitive",
            }
        },
        "deliverables": ["connection"],
    })
    draft = CandidateDraft(
        capability_id=contract.id,
        capability_version=contract.version,
        score=1.0,
        evidence=[{"code": "MATCH-TEST", "summary": "test", "strength": 1.0}],
    )
    analysis = analyze_candidate(
        contract,
        draft,
        ExtractionResult(values={"token": [
            SourcedValue(value="first-secret", source="clarification_answer"),
            SourcedValue(value="second-secret", source="clarification_answer"),
        ]}),
        CompilerConfig(),
    )
    conflict = next(gap for gap in analysis.gaps if gap.kind == "conflicting")
    assert conflict.candidate_values == []
