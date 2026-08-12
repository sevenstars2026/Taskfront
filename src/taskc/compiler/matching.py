from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from jsonschema import Draft202012Validator

from taskc.config import CompilerConfig
from taskc.extensions import ExtensionRegistry
from taskc.models import (
    CandidateDraft,
    CandidateIntent,
    CapabilityContract,
    Diagnostic,
    ExtractionResult,
    Gap,
    InternalCompilerError,
    SourcedValue,
    TruthValue,
)
from taskc.models.capability import evaluate_condition, value_matches_type
from taskc.security import SecretResolver

_SOURCE_PRIORITY = {
    "clarification_answer": 0,
    "user_request": 1,
    "application_context": 2,
    "contract_default": 3,
    "model_inference": 4,
}
_KIND_PRIORITY = {
    "conflicting": 0,
    "missing": 1,
    "unverifiable": 2,
    "constraint": 3,
    "ambiguous": 4,
}
_SOURCE_POLICY = {
    "any": set(_SOURCE_PRIORITY),
    "trusted": set(_SOURCE_PRIORITY) - {"model_inference"},
    "user_confirmed": {"user_request", "clarification_answer"},
}


@dataclass(slots=True)
class CandidateAnalysis:
    contract: CapabilityContract
    candidate: CandidateIntent
    gaps: list[Gap]
    diagnostics: list[Diagnostic]
    reproducible: bool = True


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _coerce(value: object, expected: str, config: CompilerConfig) -> object:
    if not config.allow_string_to_number or not isinstance(value, str):
        return value
    stripped = value.strip()
    if expected == "integer":
        try:
            parsed = int(stripped)
            return parsed if str(parsed) == stripped else value
        except ValueError:
            return value
    if expected == "number":
        try:
            parsed = float(stripped)
            return parsed if format(parsed, "g") == stripped else value
        except ValueError:
            return value
    return value


def _dedupe_gaps(gaps: Iterable[Gap]) -> list[Gap]:
    unique: dict[tuple[str, tuple[str, ...], str], Gap] = {}
    for gap in gaps:
        unique[(gap.code, tuple(gap.field_paths), _canonical(gap.candidate_values))] = gap
    return sorted(
        unique.values(),
        key=lambda gap: (
            not gap.blocking,
            _KIND_PRIORITY[gap.kind],
            gap.field_paths[0] if gap.field_paths else "",
            gap.code,
            _canonical(gap.candidate_values),
        ),
    )


def _matches_value_schema(value: object, schema: dict | None) -> bool:
    return schema is None or not list(Draft202012Validator(schema).iter_errors(value))


