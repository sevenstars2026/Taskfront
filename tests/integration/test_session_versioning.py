from __future__ import annotations

from taskc import TaskCompiler
from taskc.session import InMemorySessionStore


def test_catalog_change_recompiles_and_emits_diagnostic(fix_bug_contract: dict) -> None:
    store = InMemorySessionStore()
    first = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    first.compile("Fix checkout", {"repository": "current"})

    changed = {**fix_bug_contract, "version": "1.1.0"}
    second = TaskCompiler.from_contracts([changed], session_store=store)
    result = second.continue_(
        first.last_session_id,
        [{"question_id": "q-problem-description", "value": "Checkout fails"}],
    )

    assert result.status == "ready"
    assert result.intent.capability_version == "1.1.0"
    assert result.diagnostics[-1].code == "E-CONTRACT-CATALOG-CHANGED"

