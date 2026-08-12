from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from taskc.compiler import CompilerPipeline, normalize_request
from taskc.compiler.pipeline import random_id, utc_now
from taskc.config import CompilerConfig
from taskc.contracts import CapabilityCatalog, load_catalog, load_catalog_objects
from taskc.extensions import ExtensionRegistry
from taskc.models import (
    AnswerRecord,
    CapabilityRef,
    ClarificationAnswer,
    ClarificationQuestion,
    CompilationResult,
    CompilationSession,
    CompileEnvelope,
    ContractValidationError,
    Diagnostic,
    InvalidInputError,
    NormalizedRequest,
    SourcedValue,
    StaleSessionError,
    TaskCompilerError,
    InternalCompilerError,
)
from taskc.providers import InterpretationProvider, QuestionRenderer, StaticInterpretationProvider
from taskc.security import SecretResolver
from taskc.session import InMemorySessionStore, SessionStore
from taskc.telemetry import TelemetryRecorder


def _contract_fingerprints(catalog: CapabilityCatalog) -> dict[str, str]:
    result: dict[str, str] = {}
    for contract in catalog.contracts:
        payload = json.dumps(
            contract.model_dump(mode="json", by_alias=True, exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result[f"{contract.id}@{contract.version}"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result


def _contract_snapshots(catalog: CapabilityCatalog) -> dict[str, Any]:
    return {
        f"{contract.id}@{contract.version}": contract.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_unset=True
        )
        for contract in catalog.contracts
    }


def _optional_addition_only(previous, current) -> bool:
    previous_payload = dict(previous)
    current_payload = current.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude_unset=True
    )
    previous_optional = previous_payload.pop("optional_inputs", {})
    current_optional = current_payload.pop("optional_inputs", {})
    return previous_payload == current_payload and all(
        name in current_optional and current_optional[name] == spec
        for name, spec in previous_optional.items()
    )


def _extension_error(code: str, message: str, contract_ref: str) -> ContractValidationError:
    diagnostic = Diagnostic(
        code=code,
        severity="error",
        phase="extension_validation",
        message=message,
        contract_ref=contract_ref,
    )
    return ContractValidationError(message, [diagnostic])


def _apply_catalog_classifications(
    request: NormalizedRequest, catalog: CapabilityCatalog
) -> None:
    rank = {"public": 0, "sensitive": 1, "secret": 2}
    classifications: dict[str, str] = {}
    for contract in catalog.contracts:
        for field_name, spec in contract.all_inputs.items():
            current = classifications.get(field_name, "public")
            if rank[spec.classification] > rank[current]:
                classifications[field_name] = spec.classification
    for field_name, sourced in request.context.items():
        classification = classifications.get(field_name)
        if classification is not None:
            effective = (
                "sensitive"
                if classification == "secret" and sourced.value_ref is None
                else classification
            )
            request.context[field_name] = sourced.model_copy(
                update={"classification": effective}
            )
    for field_name, values in request.answers.items():
        classification = classifications.get(field_name)
        if classification is None:
            continue
        request.answers[field_name] = [
            item.model_copy(
                update={
                    "classification": (
                        "sensitive"
                        if classification == "secret" and item.value_ref is None
                        else classification
                    )
                }
            )
            for item in values
        ]


def _input_error(code: str, message: str, *, field_path: str | None = None) -> InvalidInputError:
    diagnostic = Diagnostic(
        code=code,
        severity="error",
        phase="session",
        message=message,
        field_path=field_path,
    )
    return InvalidInputError(message, [diagnostic])


class TaskCompiler:
    def __init__(
        self,
        *,
        catalog: CapabilityCatalog,
        interpretation_provider: InterpretationProvider | None = None,
        question_renderer: QuestionRenderer | None = None,
        secret_resolver: SecretResolver | None = None,
        extension_registry: ExtensionRegistry | None = None,
        config: CompilerConfig | None = None,
        session_store: SessionStore | None = None,
        telemetry: TelemetryRecorder | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_generator: Callable[[], str] = random_id,
    ) -> None:
        self.catalog = catalog
        self.config = config or CompilerConfig()
        self.session_store = session_store or InMemorySessionStore()
        self.clock = clock
        self.id_generator = id_generator
        self.extension_registry = extension_registry or ExtensionRegistry()
        self._validate_extensions()
        provider = interpretation_provider or StaticInterpretationProvider(self.config)
        self.pipeline = CompilerPipeline(
            catalog=catalog,
            interpretation_provider=provider,
            question_renderer=question_renderer,
            secret_resolver=secret_resolver,
            extension_registry=self.extension_registry,
            config=self.config,
            telemetry=telemetry,
            clock=clock,
            id_generator=id_generator,
        )
        self.last_session_id: str | None = None

    @classmethod
    def from_contract_paths(
        cls,
        paths: Iterable[str | Path],
        *,
        config: CompilerConfig | None = None,
        **kwargs: Any,
    ) -> "TaskCompiler":
        return cls(catalog=load_catalog(paths), config=config, **kwargs)

    @classmethod
    def from_contracts(
        cls,
        contracts: Iterable[dict[str, Any]],
        *,
        config: CompilerConfig | None = None,
        **kwargs: Any,
    ) -> "TaskCompiler":
        return cls(catalog=load_catalog_objects(contracts), config=config, **kwargs)

    def compile(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        requested_capability: CapabilityRef | dict[str, str] | None = None,
    ) -> CompilationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.compile_async(
                    request=request,
                    context=context,
                    requested_capability=requested_capability,
                )
            )
        raise RuntimeError("TaskCompiler.compile() cannot run inside an event loop; use compile_async().")

    async def compile_async(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        requested_capability: CapabilityRef | dict[str, str] | None = None,
    ) -> CompilationResult:
        normalized = normalize_request(request, context, self.config, requested_capability)
        _apply_catalog_classifications(normalized, self.catalog)
        session_id = self.id_generator()
        result = await self.pipeline.compile(normalized, session_id=session_id, revision=0)
        now = self.clock()
        session = CompilationSession(
            schema_version="0.2",
            session_id=session_id,
            revision=0,
            request=normalized,
            catalog_digest=self.catalog.digest,
            config_digest=self.config.digest,
            provider_fingerprints=[self.pipeline.provider_fingerprint],
            contract_fingerprints=_contract_fingerprints(self.catalog),
            contract_snapshots=_contract_snapshots(self.catalog),
            extension_fingerprints=self.extension_registry.fingerprints,
            answers=[],
            last_result=result,
            created_at=now,
            updated_at=now,
        )
        self.session_store.put(session, expected_revision=-1)
        self.last_session_id = session_id
        return result

    def try_compile(
        self,
        request: str,
        context: dict[str, Any] | None = None,
        *,
        requested_capability: CapabilityRef | dict[str, str] | None = None,
    ) -> CompileEnvelope:
        try:
            return CompileEnvelope(
                schema_version="0.2",
                kind="result",
                serialization_profile="public",
                result=self.compile(
                    request,
                    context,
                    requested_capability=requested_capability,
                ),
            )
        except TaskCompilerError as exc:
            return CompileEnvelope(
                schema_version="0.2",
                kind="error",
                serialization_profile="public",
                error=exc.error,
            )
        except Exception as exc:
            diagnostic = Diagnostic(
                code="E-INTERNAL",
                severity="error",
                phase="api",
                message="Compilation failed because of an internal error.",
            )
            error = InternalCompilerError(diagnostic.message, [diagnostic])
            return CompileEnvelope(
                schema_version="0.2",
                kind="error",
                serialization_profile="public",
                error=error.error,
            )

    def continue_(
        self,
        session_id: str,
        expected_revision: int,
        answers: Sequence[ClarificationAnswer | dict[str, Any]],
    ) -> CompilationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.continue_async(
                    session_id=session_id,
                    expected_revision=expected_revision,
                    answers=answers,
                )
            )
        raise RuntimeError("TaskCompiler.continue_() cannot run inside an event loop; use continue_async().")

    async def continue_async(
        self,
        session_id: str,
        expected_revision: int,
        answers: Sequence[ClarificationAnswer | dict[str, Any]],
    ) -> CompilationResult:
        session = self.session_store.get(session_id)
        if session is None:
            raise _input_error("E-INPUT-UNKNOWN-SESSION", "Unknown compilation session.")
        if session.revision != expected_revision:
            diagnostic = Diagnostic(
                code="E-SESSION-STALE",
                severity="error",
                phase="session",
                message="Session revision does not match expected_revision.",
            )
            raise StaleSessionError(diagnostic.message, [diagnostic])
        compatibility_diagnostic = self._validate_session_environment(session)
        parsed_answers = [
            item if isinstance(item, ClarificationAnswer) else ClarificationAnswer.model_validate(item)
            for item in answers
        ]
        if not parsed_answers:
            raise _input_error("E-INPUT-EMPTY-ANSWERS", "At least one clarification answer is required.")
        ids = [item.question_id for item in parsed_answers]
        if len(ids) != len(set(ids)):
            raise _input_error(
                "E-INPUT-DUPLICATE-ANSWER", "A clarification batch cannot answer a question twice."
            )
        questions = {question.id: question for question in session.last_result.questions}
        request = session.request.model_copy(deep=True)
        accepted_records: list[AnswerRecord] = []
        for answer in parsed_answers:
            if answer.redacted:
                raise _input_error(
                    "E-INPUT-REDACTED-ANSWER",
                    "A redacted answer cannot be submitted for compilation.",
                )
            question = questions.get(answer.question_id)
            if question is None:
                raise _input_error(
                    "E-INPUT-UNKNOWN-QUESTION",
                    "Clarification answer references an unknown question.",
                    field_path=answer.question_id,
                )
            if answer.result_id != question.result_id or answer.revision != question.revision:
                diagnostic = Diagnostic(
                    code="E-SESSION-STALE-ANSWER",
                    severity="error",
                    phase="session",
                    message="Clarification answer references a stale result or revision.",
                    field_path=answer.question_id,
                )
                raise StaleSessionError(diagnostic.message, [diagnostic])
            self._validate_answer(question, answer)
            record_classification = "public"
            for target in question.targets:
                if target == "capability":
                    capability_id, separator, version = str(answer.value).rpartition("@")
                    if not separator or self.catalog.get(capability_id, version) is None:
                        raise _input_error(
                            "E-INPUT-CAPABILITY-REF",
                            "Capability answer must reference an exact catalog version.",
                        )
                    request.requested_capability = CapabilityRef(id=capability_id, version=version)
                elif target.startswith("inputs."):
                    field_name = target.removeprefix("inputs.")
                    selected = session.last_result.selected_candidate
                    if selected is None:
                        raise _input_error(
                            "E-INPUT-QUESTION-TARGET", "Input question has no selected capability."
                        )
                    contract = self.catalog.get(selected.capability_id, selected.capability_version)
                    if contract is None or field_name not in contract.all_inputs:
                        raise _input_error(
                            "E-INPUT-QUESTION-TARGET", "Question target is not in the current contract."
                        )
                    spec = contract.all_inputs[field_name]
                    if spec.classification == "secret" or (
                        spec.classification == "sensitive" and record_classification == "public"
                    ):
                        record_classification = spec.classification
                    if spec.classification == "secret" and answer.value_ref is None:
                        raise _input_error(
                            "E-INPUT-SECRET-LITERAL",
                            "Secret clarification answers must use value_ref.",
                        )
                    resolving_conflict = any(
                        gap.kind == "conflicting" and target in gap.field_paths
                        for gap in session.last_result.gaps
                    )
                    if resolving_conflict:
                        request.answers[field_name] = []
                    request.answers.setdefault(field_name, []).append(
                        SourcedValue(
                            **(
                                {"value_ref": answer.value_ref}
                                if answer.value_ref is not None
                                else {"value": answer.value}
                            ),
                            source="clarification_answer",
                            source_ref=f"answer:{answer.question_id}:{session.revision + 1}",
                            confidence=1.0,
                            classification=spec.classification,
                        )
                    )
                elif target == "request.unresolved_terms":
                    if not isinstance(answer.value, str):
                        raise _input_error(
                            "E-INPUT-ANSWER-TYPE", "Unresolved term clarification must be text."
                        )
                    addition = unicodedata.normalize("NFC", answer.value).strip()
                    request.raw_text = f"{request.raw_text}\nClarification: {addition}"
                    request.text = f"{request.text}\nClarification: {addition}"
                else:
                    raise _input_error(
                        "E-INPUT-QUESTION-TARGET", "Question contains an unsupported target."
                    )
            accepted_records.append(
                AnswerRecord(
                    answer=answer,
                    targets=question.targets,
                    classification=record_classification,
                    accepted_at=self.clock(),
                )
            )

        next_revision = session.revision + 1
        _apply_catalog_classifications(request, self.catalog)
        result = await self.pipeline.compile(
            request,
            session_id=session_id,
            revision=next_revision,
        )
        if compatibility_diagnostic is not None:
            result.diagnostics.append(compatibility_diagnostic)
        session.request = request
        session.revision = next_revision
        session.catalog_digest = self.catalog.digest
        session.config_digest = self.config.digest
        session.provider_fingerprints = [self.pipeline.provider_fingerprint]
        session.contract_fingerprints = _contract_fingerprints(self.catalog)
        session.contract_snapshots = _contract_snapshots(self.catalog)
        session.extension_fingerprints = self.extension_registry.fingerprints
        session.answers.extend(accepted_records)
        session.last_result = result
        session.updated_at = self.clock()
        self.session_store.put(session, expected_revision=expected_revision)
        self.last_session_id = session_id
        return result

    def get_session(self, session_id: str | None = None) -> CompilationSession | None:
        effective_id = session_id or self.last_session_id
        return self.session_store.get(effective_id) if effective_id else None

    def restart_from_session(self, session_id: str) -> CompilationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.restart_from_session_async(session_id))
        raise RuntimeError(
            "TaskCompiler.restart_from_session() cannot run inside an event loop; "
            "use restart_from_session_async()."
        )

    async def restart_from_session_async(self, session_id: str) -> CompilationResult:
        previous = self.session_store.get(session_id)
        if previous is None:
            raise _input_error("E-INPUT-UNKNOWN-SESSION", "Unknown compilation session.")
        request = previous.request.model_copy(deep=True)
        request.context = {
            field_name: value
            for field_name, value in request.context.items()
            if not value.redacted
        }
        declared = {
            field_name
            for contract in self.catalog.contracts
            for field_name in contract.all_inputs
        }
        request.answers = {
            field_name: [value for value in values if not value.redacted]
            for field_name, values in request.answers.items()
            if field_name in declared and any(not value.redacted for value in values)
        }
        if (
            request.requested_capability is not None
            and self.catalog.get(
                request.requested_capability.id,
                request.requested_capability.version,
            )
            is None
        ):
            request.requested_capability = None
        _apply_catalog_classifications(request, self.catalog)
        new_session_id = self.id_generator()
        result = await self.pipeline.compile(
            request,
            session_id=new_session_id,
            revision=0,
        )
        now = self.clock()
        retained_answers = [
            record.model_copy(deep=True)
            for record in previous.answers
            if not record.answer.redacted
            and all(
                target == "request.unresolved_terms"
                or (
                    target.startswith("inputs.")
                    and target.removeprefix("inputs.") in declared
                )
                for target in record.targets
            )
        ]
        restarted = CompilationSession(
            schema_version="0.2",
            session_id=new_session_id,
            revision=0,
            request=request,
            catalog_digest=self.catalog.digest,
            config_digest=self.config.digest,
            provider_fingerprints=[self.pipeline.provider_fingerprint],
            contract_fingerprints=_contract_fingerprints(self.catalog),
            contract_snapshots=_contract_snapshots(self.catalog),
            extension_fingerprints=self.extension_registry.fingerprints,
            answers=retained_answers,
            last_result=result,
            created_at=now,
            updated_at=now,
        )
        self.session_store.put(restarted, expected_revision=-1)
        self.last_session_id = new_session_id
        return result

    def _validate_session_environment(
        self, session: CompilationSession
    ) -> Diagnostic | None:
        if session.config_digest != self.config.digest:
            raise self._stale_environment(
                "E-SESSION-CONFIG-CHANGED", "Compiler configuration changed during the session."
            )
        if session.provider_fingerprints != [self.pipeline.provider_fingerprint]:
            raise self._stale_environment(
                "E-SESSION-PROVIDER-CHANGED", "Interpretation provider changed during the session."
            )
        if session.extension_fingerprints != self.extension_registry.fingerprints:
            raise self._stale_environment(
                "E-SESSION-EXTENSIONS-CHANGED", "Registered extensions changed during the session."
            )
        if session.catalog_digest == self.catalog.digest:
            return None
        selected = session.last_result.selected_candidate
        if selected is None:
            diagnostic = Diagnostic(
                code="E-SESSION-CATALOG-CHANGED",
                severity="error",
                phase="session",
                message="Catalog changed while the session had no stable selected capability.",
            )
            raise StaleSessionError(diagnostic.message, [diagnostic])
        ref = f"{selected.capability_id}@{selected.capability_version}"
        current = _contract_fingerprints(self.catalog).get(ref)
        current_contract = self.catalog.get(selected.capability_id, selected.capability_version)
        previous_contract = session.contract_snapshots.get(ref)
        if (
            current is None
            or not isinstance(previous_contract, dict)
            or (
                current != session.contract_fingerprints.get(ref)
                and not _optional_addition_only(previous_contract, current_contract)
            )
        ):
            diagnostic = Diagnostic(
                code="E-SESSION-CONTRACT-CHANGED",
                severity="error",
                phase="session",
                message="Selected capability contract changed incompatibly.",
                contract_ref=ref,
            )
            raise StaleSessionError(diagnostic.message, [diagnostic])
        return Diagnostic(
            code="W-SESSION-CATALOG-COMPATIBLE",
            severity="warning",
            phase="session",
            message="Catalog changed, but the selected contract remains compatible.",
            contract_ref=ref,
        )

    @staticmethod
    def _stale_environment(code: str, message: str) -> StaleSessionError:
        diagnostic = Diagnostic(
            code=code,
            severity="error",
            phase="session",
            message=message,
        )
        return StaleSessionError(message, [diagnostic])

    def _validate_extensions(self) -> None:
        for contract in self.catalog.contracts:
            ref = f"{contract.id}@{contract.version}"
            references = [
                (constraint.evaluator, self.extension_registry.constraint_evaluators)
                for constraint in contract.constraints
                if constraint.evaluator is not None
            ]
            if contract.question_ranker is not None:
                references.append(
                    (contract.question_ranker, self.extension_registry.question_rankers)
                )
            for name, collection in references:
                assert name is not None
                if name not in collection:
                    raise _extension_error(
                        "E-CONTRACT-EXTENSION-UNKNOWN",
                        f"Contract references an unregistered extension: {name}.",
                        ref,
                    )
                descriptor, _ = collection[name]
                if (
                    not descriptor.deterministic
                    and not self.config.allow_nondeterministic_extensions
                ):
                    raise _extension_error(
                        "E-CONTRACT-EXTENSION-NONDETERMINISTIC",
                        "A non-deterministic extension requires explicit host opt-in.",
                        ref,
                    )

    @staticmethod
    def _validate_answer(question: ClarificationQuestion, answer: ClarificationAnswer) -> None:
        if answer.value_ref is not None:
            return
        value = answer.value
        valid = True
        if question.answer_type == "text":
            valid = isinstance(value, str) and bool(value.strip())
        elif question.answer_type == "single_choice":
            valid = value in question.choices
        elif question.answer_type == "multi_choice":
            valid = isinstance(value, list) and (
                not question.choices or all(item in question.choices for item in value)
            )
        elif question.answer_type == "boolean":
            valid = isinstance(value, bool)
        elif question.answer_type == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif question.answer_type == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif question.answer_type == "json":
            valid = isinstance(value, (list, dict))
        if not valid:
            raise _input_error(
                "E-INPUT-ANSWER-TYPE",
                "Clarification answer does not match the question type or choices.",
                field_path=question.id,
            )


__all__ = ["TaskCompiler"]
