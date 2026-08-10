from __future__ import annotations

from collections import defaultdict

from taskc.config import CompilerConfig
from taskc.models import ClarificationQuestion, Diagnostic, Gap
from taskc.models.capability import CapabilityContract, InputSpec


def _answer_type(spec: InputSpec) -> str:
    if spec.type == "enum":
        return "single_choice"
    if spec.type == "boolean":
        return "boolean"
    if spec.type in {"integer", "number"}:
        return "number"
    if spec.type == "array":
        return "multi_choice" if spec.enum else "text"
    return "text"


def _default_text(field_name: str, spec: InputSpec) -> str:
    if spec.type == "enum":
        return f"Which value should be used for {field_name}?"
    if spec.type == "boolean":
        return f"Should {field_name} be enabled?"
    if spec.type in {"integer", "number"}:
        return f"What numeric value should be used for {field_name}?"
    return f"What value should be used for {field_name}?"


def candidate_question(capability_ids: list[str], config: CompilerConfig) -> ClarificationQuestion:
    count = len(capability_ids)
    weights = config.question_weights
    elimination = (count - 1) / count
    priority = weights.candidate_elimination * elimination + weights.blocking_gap - weights.user_cost * 0.2
    return ClarificationQuestion(
        id="q-capability",
        text="Which capability best matches the task objective?",
        targets=["capability"],
        answer_type="single_choice",
        choices=capability_ids,
        priority=round(priority, 6),
        reason="Selecting a capability removes task-level ambiguity.",
    )


def plan_questions(
    gaps: list[Gap],
    contract: CapabilityContract,
    config: CompilerConfig,
    *,
    has_host_renderer: bool = False,
) -> tuple[list[ClarificationQuestion], list[Diagnostic]]:
    blocking = [gap for gap in gaps if gap.blocking]
    by_field: dict[str, list[Gap]] = defaultdict(list)
    diagnostics: list[Diagnostic] = []
    for gap in blocking:
        if gap.field_path and gap.field_path.startswith("inputs."):
            by_field[gap.field_path.removeprefix("inputs.")].append(gap)
    questions: list[ClarificationQuestion] = []
    total = max(1, len(blocking))
    for field_name, field_gaps in sorted(by_field.items()):
        spec = contract.all_inputs[field_name]
        if spec.sensitive and spec.question_template is None and not has_host_renderer:
            diagnostics.append(
                Diagnostic(
                    code="E-INPUT-SENSITIVE-TEMPLATE",
                    severity="error",
                    message="Sensitive fields require a contract question template or host renderer.",
                    field_path=f"inputs.{field_name}",
                    contract_id=contract.id,
                )
            )
            continue
        choices = list(spec.enum or [])
        for gap in field_gaps:
            if gap.kind == "conflicting":
                choices = gap.candidate_values
                break
        dependent = sum(
            1
            for requirement in contract.conditional_requirements
            if field_name in requirement.when.referenced_fields()
        )
        answer_type = _answer_type(spec)
        cost = 0.2 if answer_type in {"single_choice", "boolean", "number"} else 0.5
        sensitivity = 1.0 if spec.sensitive else 0.0
        weights = config.question_weights
        priority = (
            weights.blocking_gap * (len(field_gaps) / total)
            + weights.dependency * dependent
            - weights.user_cost * cost
            - weights.sensitivity * sensitivity
        )
        questions.append(
            ClarificationQuestion(
                id=f"q-{field_name.replace('_', '-')}",
                text=spec.question_template or _default_text(field_name, spec),
                targets=[f"inputs.{field_name}"],
                answer_type=answer_type,
                choices=choices,
                priority=round(priority, 6),
                reason=f"Resolves {len(field_gaps)} blocking gap(s) for {field_name}.",
            )
        )
    ordered = sorted(questions, key=lambda question: (-question.priority, question.id))
    return ordered[: config.max_questions_per_round], diagnostics


__all__ = ["candidate_question", "plan_questions"]

