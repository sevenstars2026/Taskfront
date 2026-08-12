from __future__ import annotations

from copy import deepcopy
from typing import Any

from taskc.models import Diagnostic


def migrate_contract_data(data: dict[str, Any]) -> tuple[dict[str, Any], list[Diagnostic]]:
    migrated = deepcopy(data)
    diagnostics: list[Diagnostic] = []
    if migrated.get("schema_version") != "0.1":
        diagnostics.append(
            Diagnostic(
                code="E-MIGRATION-SOURCE-VERSION",
                severity="error",
                phase="contract_migration",
                message="Only Capability Contract 0.1 can be migrated to 0.2.",
            )
        )
        return migrated, diagnostics
    migrated["schema_version"] = "0.2"
    for section in ("required_inputs", "optional_inputs"):
        for field_name, spec in migrated.get(section, {}).items():
            if spec.pop("must_be_explicit", False):
                spec["source_policy"] = "trusted"
            if spec.pop("sensitive", False):
                spec["classification"] = "sensitive"
            if spec.get("type") == "enum":
                spec.setdefault("enum_aliases", {})
            diagnostics.append(
                Diagnostic(
                    code="I-MIGRATION-INPUT",
                    severity="info",
                    phase="contract_migration",
                    message="Input definition migrated to 0.2.",
                    field_path=f"{section}.{field_name}",
                    contract_ref=migrated.get("id"),
                )
            )
    for index, constraint in enumerate(migrated.get("constraints", [])):
        if "targets" not in constraint:
            constraint["targets"] = []
            diagnostics.append(
                Diagnostic(
                    code="E-MIGRATION-CONSTRAINT-TARGETS",
                    severity="error",
                    phase="contract_migration",
                    message="Constraint targets require manual selection before validation.",
                    field_path=f"constraints.{index}.targets",
                    contract_ref=migrated.get("id"),
                )
            )
    return migrated, diagnostics


__all__ = ["migrate_contract_data"]
