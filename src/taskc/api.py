from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from taskc.compiler import CompilerPipeline, normalize_request
from taskc.compiler.pipeline import random_id, utc_now
from taskc.config import CompilerConfig
from taskc.contracts import CapabilityCatalog, load_catalog, load_catalog_objects
from taskc.models import (
    ClarificationAnswer,
    ClarificationQuestion,
    CompilationResult,
    CompilationSession,
    Diagnostic,
    InvalidInputError,
    NormalizedRequest,
    SourcedValue,
)
from taskc.providers import (
    CandidateProvider,
    ExtractionProvider,
    QuestionRenderer,
    StaticCandidateProvider,
    StaticExtractionProvider,
)
from taskc.session import InMemorySessionStore, SessionStore
from taskc.telemetry import TelemetryRecorder


class TaskCompiler:
    def __init__(
        self,
        *,
        catalog: CapabilityCatalog,
        candidate_provider: CandidateProvider | None = None,
        extraction_provider: ExtractionProvider | None = None,
        question_renderer: QuestionRenderer | None = None,
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
        self.pipeline = CompilerPipeline(
            catalog=catalog,
            candidate_provider=candidate_provider or StaticCandidateProvider(),
            extraction_provider=extraction_provider or StaticExtractionProvider(),
            question_renderer=question_renderer,
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
    ) -> CompilationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.compile_async(request=request, context=context))
        raise RuntimeError("TaskCompiler.compile() cannot run inside an event loop; use compile_async().")

    async def compile_async(
        self,
        request: str,
        context: dict[str, Any] | None = None,
    ) -> CompilationResult:
        normalized = normalize_request(request, context, self.config)
        session_id = self.id_generator()
        result = await self.pipeline.compile(normalized, session_id=session_id)
        now = self.clock()
        session = CompilationSession(
            session_id=session_id,
            request=normalized,
            catalog_digest=self.catalog.digest,
            answers=[],
            last_result=result,
            created_at=now,
            updated_at=now,
        )
        self.session_store.put(session)
        self.last_session_id = session_id
        return result

    def continue_(
        self,
        session_id: str,
        answers: Sequence[ClarificationAnswer | dict[str, Any]],
    ) -> CompilationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.continue_async(session_id=session_id, answers=answers))
        raise RuntimeError("TaskCompiler.continue_() cannot run inside an event loop; use continue_async().")

    async def continue_async(
        self,
        session_id: str,
        answers: Sequence[ClarificationAnswer | dict[str, Any]],
    ) -> CompilationResult:
        session = self.session_store.get(session_id)
        if session is None:
            raise InvalidInputError(
                "Unknown compilation session.",
                [
                    Diagnostic(
                        code="E-INPUT-UNKNOWN-SESSION",
                        severity="error",
                        message="Unknown compilation session.",
                    )
                ],
            )
        parsed_answers = [
            item if isinstance(item, ClarificationAnswer) else ClarificationAnswer.model_validate(item)
            for item in answers
        ]
        if not parsed_answers:
            raise InvalidInputError(
                "At least one clarification answer is required.",
                [
                    Diagnostic(
                        code="E-INPUT-EMPTY-ANSWERS",
                        severity="error",
                        message="At least one clarification answer is required.",
                    )
                ],
            )
        questions = {question.id: question for question in session.last_result.questions}
        request = session.request.model_copy(deep=True)
        for answer in parsed_answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise InvalidInputError(
                    "Clarification answer references an unknown question.",
                    [
                        Diagnostic(
                            code="E-INPUT-UNKNOWN-QUESTION",
                            severity="error",
                            message="Clarification answer references an unknown question.",
                            field_path=answer.question_id,
                        )
                    ],
                )
            self._validate_answer(question, answer)
            for target in question.targets:
                if target == "capability":
                    request.requested_capability = str(answer.value)
                elif target.startswith("inputs."):
                    field_name = target.removeprefix("inputs.")
                    resolving_conflict = any(
                        gap.kind == "conflicting" and gap.field_path == target
                        for gap in session.last_result.gaps
                    )
                    if resolving_conflict:
                        # The user is explicitly resolving a surfaced conflict. Historical
                        # answers stay in session.answers, while the effective request value
                        # becomes the selected resolution.
                        request.answers[field_name] = []
                    request.answers.setdefault(field_name, []).append(
                        SourcedValue(
                            value=answer.value,
                            source="clarification_answer",
                            source_ref=f"answer:{answer.question_id}:{len(session.answers)}",
                            confidence=1.0,
                        )
                    )
                else:
                    raise InvalidInputError(
                        "Question contains an unsupported target.",
                        [
                            Diagnostic(
                                code="E-INPUT-QUESTION-TARGET",
                                severity="error",
                                message="Question contains an unsupported target.",
                            )
                        ],
                    )
            session.answers.append(answer)

        catalog_changed = session.catalog_digest != self.catalog.digest
        result = await self.pipeline.compile(request, session_id=session_id)
        if catalog_changed:
            result = result.model_copy(
                update={
                    "diagnostics": [
                        *result.diagnostics,
                        Diagnostic(
                            code="E-CONTRACT-CATALOG-CHANGED",
                            severity="warning",
                            message="Catalog changed; candidates were recompiled against the current catalog.",
                        ),
                    ]
                }
            )
        session.request = request
        session.catalog_digest = self.catalog.digest
        session.last_result = result
        session.updated_at = self.clock()
        self.session_store.put(session)
        self.last_session_id = session_id
        return result

    def get_session(self, session_id: str | None = None) -> CompilationSession | None:
        effective_id = session_id or self.last_session_id
        return self.session_store.get(effective_id) if effective_id else None

    @staticmethod
    def _validate_answer(question: ClarificationQuestion, answer: ClarificationAnswer) -> None:
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
        elif question.answer_type == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not valid:
            raise InvalidInputError(
                "Clarification answer does not match the question type or choices.",
                [
                    Diagnostic(
                        code="E-INPUT-ANSWER-TYPE",
                        severity="error",
                        message="Clarification answer does not match the question type or choices.",
                        field_path=question.id,
                    )
                ],
            )


__all__ = ["TaskCompiler"]
