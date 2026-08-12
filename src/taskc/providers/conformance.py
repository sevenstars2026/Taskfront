from __future__ import annotations

from dataclasses import dataclass, field

from taskc.budget import ProviderBudget
from taskc.config import ProviderBudgetConfig
from taskc.contracts import CapabilityCatalog
from taskc.models import CandidateDraft, NormalizedRequest

from .base import InterpretationProvider


@dataclass(slots=True)
class ProviderConformanceReport:
    provider_id: str
    passed: bool = False
    checks: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    logical_calls: int = 0
    network_attempts: int = 0


def validate_candidate_batch(
    candidates: list[CandidateDraft],
    catalog: CapabilityCatalog,
    *,
    max_candidates: int,
) -> list[str]:
    violations: list[str] = []
    if len(candidates) > max_candidates:
        violations.append("provider returned more candidates than requested")
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        ref = (candidate.capability_id, candidate.capability_version)
        contract = catalog.get(*ref)
        if contract is None:
            violations.append(f"unknown exact capability reference: {ref[0]}@{ref[1]}")
            continue
        if ref in seen:
            violations.append(f"duplicate capability candidate: {ref[0]}@{ref[1]}")
        seen.add(ref)
        unknown = sorted(set(candidate.inputs) - set(contract.all_inputs))
        if unknown:
            violations.append(
                f"undeclared fields for {ref[0]}@{ref[1]}: {', '.join(unknown)}"
            )
        if not candidate.evidence:
            violations.append(f"candidate has no evidence: {ref[0]}@{ref[1]}")
    return violations


async def run_provider_conformance(
    provider: InterpretationProvider,
    request: NormalizedRequest,
    catalog: CapabilityCatalog,
    *,
    max_candidates: int = 5,
    budget_config: ProviderBudgetConfig | None = None,
) -> ProviderConformanceReport:
    report = ProviderConformanceReport(
        provider_id=getattr(provider, "provider_id", "<missing>")
    )
    if getattr(provider, "score_semantics", None) not in {"calibrated", "uncalibrated"}:
        report.violations.append("score_semantics must be calibrated or uncalibrated")
    else:
        report.checks.append("score_semantics")
    if not getattr(provider, "config_version", ""):
        report.violations.append("config_version must be non-empty")
    else:
        report.checks.append("config_version")
    budget = ProviderBudget(budget_config or ProviderBudgetConfig())
    candidates = await provider.interpret(
        request,
        catalog,
        max_candidates=max_candidates,
        budget=budget,
    )
    report.violations.extend(
        validate_candidate_batch(candidates, catalog, max_candidates=max_candidates)
    )
    report.checks.append("candidate_batch")
    report.logical_calls = budget.logical_calls
    report.network_attempts = budget.network_attempts
    report.passed = not report.violations
    return report


__all__ = [
    "ProviderConformanceReport",
    "run_provider_conformance",
    "validate_candidate_batch",
]
