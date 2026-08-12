from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cmp_to_key

from taskc.models import CapabilityContract, ContractValidationError, Diagnostic
from taskc.versions import SemVer


def _compare_contracts(left: CapabilityContract, right: CapabilityContract) -> int:
    if left.id != right.id:
        return -1 if left.id < right.id else 1
    left_version = SemVer.parse(left.version)
    right_version = SemVer.parse(right.version)
    if left_version < right_version:
        return -1
    if right_version < left_version:
        return 1
    if left.version == right.version:
        return 0
    return -1 if left.version < right.version else 1


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    contracts: tuple[CapabilityContract, ...]
    digest: str

    @classmethod
    def build(cls, contracts: list[CapabilityContract]) -> "CapabilityCatalog":
        ordered = tuple(sorted(contracts, key=cmp_to_key(_compare_contracts)))
        payload = [
            contract.model_dump(mode="json", by_alias=True, exclude_none=True)
            for contract in ordered
        ]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return cls(contracts=ordered, digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def get(self, capability_id: str, version: str | None = None) -> CapabilityContract | None:
        if version is None:
            return self.resolve(capability_id)
        return next(
            (
                item for item in self.contracts
                if item.id == capability_id and item.version == version
            ),
            None,
        )

    def resolve(
        self, capability_id: str, *, allow_prerelease: bool = False
    ) -> CapabilityContract | None:
        matches = [item for item in self.contracts if item.id == capability_id]
        stable = [item for item in matches if SemVer.parse(item.version).stable]
        eligible = matches if allow_prerelease else stable
        if not eligible:
            return None
        best_version = max(SemVer.parse(item.version) for item in eligible)
        winners = [
            item for item in eligible
            if SemVer.parse(item.version).same_precedence(best_version)
        ]
        exact_versions = {item.version for item in winners}
        if len(exact_versions) > 1:
            diagnostic = Diagnostic(
                code="E-CONTRACT-VERSION-AMBIGUOUS",
                severity="error",
                phase="contract_load",
                message="Multiple capability versions have equal SemVer precedence.",
                contract_ref=capability_id,
            )
            raise ContractValidationError(diagnostic.message, [diagnostic])
        return winners[0]

    def active_contracts(self, *, allow_prerelease: bool = False) -> tuple[CapabilityContract, ...]:
        return tuple(
            contract
            for capability_id in sorted({item.id for item in self.contracts})
            if (contract := self.resolve(capability_id, allow_prerelease=allow_prerelease)) is not None
        )

    def contains(self, capability_id: str, version: str) -> bool:
        return self.get(capability_id, version) is not None

    def __len__(self) -> int:
        return len(self.contracts)


__all__ = ["CapabilityCatalog"]
