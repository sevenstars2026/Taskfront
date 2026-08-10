from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from taskc.models import ProviderError
from taskc.models.common import JsonObject
from taskc.models.diagnostics import Diagnostic


class OpenAICompatibleProvider:
    """Small optional Chat Completions adapter with no vendor SDK dependency."""

    provider_id = "openai-compatible"
    config_version = "0.1"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 2_000_000,
    ):
        self.endpoint = endpoint
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def generate_json(
        self,
        *,
        system_instruction: str,
        payload: JsonObject,
        response_schema: JsonObject,
        request_id: str,
    ) -> JsonObject:
        return await asyncio.to_thread(
            self._request, system_instruction, payload, response_schema, request_id
        )

    def _request(
        self,
        system_instruction: str,
        payload: JsonObject,
        response_schema: JsonObject,
        request_id: str,
    ) -> JsonObject:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "taskc_response", "strict": True, "schema": response_schema},
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
            method="POST",
        )
        failure: Exception
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise ValueError("provider response exceeds the configured size limit")
                envelope: dict[str, Any] = json.loads(raw.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("structured response is not an object")
            return result
        except TimeoutError as exc:
            code = "E-PROVIDER-TIMEOUT"
            failure = exc
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
            code = "E-PROVIDER-RESPONSE"
            failure = exc
        diagnostic = Diagnostic(
            code=code,
            severity="error",
            message="Structured model provider request failed.",
        )
        raise ProviderError(diagnostic.message, [diagnostic]) from failure


__all__ = ["OpenAICompatibleProvider"]
