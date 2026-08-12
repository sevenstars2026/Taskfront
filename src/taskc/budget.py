from __future__ import annotations

import time
from dataclasses import dataclass, field

from taskc.config import ProviderBudgetConfig
from taskc.models import BudgetExceededError, Diagnostic


@dataclass(slots=True)
class ProviderBudget:
    config: ProviderBudgetConfig
    logical_calls: int = 0
    network_attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def _raise(self, message: str) -> None:
        diagnostic = Diagnostic(
            code="E-BUDGET-EXCEEDED",
            severity="error",
            phase="provider",
            message=message,
        )
        raise BudgetExceededError(message, [diagnostic])

    def consume_logical_call(self) -> None:
        self.check_time()
        if self.logical_calls >= self.config.max_logical_model_calls:
            self._raise("Provider logical model call budget was exceeded.")
        self.logical_calls += 1

    def consume_network_attempt(self) -> None:
        self.check_time()
        if self.network_attempts >= self.config.max_network_attempts:
            self._raise("Provider network attempt budget was exceeded.")
        self.network_attempts += 1

    def check_time(self) -> None:
        if time.monotonic() - self.started_at > self.config.max_provider_time_seconds:
            self._raise("Provider wall-clock budget was exceeded.")


__all__ = ["ProviderBudget"]
