from __future__ import annotations

from datetime import datetime
from typing import Callable

from taskc.contracts import CapabilityCatalog
from taskc.models import CandidateIntent, DispatchReadyIntent, Gap


def is_ready(candidate: CandidateIntent, gaps: list[Gap], catalog: CapabilityCatalog) -> bool:
    return (
        candidate.viability == "viable"
        and catalog.contains(candidate.capability_id, candidate.capability_version)
        and not any(gap.blocking for gap in gaps)
        and not any(value.redacted for value in candidate.inputs.values())
    )


def build_intent(
    candidate: CandidateIntent,
    catalog: CapabilityCatalog,
    clock: Callable[[], datetime],
    id_generator: Callable[[], str],
    *,
    result_id: str,
) -> DispatchReadyIntent:
    contract = catalog.get(candidate.capability_id, candidate.capability_version)
    if contract is None:
        raise ValueError("candidate is not present in the current catalog")
    undeclared = set(candidate.inputs) - set(contract.all_inputs)
    if undeclared:
        raise ValueError("candidate contains undeclared fields")
    return DispatchReadyIntent(
        schema_version="0.2",
        intent_id=id_generator(),
        result_id=result_id,
        capability_id=contract.id,
        capability_version=contract.version,
        catalog_digest=catalog.digest,
        inputs=candidate.inputs,
        deliverables=contract.deliverables,
        compiled_at=clock(),
    )


__all__ = ["build_intent", "is_ready"]
