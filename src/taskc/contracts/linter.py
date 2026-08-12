from __future__ import annotations

from taskc.models import CapabilityContract, Diagnostic
from taskc.versions import SemVer


def lint_contract(contract: CapabilityContract) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    ref = f"{contract.id}@{contract.version}"
    if not contract.examples:
        diagnostics.append(
            Diagnostic(
                code="W-CONTRACT-NO-EXAMPLES",
                severity="warning",
                phase="contract_lint",
                message="Capability has no positive matching examples.",
                contract_ref=ref,
            )
        )
    if not contract.negative_examples:
        diagnostics.append(
            Diagnostic(
                code="W-CONTRACT-NO-NEGATIVE-EXAMPLES",
                severity="warning",
                phase="contract_lint",
                message="Capability has no negative matching examples.",
                contract_ref=ref,
            )
        )
    if SemVer.parse(contract.version).build:
        diagnostics.append(
            Diagnostic(
                code="W-CONTRACT-BUILD-METADATA",
                severity="warning",
                phase="contract_lint",
                message="Build metadata can make implicit version selection ambiguous.",
                contract_ref=ref,
            )
        )
    if len(contract.required_inputs) > 6:
        diagnostics.append(
            Diagnostic(
                code="W-CONTRACT-MANY-REQUIRED-INPUTS",
                severity="warning",
                phase="contract_lint",
                message="Many required inputs may cause excessive clarification turns.",
                contract_ref=ref,
            )
        )
    for field_name, spec in contract.all_inputs.items():
        if spec.classification in {"sensitive", "secret"} and spec.source_policy == "any":
            diagnostics.append(
                Diagnostic(
                    code="W-CONTRACT-CLASSIFIED-SOURCE-POLICY",
                    severity="warning",
                    phase="contract_lint",
                    message="Classified inputs should normally use trusted or user_confirmed sources.",
                    field_path=f"inputs.{field_name}",
                    contract_ref=ref,
                )
            )
        if spec.type == "enum" and any(
            not str(value).strip() for value in spec.enum or []
        ):
            diagnostics.append(
                Diagnostic(
                    code="W-CONTRACT-ENUM-EMPTY-LABEL",
                    severity="warning",
                    phase="contract_lint",
                    message="Enum choices should have non-blank user-facing values.",
                    field_path=f"inputs.{field_name}",
                    contract_ref=ref,
                )
            )
    required = set(contract.required_inputs)
    for index, requirement in enumerate(contract.conditional_requirements):
        if set(requirement.require) <= required:
            diagnostics.append(
                Diagnostic(
                    code="W-CONTRACT-REDUNDANT-CONDITIONAL",
                    severity="warning",
                    phase="contract_lint",
                    message="Conditional requirement only repeats unconditional required fields.",
                    field_path=f"conditional_requirements.{index}",
                    contract_ref=ref,
                )
            )
    return diagnostics


def lint_catalog(contracts: list[CapabilityContract]) -> list[Diagnostic]:
    diagnostics = [item for contract in contracts for item in lint_contract(contract)]
    by_id: dict[str, list[CapabilityContract]] = {}
    for contract in contracts:
        by_id.setdefault(contract.id, []).append(contract)
    for capability_id, versions in by_id.items():
        for index, left in enumerate(versions):
            for right in versions[index + 1:]:
                if (
                    left.version != right.version
                    and SemVer.parse(left.version).same_precedence(SemVer.parse(right.version))
                ):
                    diagnostics.append(
                        Diagnostic(
                            code="W-CONTRACT-EQUAL-VERSION-PRECEDENCE",
                            severity="warning",
                            phase="contract_lint",
                            message="Two versions have equal SemVer precedence.",
                            contract_ref=capability_id,
                        )
                    )
    return sorted(
        diagnostics,
        key=lambda item: (item.contract_ref or "", item.field_path or "", item.code),
    )


__all__ = ["lint_catalog", "lint_contract"]
