from __future__ import annotations

import json

from taskc.cli import main


def _write_contract(tmp_path, contract: dict):
    path = tmp_path / "capability.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def test_cli_validate_and_compile(workspace_tmp, fix_bug_contract: dict, capsys) -> None:
    contract = _write_contract(workspace_tmp, fix_bug_contract)
    context = workspace_tmp / "context.json"
    context.write_text(
        json.dumps({"repository": "current", "problem_description": "Checkout fails"}),
        encoding="utf-8",
    )
    assert main(["contract", "validate", str(contract)]) == 0
    assert main(
        [
            "compile", "Fix checkout", "--contracts", str(contract),
            "--context", str(context), "--json",
        ]
    ) == 0
    assert '"status": "ready"' in capsys.readouterr().out


def test_cli_session_round_trip(workspace_tmp, fix_bug_contract: dict, capsys) -> None:
    contract = _write_contract(workspace_tmp, fix_bug_contract)
    context = workspace_tmp / "context.json"
    context.write_text(json.dumps({"repository": "current"}), encoding="utf-8")
    session = workspace_tmp / "session.json"
    assert main(
        [
            "compile", "Fix checkout", "--contracts", str(contract),
            "--context", str(context), "--session-out", str(session), "--json",
        ]
    ) == 2
    assert session.exists()
    assert main(
        [
            "continue", str(session), "--contracts", str(contract),
            "--answer", "q-problem-description=Checkout returns 500", "--json",
        ]
    ) == 0
    assert '"status": "ready"' in capsys.readouterr().out


def test_cli_schema_export(workspace_tmp, capsys) -> None:
    target = workspace_tmp / "schemas"
    assert main(["schema", "export", "--output", str(target)]) == 0
    assert len(list(target.glob("*.schema.json"))) == 3
