from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from taskc.config import CompilerConfig
from taskc.contracts import CapabilityCatalog
from taskc.models import (
    ClarificationQuestion,
    CompilationResult,
    Diagnostic,
    ExtractionResult,
    Gap,
    NormalizedRequest,
    ProviderError,
)
from taskc.providers import CandidateProvider, ExtractionProvider, QuestionRenderer
from taskc.providers.static import StaticExtractionProvider
from taskc.telemetry import NullTelemetryRecorder, TelemetryEvent, TelemetryRecorder

from .matching import CandidateAnalysis, analyze_candidate
from .questions import candidate_question, plan_questions
from .readiness import build_intent, is_ready


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def random_id() -> str:
    return str(uuid4())


class CompilerPipeline:
    def __init__(
        self,
        *,
        catalog: CapabilityCatalog,
        candidate_provider: CandidateProvider,
        extraction_provider: ExtractionProvider,
        config: CompilerConfig,
        question_renderer: QuestionRenderer | None = None,
        telemetry: TelemetryRecorder | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_generator: Callable[[], str] = random_id,
    ) -> None:
        self.catalog = catalog
        self.candidate_provider = candidate_provider
        self.extraction_provider = extraction_provider
        self.config = config
        self.question_renderer = question_renderer
        self.telemetry = telemetry or NullTelemetryRecorder()
        self.clock = clock
        self.id_generator = id_generator
        self._static_extractor = StaticExtractionProvider()

    def _event(self, name: str, request_id: str, **kwargs: object) -> None:
        self.telemetry.record(
            TelemetryEvent(name=name, occurred_at=self.clock(), request_id=request_id, **kwargs)
        )

    async def compile(self, request: NormalizedRequest, *, session_id: str | None = None) -> CompilationResult:
        request_id = self.id_generator()
        self._event("compilation.started", request_id, session_id=session_id)
        self._event("contracts.validated", request_id, metadata={"catalog_digest": self.catalog.digest})
        try:
            drafts = await self.candidate_provider.generate(
                request, self.catalog, self.config.max_candidates
            )
        except ProviderError as exc:
            self._event(
                "provider.failed",
                request_id,
                error_code=exc.diagnostics[0].code if exc.diagnostics else "E-PROVIDER-UNKNOWN",
            )
            return self._unsupported(exc.diagnostics)
        except Exception:
            diagnostic = self._unexpected_provider_diagnostic()
            self._event("provider.failed", request_id, error_code=diagnostic.code)
            return self._unsupported([diagnostic])

        diagnostics: list[Diagnostic] = []
        valid_drafts = []
        seen: set[tuple[str, str]] = set()
        for draft in drafts[: self.config.max_candidates]:
            contract = self.catalog.get(draft.capability_id, draft.capability_version)
            if contract is None:
                diagnostics.append(
                    Diagnostic(
                        code="E-PROVIDER-UNKNOWN-CAPABILITY",
                        severity="error",
                        message="Provider returned a capability outside the current catalog.",
                    )
                )
                continue
            key = (contract.id, contract.version)
            if key not in seen:
                seen.add(key)
                valid_drafts.append(draft.model_copy(update={"capability_version": contract.version}))
        self._event("candidates.generated", request_id, candidate_count=len(valid_drafts))
        if not valid_drafts:
            return self._unsupported(diagnostics)

        analyses: list[CandidateAnalysis] = []
        for draft in valid_drafts:
            contract = self.catalog.get(draft.capability_id, draft.capability_version)
            assert contract is not None
            try:
                base = await self._static_extractor.extract(request, contract, draft)
                if isinstance(self.extraction_provider, StaticExtractionProvider):
                    extraction = base
                else:
                    provider_extraction = await self.extraction_provider.extract(request, contract, draft)
                    extraction = self._merge_extractions(base, provider_extraction)
            except ProviderError as exc:
                diagnostics.extend(exc.diagnostics)
                self._event(
                    "provider.failed",
                    request_id,
                    capability_id=contract.id,
                    error_code=exc.diagnostics[0].code if exc.diagnostics else "E-PROVIDER-UNKNOWN",
                )
                continue
            except Exception:
                diagnostic = self._unexpected_provider_diagnostic(contract.id)
                diagnostics.append(diagnostic)
                self._event(
                    "provider.failed",
                    request_id,
                    capability_id=contract.id,
                    error_code=diagnostic.code,
                )
                continue
            analysis = analyze_candidate(contract, draft, extraction, self.config)
            analyses.append(analysis)
            diagnostics.extend(analysis.diagnostics)
            self._event("fields.extracted", request_id, capability_id=contract.id)
        if not analyses:
            return self._unsupported(diagnostics)

        analyses.sort(
            key=lambda item: (
                -item.candidate.score,
                item.candidate.capability_id,
                item.candidate.capability_version,
            )
        )
        if len(analyses) > 1 and not self._can_auto_select(analyses):
            candidates = [item.candidate for item in analyses]
            gap = Gap(
                code="GAP-AMBIGUOUS-CAPABILITY",
                kind="ambiguous",
                field_path=None,
                message="Multiple capabilities can plausibly handle this request.",
                blocking=True,
                candidate_values=[item.capability_id for item in candidates],
                suggested_resolution="choose_candidate",
            )
            question = candidate_question([item.capability_id for item in candidates], self.config)
            questions = [question][: self.config.max_questions_per_round]
            self._event("gaps.computed", request_id, gap_counts={"ambiguous": 1})
            self._event("questions.planned", request_id, question_count=len(questions))
            self._event("compilation.blocked", request_id)
            return CompilationResult(
                status="ambiguous",
                candidates=candidates,
                gaps=[gap],
                questions=questions,
                diagnostics=diagnostics,
            )

        selected = analyses[0]
        gap_counts = dict(Counter(gap.kind for gap in selected.gaps))
        self._event(
            "gaps.computed",
            request_id,
            capability_id=selected.contract.id,
            gap_counts=gap_counts,
        )
        if is_ready(selected.candidate, selected.gaps, self.catalog):
            intent = build_intent(
                selected.candidate, self.catalog, self.clock, self.id_generator
            )
            self._event("compilation.ready", request_id, capability_id=selected.contract.id)
            return CompilationResult(
                status="ready",
                candidates=[item.candidate for item in analyses],
                selected_candidate=selected.candidate,
                gaps=selected.gaps,
                intent=intent,
                diagnostics=diagnostics,
            )

        questions, question_diagnostics = plan_questions(
            selected.gaps,
            selected.contract,
            self.config,
            has_host_renderer=self.question_renderer is not None,
        )
        diagnostics.extend(question_diagnostics)
        if self.question_renderer is not None:
            try:
                questions = await self._render_questions(questions, selected.contract)
            except Exception:
                diagnostics.append(
                    Diagnostic(
                        code="E-PROVIDER-QUESTION-RENDERER",
                        severity="error",
                        message="Question renderer failed or returned invalid output.",
                        contract_id=selected.contract.id,
                    )
                )
                questions = []
        status = (
            "conflicting"
            if any(gap.kind == "conflicting" and gap.blocking for gap in selected.gaps)
            else "needs_clarification"
        )
        self._event("questions.planned", request_id, question_count=len(questions))
        self._event("compilation.blocked", request_id, capability_id=selected.contract.id)
        return CompilationResult(
            status=status,
            candidates=[item.candidate for item in analyses],
            selected_candidate=selected.candidate,
            gaps=selected.gaps,
            questions=questions,
            diagnostics=diagnostics,
        )

    async def _render_questions(self, questions, contract):
        rendered = []
        for question in questions:
            field_name = question.targets[0].removeprefix("inputs.")
            spec = contract.all_inputs[field_name]
            text = await self.question_renderer.render(
                text=question.text,
                field_path=question.targets[0],
                sensitive=spec.sensitive,
            )
            rendered.append(
                ClarificationQuestion.model_validate(
                    {**question.model_dump(mode="python"), "text": text}
                )
            )
        return rendered

    def _merge_extractions(self, base: ExtractionResult, provided: ExtractionResult) -> ExtractionResult:
        values = {field: list(items) for field, items in base.values.items()}
        for field, items in provided.values.items():
            values.setdefault(field, []).extend(items)
        return ExtractionResult(
            values=values,
            unresolved_terms=sorted(set(base.unresolved_terms + provided.unresolved_terms)),
        )

    def _can_auto_select(self, analyses: list[CandidateAnalysis]) -> bool:
        if not self.config.auto_select_candidate or len(analyses) < 2:
            return False
        return (
            analyses[0].candidate.score >= self.config.auto_select_threshold
            and analyses[0].candidate.score - analyses[1].candidate.score
            >= self.config.auto_select_margin
        )

    @staticmethod
    def _unsupported(diagnostics: list[Diagnostic]) -> CompilationResult:
        return CompilationResult(
            status="unsupported",
            gaps=[
                Gap(
                    code="GAP-UNSUPPORTED-NO-CAPABILITY",
                    kind="unsupported",
                    field_path=None,
                    message="No valid capability matched the request.",
                    blocking=True,
                    suggested_resolution="reject",
                )
            ],
            diagnostics=diagnostics,
        )

    @staticmethod
    def _unexpected_provider_diagnostic(contract_id: str | None = None) -> Diagnostic:
        return Diagnostic(
            code="E-PROVIDER-FAILURE",
            severity="error",
            message="Provider failed without a safe structured diagnostic.",
            contract_id=contract_id,
        )


__all__ = ["CompilerPipeline", "random_id", "utc_now"]
