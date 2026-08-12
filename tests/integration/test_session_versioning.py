from __future__ import annotations

import pytest

from taskc import TaskCompiler
from taskc.models import StaleSessionError
from taskc.session import InMemorySessionStore


def _answer(result, value):
    question = result.questions[0]
    return {
        "question_id": question.id,
        "result_id": question.result_id,
        "revision": question.revision,
        "value": value,
    }


def test_optional_addition_recompiles_and_emits_diagnostic(fix_bug_contract: dict) -> None:
    store = InMemorySessionStore()
    first = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    initial = first.compile("Fix checkout", {"repository": "current"})

    changed = {**fix_bug_contract, "optional_inputs": {
        **fix_bug_contract["optional_inputs"],
        "issue_url": {"type": "string", "description": "Issue URL."},
    }}
    second = TaskCompiler.from_contracts([changed], session_store=store)
    result = second.continue_(
        first.last_session_id,
        0,
        [_answer(initial, "Checkout fails")],
    )

    assert result.status == "ready"
    assert result.intent.capability_version == "1.0.0"
    assert result.diagnostics[-1].code == "W-SESSION-CATALOG-COMPATIBLE"


def test_exact_version_removal_makes_session_stale(fix_bug_contract: dict) -> None:
    store = InMemorySessionStore()
    first = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    initial = first.compile("Fix checkout", {"repository": "current"})
    changed = {**fix_bug_contract, "version": "1.1.0"}
    second = TaskCompiler.from_contracts([changed], session_store=store)
    with pytest.raises(StaleSessionError):
        second.continue_(first.last_session_id, 0, [_answer(initial, "Checkout fails")])


def test_host_can_explicitly_restart_and_revalidate_old_answers(
    fix_bug_contract: dict,
) -> None:
    store = InMemorySessionStore()
    first = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    initial = first.compile("Fix checkout", {"repository": "current"})
    first.continue_(
        first.last_session_id,
        0,
        [_answer(initial, "Checkout fails")],
    )
    old_session_id = first.last_session_id
    changed = {**fix_bug_contract, "version": "1.1.0"}
    second = TaskCompiler.from_contracts([changed], session_store=store)
    result = second.restart_from_session(old_session_id)
    assert result.status == "ready"
    assert result.intent.capability_version == "1.1.0"
    assert second.last_session_id != old_session_id
