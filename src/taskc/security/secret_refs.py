from __future__ import annotations

from typing import Protocol


class SecretResolver(Protocol):
    def validate_ref(self, value_ref: str, *, expected_type: str) -> bool: ...


__all__ = ["SecretResolver"]
