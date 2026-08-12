from __future__ import annotations

from taskc.models import CompilationResult, CompilationSession, SourcedValue


def session_contains_sensitive_literals(session: CompilationSession) -> bool:
    values = list(session.request.context.values())
    values.extend(item for items in session.request.answers.values() for item in items)
    for candidate in session.last_result.candidates:
        values.extend(candidate.inputs.values())
    if session.last_result.intent is not None:
        values.extend(session.last_result.intent.inputs.values())
    if any(
        item.classification == "sensitive"
        and item.value_ref is None
        and not item.redacted
        for item in values
    ):
        return True
    if any(
        record.classification == "sensitive"
        and not record.answer.redacted
        and record.answer.value_ref is None
        for record in session.answers
    ):
        return True
    for snapshot in session.contract_snapshots.values():
        for section in ("required_inputs", "optional_inputs"):
            definitions = snapshot.get(section, {})
            if not isinstance(definitions, dict):
                continue
            for spec in definitions.values():
                if (
                    isinstance(spec, dict)
                    and spec.get("classification") == "sensitive"
                    and "default" in spec
                ):
                    return True
    return False


def _redact_value(value: SourcedValue) -> SourcedValue:
    if value.classification == "public":
        return value
    return SourcedValue(
        source=value.source,
        source_ref=value.source_ref,
        confidence=value.confidence,
        classification=value.classification,
        redacted=True,
    )


def redact_result(result: CompilationResult) -> CompilationResult:
    payload = result.model_dump(mode="python", exclude_none=True)
    for candidate in payload.get("candidates", []):
        candidate["inputs"] = {
            key: _redact_value(SourcedValue.model_validate(value)).model_dump(
                mode="python", exclude_none=True
            )
            for key, value in candidate.get("inputs", {}).items()
        }
    selected = payload.get("selected_candidate")
    if selected is not None:
        selected["inputs"] = {
            key: _redact_value(SourcedValue.model_validate(value)).model_dump(
                mode="python", exclude_none=True
            )
            for key, value in selected.get("inputs", {}).items()
        }
    intent = payload.get("intent")
    if intent is not None:
        intent["inputs"] = {
            key: _redact_value(SourcedValue.model_validate(value)).model_dump(
                mode="python", exclude_none=True
            )
            for key, value in intent.get("inputs", {}).items()
        }
    for gap in payload.get("gaps", []):
        if any(
            field_path.removeprefix("inputs.") in {
                key for candidate in result.candidates for key, value in candidate.inputs.items()
                if value.classification != "public"
            }
            for field_path in gap.get("field_paths", [])
        ):
            gap["candidate_values"] = []
    return CompilationResult.model_validate(payload)


def redact_session(session: CompilationSession) -> CompilationSession:
    payload = session.model_dump(mode="python", exclude_none=True)
    for value in payload["request"].get("context", {}).values():
        parsed = SourcedValue.model_validate(value)
        value.clear()
        value.update(_redact_value(parsed).model_dump(mode="python", exclude_none=True))
    for values in payload["request"].get("answers", {}).values():
        for value in values:
            parsed = SourcedValue.model_validate(value)
            value.clear()
            value.update(_redact_value(parsed).model_dump(mode="python", exclude_none=True))
    for record in payload.get("answers", []):
        if record.get("classification") != "public":
            answer = record["answer"]
            record["answer"] = {
                "question_id": answer["question_id"],
                "result_id": answer["result_id"],
                "revision": answer["revision"],
                "redacted": True,
            }
    for snapshot in payload.get("contract_snapshots", {}).values():
        if not isinstance(snapshot, dict):
            continue
        for section in ("required_inputs", "optional_inputs"):
            definitions = snapshot.get(section, {})
            if not isinstance(definitions, dict):
                continue
            for spec in definitions.values():
                if (
                    isinstance(spec, dict)
                    and spec.get("classification", "public") != "public"
                ):
                    spec.pop("default", None)
    payload["last_result"] = redact_result(session.last_result).model_dump(
        mode="python", exclude_none=True
    )
    return CompilationSession.model_validate(payload)


__all__ = ["redact_result", "redact_session", "session_contains_sensitive_literals"]
