from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import StrictModel


class Diagnostic(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    phase: str = "unknown"
    field_path: str | None = None
    contract_ref: str | None = Field(default=None, alias="contract_id")
    hint: str | None = None

    @property
    def contract_id(self) -> str | None:
        """Compatibility view for the 0.1 renderer API."""
        return self.contract_ref


class OperationError(StrictModel):
    code: str
    category: Literal[
        "invalid_contract",
        "invalid_input",
        "provider_failure",
        "budget_exceeded",
        "stale_session",
        "internal_error",
    ]
    message: str
    retryable: bool = False
    diagnostics: list[Diagnostic] = []


class TaskCompilerError(Exception):
    """Base error carrying stable, safe diagnostics."""

    category = "internal_error"
    default_code = "E-INTERNAL"
    retryable = False

    def __init__(
        self,
        message: str,
        diagnostics: list[Diagnostic] | None = None,
        *,
        retryable: bool | None = None,
    ):
        super().__init__(message)
        self.diagnostics = diagnostics or []
        self.error = OperationError(
            code=self.diagnostics[0].code if self.diagnostics else self.default_code,
            category=self.category,
            message=message,
            retryable=self.retryable if retryable is None else retryable,
            diagnostics=self.diagnostics,
        )


class ContractValidationError(TaskCompilerError):
    category = "invalid_contract"
    default_code = "E-CONTRACT"


class InvalidInputError(TaskCompilerError):
    category = "invalid_input"
    default_code = "E-INPUT"


class ProviderError(TaskCompilerError):
    category = "provider_failure"
    default_code = "E-PROVIDER"
    retryable = True


ProviderExecutionError = ProviderError


class BudgetExceededError(TaskCompilerError):
    category = "budget_exceeded"
    default_code = "E-BUDGET-EXCEEDED"


class StaleSessionError(TaskCompilerError):
    category = "stale_session"
    default_code = "E-SESSION-STALE"


class InternalCompilerError(TaskCompilerError):
    category = "internal_error"
    default_code = "E-INTERNAL"
