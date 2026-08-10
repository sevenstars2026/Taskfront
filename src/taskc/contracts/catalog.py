from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from taskc.models import CapabilityContract


def _semver_key(version: str) -> tuple[int, int, int, str]:
    core, _, suffix = version.partition("-")
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch), suffix


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    contracts: tuple[CapabilityContract, ...]
    digest: str

    @classmethod
    def build(cls, contracts: list[CapabilityContract]) -> "CapabilityCatalog":
        ordered = tuple(sorted(contracts, key=lambda item: (item.id, _semver_key(item.version))))
        payload = [
            contract.model_dump(mode="json", by_alias=True, exclude_none=True)
            for contract in ordered
        ]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return cls(contracts=ordered, digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def get(self, capability_id: str, version: str | None = None) -> CapabilityContract | None:
        matches = [item for item in self.contracts if item.id == capability_id]
        if version is not None:
            return next((item for item in matches if item.version == version), None)
        return max(matches, key=lambda item: _semver_key(item.version), default=None)

    def contains(self, capability_id: str, version: str) -> bool:
        return self.get(capability_id, version) is not None

    def __len__(self) -> int:
        return len(self.contracts)


__all__ = ["CapabilityCatalog"]

