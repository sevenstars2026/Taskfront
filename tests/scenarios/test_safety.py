from __future__ import annotations

from taskc import TaskCompiler


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
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect an account.",
        "required_inputs": {
            "secret": {
                "type": "string",
                "description": "Secret value.",
                "sensitive": True,
            }
        },
        "deliverables": ["connection_request"],
    }
    compiler = TaskCompiler.from_contracts([contract])
    result = compiler.compile("Connect account")
    assert result.status == "needs_clarification"
    assert result.questions == []
    assert result.diagnostics[0].code == "E-INPUT-SENSITIVE-TEMPLATE"

