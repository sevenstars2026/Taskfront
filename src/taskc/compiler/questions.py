from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from taskc.config import CompilerConfig
from taskc.models import ClarificationQuestion, Diagnostic, Gap
from taskc.models import InternalCompilerError
from taskc.models.capability import CapabilityContract, InputSpec


def _answer_type(spec: InputSpec) -> str:
    if spec.type == "enum":
        return "single_choice"
    if spec.type == "boolean":
        return "boolean"
    if spec.type == "integer":
        return "integer"
    if spec.type == "number":
        return "number"
    if spec.type in {"array", "object"}:
        return "json"
    return "text"


def _question_id(revision: int, answer_type: str, targets: list[str], choices: list[object]) -> str:
    canonical = json.dumps(
        {"answer_type": answer_type, "targets": sorted(targets), "choices": choices},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"q-{revision}-{digest}"


def _default_text(field_name: str, spec: InputSpec) -> str:
    if spec.classification == "secret":
        return f"Provide a secure reference for {field_name}."
    if spec.type == "enum":
        return f"Which value should be used for {field_name}?"
    if spec.type == "boolean":
        return f"Should {field_name} be enabled?"
    if spec.type in {"integer", "number"}:
        return f"What numeric value should be used for {field_name}?"
    return f"What value should be used for {field_name}?"


def candidate_question(
    capability_refs: list[str],
    config: CompilerConfig,
    *,
    result_id: str,
    revision: int,
) -> ClarificationQuestion:
    count = len(capability_refs)
    elimination = (count - 1) / count
    weights = config.question_weights
    priority = weights.candidate_elimination * elimination + weights.blocking_gap - weights.user_cost * 0.2
    answer_type = "single_choice"
    targets = ["capability"]
    return ClarificationQuestion(
        id=_question_id(revision, answer_type, targets, capability_refs),
        result_id=result_id,
        revision=revision,
        text="Which capability best matches the task objective?",
        targets=targets,
        answer_type=answer_type,
        choices=capability_refs,
        priority=round(priority, 6),
        reason_code="QUESTION-SELECT-CAPABILITY",
    )


def plan_questions(
    gaps: list[Gap],
    contract: CapabilityContract,
    config: CompilerConfig,
    *,
    result_id: str,
    revision: int,
    has_host_renderer: bool = False,
    custom_ranker=None,
) -> tuple[list[ClarificationQuestion], list[Diagnostic]]:
    blocking = [gap for gap in gaps if gap.blocking]
    by_field: dict[str, list[Gap]] = defaultdict(list)
    diagnostics: list[Diagnostic] = []
    for gap in blocking:
        for target in gap.field_paths:
            by_field[target].append(gap)
    questions: list[ClarificationQuestion] = []
    total = max(1, len(blocking))
    max_dependencies = max(1, len(contract.conditional_requirements))
    for target, field_gaps in sorted(by_field.items()):
        if target == "request.unresolved_terms":
            choices = sorted({value for gap in field_gaps for value in gap.candidate_values}, key=str)
            answer_type = "text"
            cost = 0.6
            dependency_ratio = 0.0
            classification = "public"
            text = "What should the unresolved request terms mean for this task?"
        elif target.startswith("inputs."):
            field_name = target.removeprefix("inputs.")
            spec = contract.all_inputs[field_name]
            choices = list(spec.enum or [])
            for gap in field_gaps:
                if gap.kind == "conflicting" and gap.candidate_values and spec.classification == "public":
                    choices = gap.candidate_values
                    break
            answer_type = _answer_type(spec)
            cost = {
                "single_choice": 0.2,
                "boolean": 0.2,
                "integer": 0.3,
                "number": 0.3,
                "multi_choice": 0.4,
                "text": 0.6,
                "json": 0.6,
            }[answer_type]
            dependent = sum(
                1
                for requirement in contract.conditional_requirements
                if field_name in requirement.when.referenced_fields()
            )
            dependency_ratio = min(1.0, dependent / max_dependencies)
            classification = spec.classification
            if spec.classification == "secret":
                text = _default_text(field_name, spec)
            elif spec.question_template:
                text = spec.question_template.format(
                    field=field_name,
                    description=spec.description,
                    choices=", ".join(map(str, choices)),
                )
            else:
                text = _default_text(field_name, spec)
        else:
            diagnostics.append(
                Diagnostic(
                    code="E-INTERNAL-QUESTION-TARGET",
                    severity="error",
                    phase="question_planning",
                    message="A blocking gap has an unsupported question target.",
                    field_path=target,
                    contract_ref=f"{contract.id}@{contract.version}",
                )
            )
            continue
        sensitivity = {"public": 0.0, "sensitive": 0.5, "secret": 1.0}[classification]
        weights = config.question_weights
        resolved_ratio = len(set(id(gap) for gap in field_gaps)) / total
        priority = (
            weights.blocking_gap * resolved_ratio
            + weights.dependency * dependency_ratio
            - weights.user_cost * cost
            - weights.sensitivity * sensitivity
        )
        targets = [target]
        questions.append(
            ClarificationQuestion(
                id=_question_id(revision, answer_type, targets, choices),
                result_id=result_id,
                revision=revision,
                text=text,
                targets=targets,
                answer_type=answer_type,
                choices=choices,
                priority=round(priority, 6),
                reason_code="QUESTION-RESOLVE-BLOCKING-GAP",
            )
        )
    ordered = sorted(questions, key=lambda question: (-question.priority, question.targets, question.id))
    if custom_ranker is not None:
        try:
            ranked = custom_ranker.rank(list(ordered), list(gaps))
        except Exception as exc:
            diagnostic = Diagnostic(
                code="E-EXTENSION-QUESTION-RANKER",
                severity="error",
                phase="question_planning",
                message="A registered question ranker failed.",
                contract_ref=f"{contract.id}@{contract.version}",
            )
            raise InternalCompilerError(diagnostic.message, [diagnostic]) from exc
        if (
            not isinstance(ranked, list)
            or any(not isinstance(item, ClarificationQuestion) for item in ranked)
            or sorted(item.id for item in ranked) != sorted(item.id for item in ordered)
        ):
            diagnostic = Diagnostic(
                code="E-EXTENSION-QUESTION-RANKER-RESULT",
                severity="error",
                phase="question_planning",
                message="A question ranker must return a permutation of all planned questions.",
                contract_ref=f"{contract.id}@{contract.version}",
            )
            raise InternalCompilerError(diagnostic.message, [diagnostic])
        ordered = ranked
    return ordered[: config.max_questions_per_round], diagnostics


__all__ = ["candidate_question", "plan_questions"]
