from __future__ import annotations

import hashlib
import json
from typing import Protocol

from taskc.models.common import JsonObject

from .base import StructuredModelProvider


class ProviderCache(Protocol):
    def get(self, key: str) -> JsonObject | None: ...

    def put(self, key: str, value: JsonObject) -> None: ...


class InMemoryProviderCache:
    def __init__(self) -> None:
        self._values: dict[str, JsonObject] = {}

    def get(self, key: str) -> JsonObject | None:
        value = self._values.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key: str, value: JsonObject) -> None:
        self._values[key] = json.loads(json.dumps(value))


class CachedStructuredModelProvider:
    """Caches raw structured provider responses; deterministic analysis is never cached."""

    config_version = "0.1"

    def __init__(self, provider: StructuredModelProvider, cache: ProviderCache):
        self.provider = provider
        self.cache = cache
        self.provider_id = f"cached:{provider.provider_id}"

    async def generate_json(
        self,
        *,
        system_instruction: str,
        payload: JsonObject,
        response_schema: JsonObject,
        request_id: str,
    ) -> JsonObject:
        canonical = json.dumps(
            {
                "system_instruction": system_instruction,
                "payload": payload,
                "provider_id": self.provider.provider_id,
                "provider_config_version": self.provider.config_version,
                "response_schema": response_schema,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = await self.provider.generate_json(
            system_instruction=system_instruction,
            payload=payload,
            response_schema=response_schema,
            request_id=request_id,
        )
        self.cache.put(key, result)
        return result


__all__ = ["CachedStructuredModelProvider", "InMemoryProviderCache", "ProviderCache"]