def analyze_candidate(
    contract: CapabilityContract,
    draft: CandidateDraft,
    extraction: ExtractionResult,
    config: CompilerConfig,
    *,
    secret_resolver: SecretResolver | None = None,
    extension_registry: ExtensionRegistry | None = None,
) -> CandidateAnalysis:
    declared = contract.all_inputs
    gaps: list[Gap] = []
    diagnostics: list[Diagnostic] = []
    reproducible = True
    selected: dict[str, SourcedValue] = {}

    unknown = sorted(set(extraction.values) - set(declared))
    if unknown:
        diagnostics.append(
            Diagnostic(
                code="E-PROVIDER-UNKNOWN-FIELD",
                severity="error",
                phase="field_resolution",
                message="Extraction returned undeclared capability inputs.",
                field_path=unknown[0],
                contract_ref=f"{contract.id}@{contract.version}",
            )
        )

    for field_name, spec in declared.items():
        supplied = [item for item in extraction.values.get(field_name, []) if not item.redacted]
        if not supplied:
            continue
        best_priority = min(_SOURCE_PRIORITY[item.source] for item in supplied)
        effective = sorted(
            [item for item in supplied if _SOURCE_PRIORITY[item.source] == best_priority],
            key=lambda item: (item.source_ref or "", _canonical(item.value if item.value_ref is None else item.value_ref)),
        )
        shadowed = [item for item in supplied if _SOURCE_PRIORITY[item.source] > best_priority]
        effective_values = {
            _canonical(item.value if item.value_ref is None else {"value_ref": item.value_ref})
            for item in effective
        }
        top = effective[0]
        top_canonical = next(iter(effective_values)) if len(effective_values) == 1 else None
        if len(effective_values) > 1:
            gaps.append(
                Gap(
                    code="GAP-CONFLICT-001",
                    kind="conflicting",
                    field_paths=[f"inputs.{field_name}"],
                    message="Multiple equally authoritative values were provided for this input.",
                    blocking=True,
                    candidate_values=(
                        [
                            item.value if item.value_ref is None else {"value_ref": item.value_ref}
                            for item in effective
                        ]
                        if spec.classification == "public"
                        else []
                    ),
                    suggested_resolution="ask_user",
                )
            )
        if top_canonical is not None and any(
            _canonical(item.value if item.value_ref is None else {"value_ref": item.value_ref})
            != top_canonical
            for item in shadowed
        ):
            diagnostics.append(
                Diagnostic(
                    code="W-INPUT-SHADOWED-VALUE",
                    severity="warning",
                    phase="field_resolution",
                    message="A lower-priority conflicting value was shadowed.",
                    field_path=f"inputs.{field_name}",
                    contract_ref=f"{contract.id}@{contract.version}",
                )
            )

        normalized = top.model_copy(update={"classification": spec.classification})
        if spec.classification == "secret" and normalized.value_ref is None:
            gaps.append(
                Gap(
                    code="GAP-UNVERIFIABLE-SECRET-LITERAL",
                    kind="unverifiable",
                    field_paths=[f"inputs.{field_name}"],
                    message="Secret inputs must be supplied as a value reference.",
                    blocking=True,
                    suggested_resolution="ask_user",
                )
            )
            continue
        if normalized.value_ref is not None:
            if secret_resolver is None or not secret_resolver.validate_ref(
                normalized.value_ref, expected_type=spec.type
            ):
                gaps.append(
                    Gap(
                        code="GAP-UNVERIFIABLE-SECRET-REF",
                        kind="unverifiable",
                        field_paths=[f"inputs.{field_name}"],
                        message="The value reference could not be validated.",
                        blocking=True,
                        suggested_resolution="ask_user",
                    )
                )
                continue
        else:
            candidate_value = _coerce(normalized.value, spec.type, config)
            if spec.type == "enum" and isinstance(candidate_value, str):
                candidate_value = spec.enum_aliases.get(
                    unicodedata.normalize("NFC", candidate_value), candidate_value
                )
            normalized = normalized.model_copy(update={"value": candidate_value})
            if not value_matches_type(candidate_value, spec.type, spec.items):
                gaps.append(
                    Gap(
                        code="GAP-UNVERIFIABLE-TYPE",
                        kind="unverifiable",
                        field_paths=[f"inputs.{field_name}"],
                        message=f"Value does not match declared type {spec.type}.",
                        blocking=True,
                        candidate_values=(
                            [candidate_value] if spec.classification == "public" else []
                        ),
                        suggested_resolution="ask_user",
                    )
                )
                continue
            if spec.type == "enum" and candidate_value not in (spec.enum or []):
                gaps.append(
                    Gap(
                        code="GAP-UNVERIFIABLE-ENUM",
                        kind="unverifiable",
                        field_paths=[f"inputs.{field_name}"],
                        message="Value is not one of the declared enum choices.",
                        blocking=True,
                        candidate_values=(
                            [candidate_value, *(spec.enum or [])]
                            if spec.classification == "public"
                            else []
                        ),
                        suggested_resolution="ask_user",
                    )
                )
                continue
            if not _matches_value_schema(candidate_value, spec.value_schema):
                gaps.append(
                    Gap(
                        code="GAP-UNVERIFIABLE-VALUE-SCHEMA",
                        kind="unverifiable",
                        field_paths=[f"inputs.{field_name}"],
                        message="Value does not satisfy the declared value schema.",
                        blocking=True,
                        suggested_resolution="ask_user",
                    )
                )
                continue
        selected[field_name] = normalized
        if normalized.source not in _SOURCE_POLICY[spec.source_policy]:
            gaps.append(
                Gap(
                    code="GAP-UNVERIFIABLE-SOURCE",
                    kind="unverifiable",
                    field_paths=[f"inputs.{field_name}"],
                    message=f"This field requires source policy {spec.source_policy}.",
                    blocking=True,
                    suggested_resolution="ask_user",
                )
            )

    required = set(contract.required_inputs)
    raw_values = {
        field_name: sourced.value
        for field_name, sourced in selected.items()
        if sourced.value_ref is None
    }
    conditional_fields: set[str] = set()
    for requirement in contract.conditional_requirements:
        if evaluate_condition(requirement.when, raw_values) is TruthValue.TRUE:
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
                    field_paths=[f"inputs.{field_name}"],
                    message="Required capability input is missing.",
                    blocking=True,
                    suggested_resolution="ask_user",
                )
            )

    for constraint in contract.constraints:
        when = (
            TruthValue.TRUE
            if constraint.when is None
            else evaluate_condition(constraint.when, raw_values)
        )
        if when is not TruthValue.TRUE:
            continue
        if constraint.evaluator is not None:
            assert extension_registry is not None
            descriptor, evaluator = extension_registry.constraint_evaluators[constraint.evaluator]
            reproducible = reproducible and descriptor.deterministic
            extension_values: dict[str, object] = {}
            for field_name, value in raw_values.items():
                classification = contract.all_inputs[field_name].classification
                if classification == "public" or descriptor.sensitive_access == "value":
                    extension_values[field_name] = value
                elif descriptor.sensitive_access == "metadata":
                    extension_values[field_name] = {
                        "present": True,
                        "classification": classification,
                    }
            try:
                evaluated = evaluator.evaluate(
                    extension_values,
                    options=constraint.evaluator_options,
                )
            except Exception as exc:
                diagnostic = Diagnostic(
                    code="E-EXTENSION-CONSTRAINT",
                    severity="error",
                    phase="constraint_evaluation",
                    message="A registered constraint evaluator failed.",
                    contract_ref=f"{contract.id}@{contract.version}",
                )
                raise InternalCompilerError(diagnostic.message, [diagnostic]) from exc
            if evaluated is not True and evaluated is not False and evaluated is not None:
                diagnostic = Diagnostic(
                    code="E-EXTENSION-CONSTRAINT-RESULT",
                    severity="error",
                    phase="constraint_evaluation",
                    message="A constraint evaluator returned an invalid result.",
                    contract_ref=f"{contract.id}@{contract.version}",
                )
                raise InternalCompilerError(diagnostic.message, [diagnostic])
            assertion = (
                TruthValue.UNKNOWN if evaluated is None
                else TruthValue.TRUE if evaluated else TruthValue.FALSE
            )
        else:
            assert constraint.assert_condition is not None
            assertion = evaluate_condition(constraint.assert_condition, raw_values)
        if assertion is TruthValue.FALSE:
            gaps.append(
                Gap(
                    code=constraint.code,
                    kind="constraint",
                    field_paths=[f"inputs.{field_name}" for field_name in constraint.targets],
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
                field_paths=["request.unresolved_terms"],
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
        evidence=draft.evidence,
        inputs=dict(sorted(selected.items())),
        unresolved_terms=sorted(set(extraction.unresolved_terms)),
        viability=(
            "viable"
            if reproducible or config.allow_nondeterministic_extensions
            else "rejected"
        ),
    )
    return CandidateAnalysis(
        contract=contract,
        candidate=candidate,
        gaps=_dedupe_gaps(gaps),
        diagnostics=diagnostics,
        reproducible=reproducible,
    )


__all__ = ["CandidateAnalysis", "analyze_candidate"]
