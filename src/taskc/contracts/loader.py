from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from taskc.models import ContractValidationError, Diagnostic

from .catalog import CapabilityCatalog
from .validator import validate_catalog, validate_contract_data

_SUFFIXES = {".json", ".yaml", ".yml"}


def _structure_within_limits(value: Any) -> bool:
    stack = [(value, 0)]
    visited: set[int] = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 30 or nodes > 10_000:
            return False
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in visited:
                continue
            visited.add(identity)
            if len(item) > 1_000:
                return False
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return True


def discover_contract_files(paths: Iterable[str | Path]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(
                child for child in path.rglob("*") if child.is_file() and child.suffix.lower() in _SUFFIXES
            )
        elif path.is_file() and path.suffix.lower() in _SUFFIXES:
            files.append(path)
        else:
            diagnostic = Diagnostic(
                code="E-CONTRACT-005",
                severity="error",
                message="Contract path does not exist or has an unsupported extension.",
                hint=str(path),
            )
            raise ContractValidationError(diagnostic.message, [diagnostic])
    return sorted(set(path.resolve() for path in files), key=lambda item: str(item).casefold())


def _parse_file(path: Path) -> Any:
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError("contract file exceeds the 2 MB safety limit")
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        diagnostic = Diagnostic(
            code="E-CONTRACT-006",
            severity="error",
            message=f"Unable to parse capability contract: {type(exc).__name__}.",
            hint=str(path),
        )
        raise ContractValidationError(diagnostic.message, [diagnostic]) from exc
    if not _structure_within_limits(data):
        diagnostic = Diagnostic(
            code="E-CONTRACT-007",
            severity="error",
            message="Capability contract nesting exceeds the safety limit.",
            hint=str(path),
        )
        raise ContractValidationError(diagnostic.message, [diagnostic])
    return data


def load_catalog(paths: Iterable[str | Path]) -> CapabilityCatalog:
    files = discover_contract_files(paths)
    if not files:
        diagnostic = Diagnostic(
            code="E-CONTRACT-008",
            severity="error",
            message="No capability contract files were found.",
        )
        raise ContractValidationError(diagnostic.message, [diagnostic])
    contracts = [validate_contract_data(_parse_file(path), str(path)) for path in files]
    validate_catalog(contracts)
    return CapabilityCatalog.build(contracts)


def load_catalog_objects(objects: Iterable[dict[str, Any]]) -> CapabilityCatalog:
    contracts = [validate_contract_data(item, f"<object:{index}>") for index, item in enumerate(objects)]
    validate_catalog(contracts)
    return CapabilityCatalog.build(contracts)


__all__ = ["discover_contract_files", "load_catalog", "load_catalog_objects"]
