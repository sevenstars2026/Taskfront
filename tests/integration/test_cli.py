from __future__ import annotations

import json
from copy import deepcopy

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
    first_output = json.loads(capsys.readouterr().out)
    question_id = first_output["result"]["questions"][0]["id"]
    assert main(
        [
            "continue", str(session), "--contracts", str(contract),
            "--answer", f"{question_id}=Checkout returns 500", "--json",
        ]
    ) == 0
    assert '"status": "ready"' in capsys.readouterr().out


def test_cli_schema_export(workspace_tmp, capsys) -> None:
    target = workspace_tmp / "schemas"
    assert main(["schema", "export", "--output", str(target)]) == 0
    assert len(list(target.glob("*.schema.json"))) == 6


def test_cli_migrates_v01_contract_with_machine_report(
    workspace_tmp, fix_bug_contract: dict, capsys
) -> None:
    old = deepcopy(fix_bug_contract)
    old["schema_version"] = "0.1"
    old["required_inputs"]["repository"].pop("source_policy")
    old["required_inputs"]["repository"]["must_be_explicit"] = True
    source = _write_contract(workspace_tmp, old)
    output = workspace_tmp / "migrated"
    assert main([
        "contract", "migrate", str(source),
        "--from", "0.1", "--to", "0.2",
        "--output", str(output), "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["migrations"][0]["valid"] is True
    migrated = json.loads((output / source.name).read_text(encoding="utf-8"))
    assert migrated["schema_version"] == "0.2"
    assert migrated["required_inputs"]["repository"]["source_policy"] == "trusted"
