from __future__ import annotations

import asyncio
import warnings
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable
from uuid import uuid4

from taskc.budget import ProviderBudget
from taskc.config import CompilerConfig
from taskc.contracts import CapabilityCatalog
from taskc.extensions import ExtensionRegistry
from taskc.models import (
    BudgetExceededError,
    CandidateDraft,
    ClarificationQuestion,
    CompilationResult,
    Diagnostic,
    ExplainTrace,
    Gap,
    InternalCompilerError,
    NormalizedRequest,
    ProviderError,
    TraceStep,
)
from taskc.providers import InterpretationProvider, QuestionRenderer
from taskc.providers.static import StaticExtractionProvider
from taskc.security import SecretResolver
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
        interpretation_provider: InterpretationProvider,
        config: CompilerConfig,
        question_renderer: QuestionRenderer | None = None,
        secret_resolver: SecretResolver | None = None,
        extension_registry: ExtensionRegistry | None = None,
        telemetry: TelemetryRecorder | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_generator: Callable[[], str] = random_id,
    ) -> None:
        self.catalog = catalog
        self.interpretation_provider = interpretation_provider
        self.config = config
        self.question_renderer = question_renderer
        self.secret_resolver = secret_resolver
        self.extension_registry = extension_registry or ExtensionRegistry()
        self.telemetry = telemetry or NullTelemetryRecorder()
        self.clock = clock
        self.id_generator = id_generator
        self._static_extractor = StaticExtractionProvider()

    @property
    def provider_fingerprint(self) -> str:
        return (
            f"{self.interpretation_provider.provider_id}:"
            f"{self.interpretation_provider.config_version}:"
            f"{self.interpretation_provider.score_semantics}"
        )

    def _event(self, name: str, request_id: str, **kwargs: object) -> None:
        try:
            self.telemetry.record(
                TelemetryEvent(name=name, occurred_at=self.clock(), request_id=request_id, **kwargs)
            )
        except Exception:
            if self.config.telemetry_strict:
                raise
            warnings.warn(
                "TaskFront telemetry recorder failed; compilation continued.",
                RuntimeWarning,
                stacklevel=2,
            )

    async def compile(
        self,
        request: NormalizedRequest,
        *,
        session_id: str,
        revision: int,
    ) -> CompilationResult:
        compilation_started = perf_counter()
        request_id = self.id_generator()
        result_id = self.id_generator()
        trace_id = self.id_generator()
        trace_steps: list[TraceStep] = []
        self._event("compilation.started", request_id, session_id=session_id, duration_ms=0.0)
        self._event(
            "contracts.validated",
            request_id,
            duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
            metadata={"catalog_digest": self.catalog.digest},
        )
        budget = ProviderBudget(self.config.provider_budget)
        provider_started = perf_counter()
        self._event("provider.requested", request_id, duration_ms=0.0)
        try:
            async with asyncio.timeout(self.config.provider_budget.max_provider_time_seconds):
                try:
                    drafts = await self.interpretation_provider.interpret(
                        request,
                        self.catalog,
                        max_candidates=self.config.max_candidates,
                        budget=budget,
                    )
                except TimeoutError as exc:
                    diagnostic = Diagnostic(
                        code="E-PROVIDER-TIMEOUT",
                        severity="error",
                        phase="provider",
                        message="Interpretation provider timed out.",
                    )
                    raise ProviderError(diagnostic.message, [diagnostic]) from exc
        except TimeoutError as exc:
            diagnostic = Diagnostic(
                code="E-BUDGET-PROVIDER-TIME",
                severity="error",
                phase="provider",
                message="Provider wall-clock budget was exceeded.",
            )
            self._event(
                "provider.failed",
                request_id,
                duration_ms=round((perf_counter() - provider_started) * 1000, 3),
                error_code=diagnostic.code,
            )
            self._event(
                "compilation.failed",
                request_id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
                error_code=diagnostic.code,
            )
            raise BudgetExceededError(diagnostic.message, [diagnostic]) from exc
        except (ProviderError, InternalCompilerError, BudgetExceededError):
            self._event(
                "provider.failed",
                request_id,
                duration_ms=round((perf_counter() - provider_started) * 1000, 3),
                error_code="E-PROVIDER",
            )
            self._event(
                "compilation.failed",
                request_id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
                error_code="E-PROVIDER",
            )
            raise
        except Exception as exc:
            diagnostic = Diagnostic(
                code="E-PROVIDER-FAILURE",
                severity="error",
                phase="provider",
                message="Provider failed without a safe structured diagnostic.",
            )
            self._event(
                "provider.failed",
                request_id,
                duration_ms=round((perf_counter() - provider_started) * 1000, 3),
                error_code=diagnostic.code,
            )
            self._event(
                "compilation.failed",
                request_id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
                error_code=diagnostic.code,
            )
            raise ProviderError(diagnostic.message, [diagnostic]) from exc

        self._event(
            "provider.completed",
            request_id,
            duration_ms=round((perf_counter() - provider_started) * 1000, 3),
        )
        valid_drafts = self._validate_drafts(drafts)
        self._event(
            "candidates.generated",
            request_id,
            duration_ms=0.0,
            candidate_count=len(valid_drafts),
        )
        if not valid_drafts:
            trace_steps.append(
                TraceStep(phase="candidate_resolution", reason_code="D-NO-POSITIVE-MATCH")
            )
            result = self._unsupported(
                result_id=result_id,
                session_id=session_id,
                revision=revision,
                trace_id=trace_id,
                trace_steps=trace_steps,
            )
            self._event(
                "compilation.blocked",
                request_id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
            )
            return result

        analyses: list[CandidateAnalysis] = []
        diagnostics: list[Diagnostic] = []
        for draft in valid_drafts:
            contract = self.catalog.get(draft.capability_id, draft.capability_version)
            assert contract is not None
            try:
                extraction = await self._static_extractor.extract(request, contract, draft)
            except ValueError as exc:
                diagnostic = Diagnostic(
                    code="E-PROVIDER-UNKNOWN-FIELD",
                    severity="error",
                    phase="provider_validation",
                    message="Provider candidate contained undeclared inputs.",
                    contract_ref=f"{contract.id}@{contract.version}",
                )
                raise ProviderError(diagnostic.message, [diagnostic]) from exc
            analysis = analyze_candidate(
                contract,
                draft,
                extraction,
                self.config,
                secret_resolver=self.secret_resolver,
                extension_registry=self.extension_registry,
            )
            analyses.append(analysis)
            diagnostics.extend(analysis.diagnostics)
            trace_steps.append(
                TraceStep(
                    phase="candidate_analysis",
                    reason_code="D-CANDIDATE-ANALYZED",
                    capability_ref=f"{contract.id}@{contract.version}",
                    metadata={"blocking_gap_count": sum(gap.blocking for gap in analysis.gaps)},
                )
            )
            self._event(
                "fields.resolved", request_id, capability_id=contract.id, duration_ms=0.0
            )
            self._event(
                "constraints.evaluated",
                request_id,
                capability_id=contract.id,
                duration_ms=0.0,
            )

        analyses.sort(
            key=lambda item: (
                -item.candidate.score,
                item.candidate.capability_id,
                item.candidate.capability_version,
            )
        )
        viable = [item for item in analyses if item.candidate.viability == "viable"]
        if not viable:
            trace_steps.append(
                TraceStep(phase="candidate_resolution", reason_code="D-NO-VIABLE-CANDIDATE")
            )
            return self._unsupported(
                result_id=result_id,
                session_id=session_id,
                revision=revision,
                trace_id=trace_id,
                trace_steps=trace_steps,
                candidates=[item.candidate for item in analyses],
                diagnostics=diagnostics,
                decision_code="D-NO-VIABLE-CANDIDATE",
                reproducible=all(item.reproducible for item in analyses),
            )

        if len(viable) > 1 and not self._can_auto_select(viable):
            candidates = [item.candidate for item in analyses]
            refs = [f"{item.candidate.capability_id}@{item.candidate.capability_version}" for item in viable]
            gap = Gap(
                code="GAP-AMBIGUOUS-CAPABILITY",
                kind="ambiguous",
                field_paths=["capability"],
                message="Multiple capabilities can plausibly handle this request.",
                blocking=True,
                candidate_values=refs,
                suggested_resolution="choose_candidate",
            )
            question = candidate_question(
                refs,
                self.config,
                result_id=result_id,
                revision=revision,
            )
            self._event(
                "gaps.computed", request_id, duration_ms=0.0, gap_counts={"ambiguous": 1}
            )
            self._event(
                "questions.planned", request_id, duration_ms=0.0, question_count=1
            )
            self._event(
                "compilation.blocked",
                request_id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
            )
            trace_steps.append(
                TraceStep(phase="candidate_resolution", reason_code="D-MULTIPLE-VIABLE-CANDIDATES")
            )
            return CompilationResult(
                schema_version="0.2",
                result_id=result_id,
                session_id=session_id,
                revision=revision,
                catalog_digest=self.catalog.digest,
                reproducible=all(item.reproducible for item in viable),
                status="ambiguous",
                candidates=candidates,
                gaps=[gap],
                questions=[question],
                diagnostics=diagnostics,
                decision_codes=["D-MULTIPLE-VIABLE-CANDIDATES"],
                trace=ExplainTrace(
                    trace_id=trace_id,
                    created_at=self.clock(),
                    reproducible=all(item.reproducible for item in viable),
                    steps=trace_steps,
                ),
            )

        selected = viable[0]
        gap_counts = dict(Counter(gap.kind for gap in selected.gaps))
        self._event(
            "gaps.computed",
            request_id,
            capability_id=selected.contract.id,
            duration_ms=0.0,
            gap_counts=gap_counts,
        )
        candidates = [item.candidate for item in analyses]
        if is_ready(selected.candidate, selected.gaps, self.catalog):
            intent = build_intent(
                selected.candidate,
                self.catalog,
                self.clock,
                self.id_generator,
                result_id=result_id,
            )
            self._event(
                "compilation.ready",
                request_id,
                capability_id=selected.contract.id,
                duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
            )
            trace_steps.append(
                TraceStep(
                    phase="readiness",
                    reason_code="D-READY",
                    capability_ref=f"{selected.contract.id}@{selected.contract.version}",
                )
            )
            return CompilationResult(
                schema_version="0.2",
                result_id=result_id,
                session_id=session_id,
                revision=revision,
                catalog_digest=self.catalog.digest,
                reproducible=selected.reproducible,
                status="ready",
                candidates=candidates,
                selected_candidate=selected.candidate,
                gaps=selected.gaps,
                intent=intent,
                diagnostics=diagnostics,
                decision_codes=["D-READY"],
                trace=ExplainTrace(
                    trace_id=trace_id,
                    created_at=self.clock(),
                    reproducible=selected.reproducible,
                    steps=trace_steps,
                ),
            )

        questions, question_diagnostics = plan_questions(
            selected.gaps,
            selected.contract,
            self.config,
            result_id=result_id,
            revision=revision,
            has_host_renderer=self.question_renderer is not None,
            custom_ranker=(
                self.extension_registry.question_rankers[selected.contract.question_ranker][1]
                if selected.contract.question_ranker is not None
                else None
            ),
        )
        diagnostics.extend(question_diagnostics)
        if not questions:
            diagnostic = Diagnostic(
                code="E-INTERNAL-UNASKABLE-BLOCKER",
                severity="error",
                phase="question_planning",
                message="Blocking gaps could not be mapped to clarification questions.",
                contract_ref=f"{selected.contract.id}@{selected.contract.version}",
            )
            raise InternalCompilerError(diagnostic.message, [diagnostic])
        if self.question_renderer is not None:
            questions, renderer_diagnostics = await self._render_questions(
                questions, selected.contract
            )
            diagnostics.extend(renderer_diagnostics)
        status = (
            "conflicting"
            if any(gap.kind == "conflicting" and gap.blocking for gap in selected.gaps)
            else "needs_clarification"
        )
        result_reproducible = (
            selected.reproducible
            and (
                selected.contract.question_ranker is None
                or self.extension_registry.question_rankers[
                    selected.contract.question_ranker
                ][0].deterministic
            )
        )
        self._event(
            "questions.planned", request_id, duration_ms=0.0, question_count=len(questions)
        )
        self._event(
            "compilation.blocked",
            request_id,
            capability_id=selected.contract.id,
            duration_ms=round((perf_counter() - compilation_started) * 1000, 3),
        )
        trace_steps.append(
            TraceStep(
                phase="readiness",
                reason_code="D-BLOCKED",
                capability_ref=f"{selected.contract.id}@{selected.contract.version}",
            )
        )
        return CompilationResult(
            schema_version="0.2",
            result_id=result_id,
            session_id=session_id,
            revision=revision,
            catalog_digest=self.catalog.digest,
            reproducible=result_reproducible,
            status=status,
            candidates=candidates,
            selected_candidate=selected.candidate,
            gaps=selected.gaps,
            questions=questions,
            diagnostics=diagnostics,
            decision_codes=["D-BLOCKED"],
            trace=ExplainTrace(
                trace_id=trace_id,
                created_at=self.clock(),
                reproducible=result_reproducible,
                steps=trace_steps,
            ),
        )

    def _validate_drafts(self, drafts: list[CandidateDraft]) -> list[CandidateDraft]:
        if len(drafts) > self.config.max_candidates:
            diagnostic = Diagnostic(
                code="E-PROVIDER-CANDIDATE-LIMIT",
                severity="error",
                phase="provider_validation",
                message="Provider returned more candidates than requested.",
            )
            raise ProviderError(diagnostic.message, [diagnostic], retryable=False)
        valid: list[CandidateDraft] = []
        seen: set[tuple[str, str]] = set()
        for draft in drafts:
            contract = self.catalog.get(draft.capability_id, draft.capability_version)
            if contract is None:
                diagnostic = Diagnostic(
                    code="E-PROVIDER-UNKNOWN-CAPABILITY",
                    severity="error",
                    phase="provider_validation",
                    message="Provider returned a capability outside the current catalog.",
                    contract_ref=f"{draft.capability_id}@{draft.capability_version}",
                )
                raise ProviderError(diagnostic.message, [diagnostic])
            unknown = set(draft.inputs) - set(contract.all_inputs)
            if unknown:
                diagnostic = Diagnostic(
                    code="E-PROVIDER-UNKNOWN-FIELD",
                    severity="error",
                    phase="provider_validation",
                    message="Provider returned undeclared capability inputs.",
                    field_path=sorted(unknown)[0],
                    contract_ref=f"{contract.id}@{contract.version}",
                )
                raise ProviderError(diagnostic.message, [diagnostic])
            key = (contract.id, contract.version)
            if key in seen:
                diagnostic = Diagnostic(
                    code="E-PROVIDER-DUPLICATE-CANDIDATE",
                    severity="error",
                    phase="provider_validation",
                    message="Provider returned a duplicate exact capability candidate.",
                    contract_ref=f"{contract.id}@{contract.version}",
                )
                raise ProviderError(diagnostic.message, [diagnostic], retryable=False)
            seen.add(key)
            valid.append(draft)
        return valid

    async def _render_questions(
        self,
        questions: list[ClarificationQuestion],
        contract,
    ) -> tuple[list[ClarificationQuestion], list[Diagnostic]]:
        rendered: list[ClarificationQuestion] = []
        diagnostics: list[Diagnostic] = []
        for question in questions:
            target = question.targets[0]
            if not target.startswith("inputs."):
                rendered.append(question)
                continue
            field_name = target.removeprefix("inputs.")
            spec = contract.all_inputs[field_name]
            if spec.classification == "secret":
                rendered.append(question)
                continue
            try:
                text = await self.question_renderer.render(
                    text=question.text,
                    field_path=target,
                    classification=spec.classification,
                )
                rendered.append(question.model_copy(update={"text": text}))
            except Exception:
                diagnostics.append(
                    Diagnostic(
                        code="E-PROVIDER-QUESTION-RENDERER",
                        severity="warning",
                        phase="question_rendering",
                        message="Question renderer failed; deterministic text was retained.",
                        contract_ref=f"{contract.id}@{contract.version}",
                    )
                )
                rendered.append(question)
        return rendered, diagnostics

    def _can_auto_select(self, analyses: list[CandidateAnalysis]) -> bool:
        if (
            not self.config.auto_select_candidate
            or len(analyses) < 2
            or self.interpretation_provider.score_semantics != "calibrated"
        ):
            return False
        return (
            analyses[0].candidate.score >= self.config.auto_select_threshold
            and analyses[0].candidate.score - analyses[1].candidate.score
            >= self.config.auto_select_margin
        )

    def _unsupported(
        self,
        *,
        result_id: str,
        session_id: str,
        revision: int,
        trace_id: str,
        trace_steps: list[TraceStep],
        candidates=None,
        diagnostics=None,
        decision_code: str = "D-NO-POSITIVE-MATCH",
        reproducible: bool = True,
    ) -> CompilationResult:
        return CompilationResult(
            schema_version="0.2",
            result_id=result_id,
            session_id=session_id,
            revision=revision,
            catalog_digest=self.catalog.digest,
            reproducible=reproducible,
            status="unsupported",
            candidates=candidates or [],
            diagnostics=diagnostics or [],
            decision_codes=[decision_code],
            trace=ExplainTrace(
                trace_id=trace_id,
                created_at=self.clock(),
                reproducible=reproducible,
                steps=trace_steps,
            ),
        )


__all__ = ["CompilerPipeline", "random_id", "utc_now"]
