from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

try:  # The package dependency is required in distributions; this keeps source checkouts usable.
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised only in minimal development environments
    Draft202012Validator = None  # type: ignore[assignment,misc]

from taskc.models import CapabilityContract, ContractValidationError, Diagnostic

_CONTRACT_SCHEMA = CapabilityContract.model_json_schema(mode="validation")
_CONTRACT_SCHEMA_VALIDATOR = (
    Draft202012Validator(_CONTRACT_SCHEMA) if Draft202012Validator is not None else None
)


def _safe_path(parts: Sequence[Any]) -> str | None:
    return ".".join(str(part) for part in parts) or None


def validate_contract_data(data: Any, source: str = "<object>") -> CapabilityContract:
    if not isinstance(data, Mapping):
        diagnostic = Diagnostic(
            code="E-CONTRACT-001",
            severity="error",
            message="Capability contract root must be an object.",
            hint=source,
        )
        raise ContractValidationError(diagnostic.message, [diagnostic])

    schema_errors = (
        sorted(
            _CONTRACT_SCHEMA_VALIDATOR.iter_errors(dict(data)),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if _CONTRACT_SCHEMA_VALIDATOR is not None
        else []
    )
    if schema_errors:
        diagnostics = [
            Diagnostic(
                code="E-CONTRACT-002",
                severity="error",
                message=error.message,
                field_path=_safe_path(error.absolute_path),
                contract_id=str(data.get("id")) if data.get("id") else None,
                hint=source,
            )
            for error in schema_errors
        ]
        raise ContractValidationError("Contract failed JSON Schema validation.", diagnostics)

    try:
        return CapabilityContract.model_validate(dict(data))
    except ValidationError as exc:
        diagnostics = [
            Diagnostic(
                code="E-CONTRACT-003",
                severity="error",
                message=error["msg"],
                field_path=_safe_path(error["loc"]),
                contract_id=str(data.get("id")) if data.get("id") else None,
                hint=source,
            )
            for error in exc.errors(include_url=False, include_input=False)
        ]
        raise ContractValidationError("Contract failed semantic validation.", diagnostics) from exc


def validate_catalog(contracts: list[CapabilityContract]) -> None:
    seen: dict[tuple[str, str], int] = {}
    diagnostics: list[Diagnostic] = []
    for index, contract in enumerate(contracts):
        key = (contract.id, contract.version)
        if key in seen:
            diagnostics.append(
                Diagnostic(
                    code="E-CONTRACT-004",
                    severity="error",
                    message=f"Duplicate capability contract {contract.id}@{contract.version}.",
                    contract_id=contract.id,
                    hint=f"entries {seen[key]} and {index}",
                )
            )
        else:
            seen[key] = index
    if diagnostics:
        raise ContractValidationError("Catalog contains duplicate contracts.", diagnostics)


__all__ = ["validate_catalog", "validate_contract_data"]
