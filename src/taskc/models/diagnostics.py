from __future__ import annotations

from typing import Literal

from .common import StrictModel


class Diagnostic(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    field_path: str | None = None
    contract_id: str | None = None
    hint: str | None = None


class TaskCompilerError(Exception):
    """Base error carrying stable, safe diagnostics."""

    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


class ContractValidationError(TaskCompilerError):
    pass


class InvalidInputError(TaskCompilerError):
    pass


class ProviderError(TaskCompilerError):
    pass

