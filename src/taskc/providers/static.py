from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from taskc.contracts import CapabilityCatalog
from taskc.models import (
    CandidateDraft,
    CapabilityContract,
    ExtractionResult,
    NormalizedRequest,
    SourcedValue,
)

_WORD = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")


def _tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in _WORD.findall(text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            result.update(token[index:index + 2] for index in range(max(1, len(token) - 1)))
            result.update(token)
        elif len(token) > 1:
            result.add(token)
    return result


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class StaticCandidateProvider:
    provider_id = "static-candidate"
    config_version = "0.1"

    async def generate(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        limit: int,
    ) -> list[CandidateDraft]:
        if request.requested_capability:
            contract = catalog.get(request.requested_capability)
            if contract is None:
                return []
            return [
                CandidateDraft(
                    capability_id=contract.id,
                    capability_version=contract.version,
                    score=1.0,
                    match_reasons=["Selected by an explicit clarification answer."],
                )
            ]

        latest = [catalog.get(capability_id) for capability_id in sorted({c.id for c in catalog.contracts})]
        contracts = [contract for contract in latest if contract is not None]
        if len(contracts) == 1:
            contract = contracts[0]
            return [
                CandidateDraft(
                    capability_id=contract.id,
                    capability_version=contract.version,
                    score=1.0,
                    match_reasons=["Only capability available in the catalog."],
                )
            ]

        request_tokens = _tokens(request.text)
        candidates: list[CandidateDraft] = []
        for contract in contracts:
            positive_texts = [contract.description, *contract.examples]
            positive = max((_similarity(request_tokens, _tokens(text)) for text in positive_texts), default=0.0)
            negative = max(
                (_similarity(request_tokens, _tokens(text)) for text in contract.negative_examples),
                default=0.0,
            )
            # Negative examples lower confidence but do not erase a positive match.
            # This preserves multiple plausible intents in mixed or ambiguous requests.
            score = max(0.0, min(1.0, positive * (1.0 - (negative * 0.75))))
            if score > 0:
                candidates.append(
                    CandidateDraft(
                        capability_id=contract.id,
                        capability_version=contract.version,
                        score=round(score, 6),
                        match_reasons=["Deterministic lexical match against the capability contract."],
                    )
                )
        return sorted(candidates, key=lambda item: (-item.score, item.capability_id, item.capability_version or ""))[:limit]


def _parse_assignment(raw: str) -> Any:
    value = raw.strip().strip("'\"")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class StaticExtractionProvider:
    provider_id = "static-extraction"
    config_version = "0.1"

    async def extract(
        self,
        request: NormalizedRequest,
        contract: CapabilityContract,
        candidate: CandidateDraft,
    ) -> ExtractionResult:
        values: dict[str, list[SourcedValue]] = defaultdict(list)
        declared = contract.all_inputs
        for field_name, sourced in candidate.inputs.items():
            if field_name in declared:
                values[field_name].append(sourced)
        for field_name, sourced in request.context.items():
            if field_name in declared:
                values[field_name].append(sourced)
        for field_name, sourced_values in request.answers.items():
            if field_name in declared:
                values[field_name].extend(sourced_values)

        for field_name in declared:
            pattern = re.compile(
                rf"(?:^|[\s,;]){re.escape(field_name)}\s*[:=]\s*([^,;\n]+)",
                re.IGNORECASE,
            )
            match = pattern.search(request.raw_text)
            if match:
                values[field_name].append(
                    SourcedValue(
                        value=_parse_assignment(match.group(1)),
                        source="user",
                        source_ref=f"request:{field_name}",
                        confidence=1.0,
                    )
                )

        for field_name, spec in declared.items():
            if not values[field_name] and "default" in spec.model_fields_set:
                values[field_name].append(
                    SourcedValue(
                        value=spec.default,
                        source="explicit_default",
                        source_ref=f"contract:{contract.id}:{field_name}:default",
                        confidence=1.0,
                    )
                )
        return ExtractionResult(values=dict(values), unresolved_terms=candidate.unresolved_terms)


__all__ = ["StaticCandidateProvider", "StaticExtractionProvider"]
