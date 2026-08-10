from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from taskc.config import CompilerConfig
from taskc.models import (
    CandidateDraft,
    CandidateIntent,
    CapabilityContract,
    Diagnostic,
    ExtractionResult,
    Gap,
    SourcedValue,
)
from taskc.models.capability import evaluate_condition, value_matches_type

_SOURCE_PRIORITY = {
    "clarification_answer": 0,
    "user": 1,
    "application_context": 2,
    "explicit_default": 3,
    "model_inference": 4,
}
_KIND_PRIORITY = {
    "conflicting": 0,
    "missing": 1,
    "unverifiable": 2,
    "ambiguous": 3,
    "unsupported": 4,
    "defaultable": 5,
}


@dataclass(slots=True)
class CandidateAnalysis:
    contract: CapabilityContract
    candidate: CandidateIntent
    gaps: list[Gap]
    diagnostics: list[Diagnostic]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _coerce(value: object, expected: str, config: CompilerConfig) -> object:
    if not config.allow_string_to_number or not isinstance(value, str):
        return value
    if expected == "integer":
        try:
            parsed = int(value)
            return parsed if str(parsed) == value.strip() else value
        except ValueError:
            return value
    if expected == "number":
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _effective_values(values: list[SourcedValue]) -> list[SourcedValue]:
    if not values:
        return []
    best = min(_SOURCE_PRIORITY[item.source] for item in values)
    effective = [item for item in values if _SOURCE_PRIORITY[item.source] == best]
    return sorted(effective, key=lambda item: (item.source_ref or "", _canonical(item.value)))


def _dedupe_gaps(gaps: Iterable[Gap]) -> list[Gap]:
    unique: dict[tuple[str, str | None, str], Gap] = {}
    for gap in gaps:
        unique[(gap.code, gap.field_path, _canonical(gap.candidate_values))] = gap
    return sorted(
        unique.values(),
        key=lambda gap: (
            not gap.blocking,
            _KIND_PRIORITY[gap.kind],
            gap.field_path or "",
            gap.code,
        ),
    )


def analyze_candidate(
    contract: CapabilityContract,
    draft: CandidateDraft,
    extraction: ExtractionResult,
    config: CompilerConfig,
) -> CandidateAnalysis:
    declared = contract.all_inputs
    gaps: list[Gap] = []
    diagnostics: list[Diagnostic] = []
    selected: dict[str, SourcedValue] = {}

    for unknown in sorted(set(extraction.values) - set(declared)):
        diagnostics.append(
            Diagnostic(
                code="E-INPUT-UNDECLARED-FIELD",
                severity="error",
                message="Extraction returned a field not declared by the capability contract.",
                field_path=unknown,
                contract_id=contract.id,
            )
        )

    for field_name, spec in declared.items():
        effective = _effective_values(extraction.values.get(field_name, []))
        if not effective:
            continue
        by_value: dict[str, SourcedValue] = {}
        for sourced in effective:
            candidate_value = _coerce(sourced.value, spec.type, config)
            normalized = sourced.model_copy(update={"value": candidate_value})
            by_value.setdefault(_canonical(candidate_value), normalized)
        distinct = list(by_value.values())
        if len(distinct) > 1:
            gaps.append(
                Gap(
                    code="GAP-CONFLICT-001",
                    kind="conflicting",
                    field_path=f"inputs.{field_name}",
                    message="Multiple equally authoritative values were provided for this input.",
                    blocking=True,
                    candidate_values=[item.value for item in distinct],
                    suggested_resolution="ask_user",
                )
            )
        chosen = distinct[0]
        if not value_matches_type(chosen.value, spec.type, spec.items):
            gaps.append(
                Gap(
                    code="GAP-UNVERIFIABLE-TYPE",
                    kind="unverifiable",
                    field_path=f"inputs.{field_name}",
                    message=f"Value does not match declared type {spec.type}.",
                    blocking=True,
                    candidate_values=[chosen.value],
                    suggested_resolution="ask_user",
                )
            )
            continue
        if spec.type == "enum" and chosen.value not in (spec.enum or []):
            gaps.append(
                Gap(
                    code="GAP-UNVERIFIABLE-ENUM",
                    kind="unverifiable",
                    field_path=f"inputs.{field_name}",
                    message="Value is not one of the declared enum choices.",
                    blocking=True,
                    candidate_values=[chosen.value, *(spec.enum or [])],
                    suggested_resolution="ask_user",
                )
            )
            continue
        selected[field_name] = chosen
        if spec.must_be_explicit and chosen.source not in config.explicit_sources:
            gaps.append(
                Gap(
                    code="GAP-UNVERIFIABLE-EXPLICIT",
                    kind="unverifiable",
                    field_path=f"inputs.{field_name}",
                    message="This field must be supplied by an explicit source.",
                    blocking=True,
                    candidate_values=[chosen.value],
                    suggested_resolution="ask_user",
                )
            )

    required = set(contract.required_inputs)
    raw_values = {field_name: sourced.value for field_name, sourced in selected.items()}
    conditional_fields: set[str] = set()
    for requirement in contract.conditional_requirements:
        if evaluate_condition(requirement.when, raw_values):
            conditional_fields.update(requirement.require)
    for field_name in sorted(required | conditional_fields):
        if field_name not in selected:
            gaps.append(
                Gap(
                    code=(
                        "GAP-MISSING-CONDITIONAL"
                        if field_name in conditional_fields and field_name not in required
                        else "GAP-MISSING-REQUIRED"
                    ),
                    kind="missing",
                    field_path=f"inputs.{field_name}",
                    message="Required capability input is missing.",
                    blocking=True,
                    suggested_resolution="ask_user",
                )
            )

    for constraint in contract.constraints:
        if not evaluate_condition(constraint.assert_condition, raw_values):
            gaps.append(
                Gap(
                    code=constraint.code,
                    kind="unverifiable",
                    field_path=None,
                    message=constraint.message,
                    blocking=constraint.blocking,
                    suggested_resolution="ask_user" if constraint.blocking else "reject",
                )
            )

    if extraction.unresolved_terms:
        gaps.append(
            Gap(
                code="GAP-AMBIGUOUS-TERMS",
                kind="ambiguous",
                field_path=None,
                message="The request contains unresolved terms relevant to this capability.",
                blocking=True,
                candidate_values=sorted(set(extraction.unresolved_terms)),
                suggested_resolution="ask_user",
            )
        )

    candidate = CandidateIntent(
        capability_id=contract.id,
        capability_version=contract.version,
        score=draft.score,
        match_reasons=draft.match_reasons,
        inputs=dict(sorted(selected.items())),
        unresolved_terms=sorted(set(extraction.unresolved_terms)),
    )
    return CandidateAnalysis(
        contract=contract,
        candidate=candidate,
        gaps=_dedupe_gaps(gaps),
        diagnostics=diagnostics,
    )


__all__ = ["CandidateAnalysis", "analyze_candidate"]

