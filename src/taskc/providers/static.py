from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from taskc.budget import ProviderBudget
from taskc.config import CompilerConfig
from taskc.contracts import CapabilityCatalog
from taskc.models import (
    CandidateDraft,
    CapabilityContract,
    ExtractionResult,
    MatchEvidence,
    NormalizedRequest,
    SourcedValue,
)

_WORD = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+")
_STOP_WORDS = {
    "a", "an", "and", "for", "from", "help", "in", "into", "of", "or",
    "please", "the", "this", "to", "with",
}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    result: set[str] = set()
    for token in _WORD.findall(normalized):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            result.add(token)
            if len(token) > 1:
                result.update(token[index:index + 2] for index in range(len(token) - 1))
        elif len(token) > 1 and token not in _STOP_WORDS:
            result.add(token)
    return result


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class StaticCandidateProvider:
    provider_id = "static-candidate"
    config_version = "0.2.1-stopwords"
    score_semantics = "uncalibrated"

    def __init__(
        self,
        *,
        min_score: float = 0.05,
        allow_prerelease: bool = False,
    ):
        self.min_score = min_score
        self.allow_prerelease = allow_prerelease

    async def generate(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        limit: int,
    ) -> list[CandidateDraft]:
        if request.requested_capability:
            requested = request.requested_capability
            contract = catalog.get(requested.id, requested.version)
            if contract is None:
                return []
            return [
                CandidateDraft(
                    capability_id=contract.id,
                    capability_version=contract.version,
                    score=1.0,
                    evidence=[
                        MatchEvidence(
                            code="MATCH-EXPLICIT-CAPABILITY",
                            summary="Selected by an explicit host capability reference.",
                            strength=1.0,
                        )
                    ],
                )
            ]

        request_tokens = _tokens(request.text)
        candidates: list[CandidateDraft] = []
        for contract in catalog.active_contracts(allow_prerelease=self.allow_prerelease):
            positive = max(
                (_similarity(request_tokens, _tokens(text)) for text in [contract.description, *contract.examples]),
                default=0.0,
            )
            negative = max(
                (_similarity(request_tokens, _tokens(text)) for text in contract.negative_examples),
                default=0.0,
            )
            score = max(0.0, min(1.0, positive * (1.0 - (negative * 0.75))))
            if score >= self.min_score and score > 0:
                candidates.append(
                    CandidateDraft(
                        capability_id=contract.id,
                        capability_version=contract.version,
                        score=round(score, 6),
                        evidence=[
                            MatchEvidence(
                                code="MATCH-STATIC-JACCARD",
                                summary="Deterministic lexical match against contract text.",
                                strength=round(score, 6),
                            )
                        ],
                    )
                )
        return sorted(
            candidates,
            key=lambda item: (-item.score, item.capability_id, item.capability_version),
        )[:limit]


class StaticInterpretationProvider:
    provider_id = "static-interpretation"
    config_version = "0.2.1-stopwords"
    score_semantics = "uncalibrated"

    def __init__(self, config: CompilerConfig):
        self.candidate_provider = StaticCandidateProvider(
            min_score=config.min_static_match_score,
            allow_prerelease=config.allow_prerelease,
        )

    async def interpret(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        *,
        max_candidates: int,
        budget: ProviderBudget,
    ) -> list[CandidateDraft]:
        return await self.candidate_provider.generate(request, catalog, max_candidates)


def _parse_assignment(raw: str) -> Any:
    value = raw.strip().strip("'\"")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class StaticExtractionProvider:
    provider_id = "static-extraction"
    config_version = "0.2"

    async def extract(
        self,
        request: NormalizedRequest,
        contract: CapabilityContract,
        candidate: CandidateDraft,
    ) -> ExtractionResult:
        values: dict[str, list[SourcedValue]] = defaultdict(list)
        declared = contract.all_inputs
        unknown_candidate_fields = set(candidate.inputs) - set(declared)
        if unknown_candidate_fields:
            raise ValueError(
                f"candidate contains undeclared fields: {sorted(unknown_candidate_fields)}"
            )
        for field_name, sourced in candidate.inputs.items():
            spec = declared[field_name]
            classification = (
                "sensitive"
                if spec.classification == "secret" and sourced.value_ref is None
                else spec.classification
            )
            values[field_name].append(sourced.model_copy(update={"classification": classification}))
        for field_name, sourced in request.context.items():
            if field_name in declared:
                spec = declared[field_name]
                classification = (
                    "sensitive"
                    if spec.classification == "secret" and sourced.value_ref is None
                    else spec.classification
                )
                values[field_name].append(sourced.model_copy(update={"classification": classification}))
        for field_name, sourced_values in request.answers.items():
            if field_name in declared:
                spec = declared[field_name]
                values[field_name].extend(
                    item.model_copy(
                        update={
                            "classification": (
                                "sensitive"
                                if spec.classification == "secret" and item.value_ref is None
                                else spec.classification
                            )
                        }
                    )
                    for item in sourced_values
                )

        for field_name, spec in declared.items():
            pattern = re.compile(
                rf"(?:^|[\s,;]){re.escape(field_name)}\s*[:=]\s*([^,;\n]+)",
                re.IGNORECASE,
            )
            match = pattern.search(request.raw_text)
            if match:
                values[field_name].append(
                    SourcedValue(
                        value=_parse_assignment(match.group(1)),
                        source="user_request",
                        source_ref=f"request:{field_name}",
                        confidence=1.0,
                        classification=(
                            "sensitive" if spec.classification == "secret" else spec.classification
                        ),
                    )
                )

        for field_name, spec in declared.items():
            if not values[field_name] and "default" in spec.model_fields_set:
                values[field_name].append(
                    SourcedValue(
                        value=spec.default,
                        source="contract_default",
                        source_ref=f"contract:{contract.id}@{contract.version}:{field_name}:default",
                        confidence=1.0,
                        classification=spec.classification,
                    )
                )
        return ExtractionResult(values=dict(values), unresolved_terms=candidate.unresolved_terms)


__all__ = [
    "StaticCandidateProvider",
    "StaticExtractionProvider",
    "StaticInterpretationProvider",
]
