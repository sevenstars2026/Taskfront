from __future__ import annotations

from taskc.models import CompilationResult, Diagnostic


def render_diagnostic(diagnostic: Diagnostic) -> str:
    location = f" [{diagnostic.field_path}]" if diagnostic.field_path else ""
    contract = f" ({diagnostic.contract_id})" if diagnostic.contract_id else ""
    hint = f" Hint: {diagnostic.hint}" if diagnostic.hint else ""
    return f"{diagnostic.code}{location}{contract}: {diagnostic.message}{hint}"


def render_result(result: CompilationResult, *, session_id: str | None = None) -> str:
    lines = [result.status.upper().replace("_", " ")]
    if result.candidates:
        lines.extend(["", "Candidates:"])
        for index, candidate in enumerate(result.candidates, start=1):
            lines.append(
                f"  {index}. {candidate.capability_id}@{candidate.capability_version} "
                f"(score={candidate.score:.3f})"
            )
    if result.intent is not None:
        lines.extend(["", f"Intent: {result.intent.intent_id}"])
    if result.gaps:
        lines.extend(["", "Gaps:"])
        for gap in result.gaps:
            field = f" [{gap.field_path}]" if gap.field_path else ""
            lines.append(f"  {gap.code}{field}: {gap.message}")
    if result.questions:
        lines.extend(["", "Questions:"])
        for question in result.questions:
            choices = f" Choices: {', '.join(map(str, question.choices))}" if question.choices else ""
            lines.append(f"  {question.id}: {question.text}{choices}")
    if result.diagnostics:
        lines.extend(["", "Diagnostics:"])
        lines.extend(f"  {render_diagnostic(item)}" for item in result.diagnostics)
    if session_id:
        lines.extend(["", f"Session: {session_id}"])
    return "\n".join(lines)


__all__ = ["render_diagnostic", "render_result"]

