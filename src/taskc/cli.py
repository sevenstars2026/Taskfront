from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from taskc import TaskCompiler
from taskc.adapters import result_to_json
from taskc.contracts import load_catalog
from taskc.models import (
    CompilationResult,
    CompilationSession,
    ContractValidationError,
    InvalidInputError,
    TaskCompilerError,
)
from taskc.renderers import render_diagnostic, render_result
from taskc.schema import export_schemas
from taskc.session import InMemorySessionStore

_STATUS_EXIT = {
    "ready": 0,
    "needs_clarification": 2,
    "ambiguous": 2,
    "conflicting": 3,
    "unsupported": 4,
    "invalid_contract": 5,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskc", description="Agent Task Compiler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract", help="Capability contract commands")
    contract_sub = contract.add_subparsers(dest="contract_command", required=True)
    validate = contract_sub.add_parser("validate", help="Validate capability contracts")
    validate.add_argument("paths", nargs="+", help="YAML/JSON file or directory")

    compile_parser = subparsers.add_parser("compile", help="Compile a natural-language request")
    compile_parser.add_argument("request")
    compile_parser.add_argument("--contracts", nargs="+", required=True)
    compile_parser.add_argument("--context", help="JSON context file")
    compile_parser.add_argument("--session-out", help="Write the compilation session to this JSON file")
    compile_parser.add_argument("--json", action="store_true", dest="as_json")

    continue_parser = subparsers.add_parser("continue", help="Continue a saved compilation session")
    continue_parser.add_argument("session", help="Saved session JSON file")
    continue_parser.add_argument("--contracts", nargs="+", required=True)
    continue_parser.add_argument(
        "--answer", action="append", required=True, metavar="QUESTION_ID=VALUE"
    )
    continue_parser.add_argument("--json", action="store_true", dest="as_json")

    schema = subparsers.add_parser("schema", help="JSON Schema commands")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_sub.add_parser("export", help="Export public JSON Schemas")
    export.add_argument("--output", default="schemas")
    return parser


def _read_context(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("context JSON root must be an object")
    return payload


def _parse_answer(raw: str) -> dict[str, Any]:
    if "=" not in raw:
        raise ValueError("answer must use QUESTION_ID=VALUE")
    question_id, raw_value = raw.split("=", 1)
    if not question_id:
        raise ValueError("answer question ID must not be blank")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return {"question_id": question_id, "value": value}


def _write_session(compiler: TaskCompiler, path: str) -> None:
    session = compiler.get_session()
    if session is None:
        raise RuntimeError("compiler did not create a session")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def _emit_result(result: CompilationResult, compiler: TaskCompiler, as_json: bool) -> int:
    provider_failure = any(item.code.startswith("E-PROVIDER-") for item in result.diagnostics)
    print(result_to_json(result) if as_json else render_result(result, session_id=compiler.last_session_id))
    return 6 if provider_failure else _STATUS_EXIT[result.status]


def _run(args: argparse.Namespace) -> int:
    if args.command == "contract":
        catalog = load_catalog(args.paths)
        print(f"Validated {len(catalog)} capability contract(s).")
        return 0
    if args.command == "schema":
        paths = export_schemas(args.output)
        print(f"Exported {len(paths)} schema(s) to {Path(args.output).resolve()}.")
        return 0
    if args.command == "compile":
        compiler = TaskCompiler.from_contract_paths(args.contracts)
        result = compiler.compile(args.request, _read_context(args.context))
        if args.session_out:
            _write_session(compiler, args.session_out)
        return _emit_result(result, compiler, args.as_json)
    if args.command == "continue":
        session_path = Path(args.session)
        session = CompilationSession.model_validate_json(session_path.read_text(encoding="utf-8"))
        store = InMemorySessionStore()
        store.put(session)
        compiler = TaskCompiler(catalog=load_catalog(args.contracts), session_store=store)
        result = compiler.continue_(session.session_id, [_parse_answer(item) for item in args.answer])
        _write_session(compiler, str(session_path))
        return _emit_result(result, compiler, args.as_json)
    raise AssertionError("unhandled command")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(_parser().parse_args(argv))
    except ContractValidationError as exc:
        for diagnostic in exc.diagnostics:
            print(render_diagnostic(diagnostic), file=sys.stderr)
        return 5
    except (InvalidInputError, ValidationError) as exc:
        if isinstance(exc, TaskCompilerError):
            for diagnostic in exc.diagnostics:
                print(render_diagnostic(diagnostic), file=sys.stderr)
        else:
            print(f"E-INPUT-VALIDATION: {exc}", file=sys.stderr)
        return 7
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"E-INPUT-IO: {exc}", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
