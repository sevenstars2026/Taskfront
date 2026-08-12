from __future__ import annotations

import pytest

from taskc import TaskCompiler
from taskc.adapters import JsonFileSessionStore
from taskc.models import InvalidInputError, StaleSessionError


def test_json_session_store_round_trip_and_cas(
    workspace_tmp, fix_bug_contract: dict
) -> None:
    store = JsonFileSessionStore(workspace_tmp / "sessions")
    compiler = TaskCompiler.from_contracts([fix_bug_contract], session_store=store)
    compiler.compile("Fix checkout", {"repository": "current"})
    session = store.get(compiler.last_session_id)
    assert session.revision == 0
    session.revision = 1
    store.put(session, expected_revision=0)
    session.revision = 2
    with pytest.raises(StaleSessionError):
        store.put(session, expected_revision=0)


def test_json_session_store_refuses_sensitive_literals(workspace_tmp) -> None:
    contract = {
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
    }
    compiler = TaskCompiler.from_contracts(
        [contract], session_store=JsonFileSessionStore(workspace_tmp / "sessions")
    )
    with pytest.raises(InvalidInputError):
        compiler.compile("Connect account", {"token": "literal"})
