from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

import yaml
from pydantic import ValidationError

from taskc import CapabilityRef, CompilerConfig, ProviderBudgetConfig, TaskCompiler, __version__
from taskc.adapters import envelope_to_json
from taskc.contracts import (
    discover_contract_files,
    lint_catalog,
    load_catalog,
    migrate_contract_data,
    validate_contract_data,
)
from taskc.evaluation import EvaluationScenario, evaluate_scenarios
from taskc.models import (
    CompilationResult,
    CompilationSession,
    CompileEnvelope,
    ContractValidationError,
    Diagnostic,
    InternalCompilerError,
    InvalidInputError,
    OperationError,
    TaskCompilerError,
)
from taskc.renderers import render_diagnostic, render_result
from taskc.schema import export_schemas
from taskc.security import session_contains_sensitive_literals
from taskc.session import InMemorySessionStore

_STATUS_EXIT = {
    "ready": 0,
    "needs_clarification": 2,
    "ambiguous": 2,
    "conflicting": 3,
    "unsupported": 4,
}
_ERROR_EXIT = {
    "invalid_contract": 5,
    "provider_failure": 6,
    "invalid_input": 7,
    "stale_session": 8,
    "budget_exceeded": 9,
    "internal_error": 10,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskc", description="TaskFront Agent Task Compiler")
    parser.add_argument("--version", action="version", version=f"taskc {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract", help="Capability contract commands")
    contract_sub = contract.add_subparsers(dest="contract_command", required=True)
    validate = contract_sub.add_parser("validate", help="Validate capability contracts")
    validate.add_argument("paths", nargs="+", help="YAML/JSON file or directory")
    lint = contract_sub.add_parser("lint", help="Lint capability contracts")
    lint.add_argument("paths", nargs="+")
    migrate = contract_sub.add_parser("migrate", help="Migrate 0.1 contracts to 0.2")
    migrate.add_argument("paths", nargs="+")
    migrate.add_argument("--from", dest="from_version", required=True)
    migrate.add_argument("--to", dest="to_version", required=True)
    migrate.add_argument("--output")
    migrate.add_argument("--in-place", action="store_true")
    migrate.add_argument("--json", action="store_true", dest="as_json")

    compile_parser = subparsers.add_parser("compile", help="Compile a natural-language request")
    compile_parser.add_argument("request", nargs="?")
    compile_parser.add_argument("--request-file")
    compile_parser.add_argument("--contracts", nargs="+", required=True)
    compile_parser.add_argument("--context", help="JSON context file")
    compile_parser.add_argument("--force-capability", help="Exact ID@VERSION selected by the host")
    compile_parser.add_argument("--session-out", help="Write a resumable JSON session")
    compile_parser.add_argument("--json", action="store_true", dest="as_json")
    _add_compiler_options(compile_parser)

    continue_parser = subparsers.add_parser("continue", help="Continue a saved compilation session")
    continue_parser.add_argument("session", help="Saved session JSON file")
    continue_parser.add_argument("--contracts", nargs="+", required=True)
    continue_parser.add_argument(
        "--answer", action="append", required=True, metavar="QUESTION_ID=JSON"
    )
    continue_parser.add_argument("--json", action="store_true", dest="as_json")
    _add_compiler_options(continue_parser)

    explain = subparsers.add_parser("explain", help="Render a result or session explain trace")
    explain.add_argument("path")
    explain.add_argument("--json", action="store_true", dest="as_json")

    schema = subparsers.add_parser("schema", help="JSON Schema commands")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_sub.add_parser("export", help="Export public JSON Schemas")
    export.add_argument("--version", default="0.2")
    export.add_argument("--output", default="schemas")

    evaluation = subparsers.add_parser("eval", help="Evaluation commands")
    evaluation_sub = evaluation.add_subparsers(dest="eval_command", required=True)
    run = evaluation_sub.add_parser("run", help="Run a labeled evaluation dataset")
    run.add_argument("dataset")
    run.add_argument("--contracts", nargs="+", required=True)
    run.add_argument("--json", action="store_true", dest="as_json")
    _add_compiler_options(run)
    return parser


def _add_compiler_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--max-questions", type=int, default=1)
    parser.add_argument("--max-logical-model-calls", type=int, default=2)
    parser.add_argument("--max-network-attempts", type=int, default=3)
    parser.add_argument("--max-provider-time-seconds", type=float, default=60.0)
    parser.add_argument("--max-response-bytes", type=int, default=2_000_000)
    parser.add_argument("--allow-prerelease", action="store_true")


def _config_from_args(args: argparse.Namespace) -> CompilerConfig:
    return CompilerConfig(
        max_candidates=args.max_candidates,
        max_questions_per_round=args.max_questions,
        allow_prerelease=args.allow_prerelease,
        provider_budget=ProviderBudgetConfig(
            max_logical_model_calls=args.max_logical_model_calls,
            max_network_attempts=args.max_network_attempts,
            max_provider_time_seconds=args.max_provider_time_seconds,
            max_response_bytes=args.max_response_bytes,
        ),
    )


def _read_context(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("context JSON root must be an object")
    return payload


def _read_request(args: argparse.Namespace) -> str:
    if bool(args.request) == bool(args.request_file):
        raise ValueError("provide exactly one of REQUEST or --request-file")
    if args.request is not None:
        return args.request
    if args.request_file == "-":
        return sys.stdin.read()
    return Path(args.request_file).read_text(encoding="utf-8")


def _parse_capability_ref(raw: str | None) -> CapabilityRef | None:
    if raw is None:
        return None
    capability_id, separator, version = raw.rpartition("@")
    if not separator or not capability_id or not version:
        raise ValueError("--force-capability must use ID@VERSION")
    return CapabilityRef(id=capability_id, version=version)


def _parse_answer(raw: str, session: CompilationSession) -> dict[str, Any]:
    if "=" not in raw:
        raise ValueError("answer must use QUESTION_ID=JSON")
    question_id, raw_value = raw.split("=", 1)
    question = next((item for item in session.last_result.questions if item.id == question_id), None)
    if question is None:
        raise ValueError(f"unknown current question ID: {question_id}")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    payload: dict[str, Any] = {
        "question_id": question_id,
        "result_id": question.result_id,
        "revision": question.revision,
    }
    if isinstance(value, dict) and set(value) == {"value_ref"}:
        payload["value_ref"] = value["value_ref"]
    else:
        payload["value"] = value
    return payload


def _write_session(compiler: TaskCompiler, path: str) -> None:
    session = compiler.get_session()
    if session is None:
        raise RuntimeError("compiler did not create a session")
    if session_contains_sensitive_literals(session):
        raise InvalidInputError(
            "Plain JSON sessions cannot persist sensitive literal values.",
            [
                Diagnostic(
                    code="E-INPUT-SENSITIVE-SESSION",
                    severity="error",
                    phase="session_persistence",
                    message="Plain JSON sessions cannot persist sensitive literal values.",
                )
            ],
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(session.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(target)


def _write_migrated_file(destination: Path, text: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(destination)


def _emit_result(result: CompilationResult, as_json: bool) -> int:
    if as_json:
        envelope = CompileEnvelope(
            schema_version="0.2",
            kind="result",
            serialization_profile="public",
            result=result,
        )
        print(envelope_to_json(envelope))
    else:
        print(render_result(result, session_id=result.session_id))
    return _STATUS_EXIT[result.status]


def _run(args: argparse.Namespace) -> int:
    if args.command == "contract" and args.contract_command == "validate":
        catalog = load_catalog(args.paths)
        print(f"Validated {len(catalog)} capability contract(s).")
        return 0
    if args.command == "contract" and args.contract_command == "lint":
        catalog = load_catalog(args.paths)
        diagnostics = lint_catalog(list(catalog.contracts))
        for diagnostic in diagnostics:
            print(render_diagnostic(diagnostic))
        print(f"Linted {len(catalog)} capability contract(s); {len(diagnostics)} warning(s).")
        return 0
    if args.command == "contract" and args.contract_command == "migrate":
        if args.from_version != "0.1" or args.to_version != "0.2":
            raise ValueError("this release only supports migration from 0.1 to 0.2")
        if args.in_place == bool(args.output):
            raise ValueError("provide exactly one of --output or --in-place")
        target = Path(args.output) if args.output else None
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
        failures = 0
        report: list[dict[str, Any]] = []
        for source in discover_contract_files(args.paths):
            raw = source.read_text(encoding="utf-8")
            data = json.loads(raw) if source.suffix.lower() == ".json" else yaml.safe_load(raw)
            migrated, diagnostics = migrate_contract_data(data)
            destination = source if args.in_place else target / source.name
            if destination.suffix.lower() == ".json":
                _write_migrated_file(
                    destination,
                    json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
                )
            else:
                _write_migrated_file(
                    destination,
                    yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True),
                )
            try:
                validated = validate_contract_data(migrated, str(destination))
                diagnostics.extend(lint_catalog([validated]))
            except ContractValidationError as exc:
                diagnostics.extend(exc.diagnostics)
            failed = any(item.severity == "error" for item in diagnostics)
            failures += int(failed)
            report.append({
                "source": str(source),
                "destination": str(destination),
                "valid": not failed,
                "diagnostics": [
                    item.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for item in diagnostics
                ],
            })
            if not args.as_json:
                for diagnostic in diagnostics:
                    print(render_diagnostic(diagnostic))
        if args.as_json:
            print(json.dumps({"schema_version": "0.2", "migrations": report}, ensure_ascii=False, indent=2))
        return 5 if failures else 0
    if args.command == "schema":
        paths = export_schemas(args.output, version=args.version)
        print(f"Exported {len(paths)} schema(s) to {Path(args.output).resolve()}.")
        return 0
    if args.command == "compile":
        compiler = TaskCompiler.from_contract_paths(
            args.contracts, config=_config_from_args(args)
        )
        result = compiler.compile(
            _read_request(args),
            _read_context(args.context),
            requested_capability=_parse_capability_ref(args.force_capability),
        )
        if args.session_out:
            _write_session(compiler, args.session_out)
        return _emit_result(result, args.as_json)
    if args.command == "continue":
        session_path = Path(args.session)
        session = CompilationSession.model_validate_json(session_path.read_text(encoding="utf-8"))
        store = InMemorySessionStore()
        store.put(session, expected_revision=-1)
        compiler = TaskCompiler(
            catalog=load_catalog(args.contracts),
            session_store=store,
            config=_config_from_args(args),
        )
        result = compiler.continue_(
            session.session_id,
            session.revision,
            [_parse_answer(item, session) for item in args.answer],
        )
        _write_session(compiler, str(session_path))
        return _emit_result(result, args.as_json)
    if args.command == "explain":
        payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
        if "last_result" in payload:
            result = CompilationSession.model_validate(payload).last_result
        elif payload.get("kind") == "result":
            result = CompileEnvelope.model_validate(payload).result
        else:
            result = CompilationResult.model_validate(payload)
        if result is None or result.trace is None:
            raise ValueError("input does not contain an explain trace")
        if args.as_json:
            print(result.trace.model_dump_json(indent=2))
        else:
            for step in result.trace.steps:
                fields = f" [{', '.join(step.field_paths)}]" if step.field_paths else ""
                print(f"{step.phase}: {step.reason_code}{fields}")
        return 0
    if args.command == "eval":
        raw = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        scenarios = [EvaluationScenario.model_validate(item) for item in raw]
        catalog = load_catalog(args.contracts)
        config = _config_from_args(args)
        report = evaluate_scenarios(
            scenarios, lambda: TaskCompiler(catalog=catalog, config=config)
        )
        if args.as_json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            for key, value in report.as_dict().items():
                print(f"{key}: {value}")
        return 0 if not report.failures else 2
    raise AssertionError("unhandled command")


def _generic_error(exc: Exception) -> OperationError:
    diagnostic = Diagnostic(
        code="E-INTERNAL",
        severity="error",
        phase="cli",
        message="The CLI failed without a safe typed error.",
    )
    return OperationError(
        code=diagnostic.code,
        category="internal_error",
        message=diagnostic.message,
        retryable=False,
        diagnostics=[diagnostic],
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except TaskCompilerError as exc:
        if getattr(args, "as_json", False):
            print(
                envelope_to_json(
                    CompileEnvelope(
                        schema_version="0.2",
                        kind="error",
                        serialization_profile="public",
                        error=exc.error,
                    )
                )
            )
        else:
            for diagnostic in exc.diagnostics:
                print(render_diagnostic(diagnostic), file=sys.stderr)
        return _ERROR_EXIT[exc.error.category]
    except (ValidationError, OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
        error = _generic_error(exc)
        if isinstance(exc, (ValidationError, ValueError, json.JSONDecodeError)):
            error = error.model_copy(
                update={
                    "code": "E-INPUT-VALIDATION",
                    "category": "invalid_input",
                    "message": str(exc),
                }
            )
        if getattr(args, "as_json", False):
            print(
                envelope_to_json(
                    CompileEnvelope(
                        schema_version="0.2",
                        kind="error",
                        serialization_profile="public",
                        error=error,
                    )
                )
            )
        else:
            print(f"{error.code}: {error.message}", file=sys.stderr)
        return _ERROR_EXIT[error.category]
    except Exception as exc:  # pragma: no cover - last-resort safety boundary
        error = _generic_error(exc)
        if getattr(args, "as_json", False):
            print(
                envelope_to_json(
                    CompileEnvelope(
                        schema_version="0.2",
                        kind="error",
                        serialization_profile="public",
                        error=error,
                    )
                )
            )
        else:
            print(f"{error.code}: {error.message}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
