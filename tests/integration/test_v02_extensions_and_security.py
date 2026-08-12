from __future__ import annotations

import pytest

from taskc import CompilerConfig, TaskCompiler
from taskc.adapters import result_to_json
from taskc.extensions import ExtensionDescriptor, ExtensionRegistry
from taskc.models import ContractValidationError


def test_single_irrelevant_capability_with_complete_context_is_unsupported(
    fix_bug_contract: dict,
) -> None:
    compiler = TaskCompiler.from_contracts([fix_bug_contract])
    result = compiler.compile(
        "Translate this legal document into French",
        {"repository": "current", "problem_description": "Checkout fails"},
    )
    assert result.status == "unsupported"
    assert result.intent is None


def test_blocking_constraint_produces_a_resolvable_question() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "example.measure",
        "version": "1.0.0",
        "description": "Measure an example target.",
        "examples": ["Measure this target"],
        "required_inputs": {
            "target": {"type": "string", "description": "Target."},
            "measure": {"type": "boolean", "description": "Enable measurement."},
        },
        "constraints": [{
            "code": "GAP-CONSTRAINT-MEASURE",
            "assert": {"field": "measure", "equals": True},
            "targets": ["measure"],
            "message": "Measurement must be enabled.",
            "blocking": True,
        }],
        "deliverables": ["report"],
    }
    compiler = TaskCompiler.from_contracts([contract])
    initial = compiler.compile("Measure this target", {"target": "api", "measure": False})
    assert initial.status == "needs_clarification"
    assert initial.questions[0].targets == ["inputs.measure"]
    question = initial.questions[0]
    result = compiler.continue_(compiler.last_session_id, 0, [{
        "question_id": question.id,
        "result_id": question.result_id,
        "revision": question.revision,
        "value": True,
    }])
    assert result.status == "ready"


class _FalseConstraint:
    def evaluate(self, values, *, options):
        return False


class _TrueConstraint:
    def evaluate(self, values, *, options):
        return True


class _ReverseRanker:
    def rank(self, questions, gaps):
        return list(reversed(questions))


def test_registered_constraint_evaluator_and_question_ranker_are_used() -> None:
    registry = ExtensionRegistry()
    registry.register_constraint(
        ExtensionDescriptor(name="test.constraint", version="1"), _FalseConstraint()
    )
    registry.register_question_ranker(
        ExtensionDescriptor(name="test.ranker", version="1"), _ReverseRanker()
    )
    contract = {
        "schema_version": "0.2",
        "id": "example.extend",
        "version": "1.0.0",
        "description": "Extend an example.",
        "examples": ["Extend example"],
        "required_inputs": {
            "a": {"type": "string", "description": "A."},
            "b": {"type": "string", "description": "B."},
        },
        "constraints": [{
            "code": "GAP-CONSTRAINT-CUSTOM",
            "evaluator": "test.constraint",
            "targets": ["a"],
            "message": "Custom constraint failed.",
        }],
        "question_ranker": "test.ranker",
        "deliverables": ["result"],
    }
    result = TaskCompiler.from_contracts(
        [contract], extension_registry=registry
    ).compile("Extend example")
    assert result.status == "needs_clarification"
    assert result.questions[0].targets == ["inputs.b"]


def test_unregistered_and_nondeterministic_extensions_fail_closed() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "example.extend",
        "version": "1.0.0",
        "description": "Extend an example.",
        "examples": ["Extend example"],
        "required_inputs": {"a": {"type": "string", "description": "A."}},
        "question_ranker": "test.ranker",
        "deliverables": ["result"],
    }
    with pytest.raises(ContractValidationError):
        TaskCompiler.from_contracts([contract])
    registry = ExtensionRegistry()
    registry.register_question_ranker(
        ExtensionDescriptor(name="test.ranker", version="1", deterministic=False),
        _ReverseRanker(),
    )
    with pytest.raises(ContractValidationError):
        TaskCompiler.from_contracts([contract], extension_registry=registry)
    result = TaskCompiler.from_contracts(
        [contract],
        extension_registry=registry,
        config=CompilerConfig(allow_nondeterministic_extensions=True),
    ).compile("Extend example")
    assert result.reproducible is False
    assert result.trace.reproducible is False


def test_nondeterministic_constraint_requires_opt_in_and_marks_ready_result() -> None:
    registry = ExtensionRegistry()
    registry.register_constraint(
        ExtensionDescriptor(
            name="test.nondeterministic",
            version="1",
            deterministic=False,
        ),
        _TrueConstraint(),
    )
    contract = {
        "schema_version": "0.2",
        "id": "example.nondeterministic",
        "version": "1.0.0",
        "description": "Check a nondeterministic example.",
        "examples": ["Check nondeterministic example"],
        "required_inputs": {"value": {"type": "string", "description": "Value."}},
        "constraints": [{
            "code": "GAP-CONSTRAINT-NONDETERMINISTIC",
            "evaluator": "test.nondeterministic",
            "targets": ["value"],
            "message": "External constraint failed.",
        }],
        "deliverables": ["result"],
    }
    with pytest.raises(ContractValidationError):
        TaskCompiler.from_contracts([contract], extension_registry=registry)
    result = TaskCompiler.from_contracts(
        [contract],
        extension_registry=registry,
        config=CompilerConfig(allow_nondeterministic_extensions=True),
    ).compile("Check nondeterministic example", {"value": "ok"})
    assert result.status == "ready"
    assert result.reproducible is False
    assert result.trace.reproducible is False


class _SecretResolver:
    def validate_ref(self, value_ref: str, *, expected_type: str) -> bool:
        return value_ref.startswith("vault://") and expected_type == "string"


def test_secret_reference_is_required_and_public_output_hides_it() -> None:
    contract = {
        "schema_version": "0.2",
        "id": "account.connect",
        "version": "1.0.0",
        "description": "Connect an account.",
        "examples": ["Connect account"],
        "required_inputs": {
            "token": {
                "type": "string",
                "description": "Token reference.",
                "classification": "secret",
            }
        },
        "deliverables": ["connection"],
    }
    compiler = TaskCompiler.from_contracts([contract], secret_resolver=_SecretResolver())
    result = compiler.compile("Connect account", {"token": {"value_ref": "vault://token"}})
    assert result.status == "ready"
    assert "vault://token" not in result_to_json(result)
    literal = compiler.compile("Connect account", {"token": "literal"})
    assert literal.status == "needs_clarification"
    assert any(gap.code == "GAP-UNVERIFIABLE-SECRET-LITERAL" for gap in literal.gaps)
