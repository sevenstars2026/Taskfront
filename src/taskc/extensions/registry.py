from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ExtensionDescriptor:
    name: str
    version: str
    deterministic: bool = True
    sensitive_access: Literal["none", "metadata", "value"] = "none"

    @property
    def fingerprint(self) -> str:
        return f"{self.name}:{self.version}:{self.deterministic}:{self.sensitive_access}"


@dataclass(slots=True)
class ExtensionRegistry:
    value_validators: dict[str, tuple[ExtensionDescriptor, Any]] = field(default_factory=dict)
    constraint_evaluators: dict[str, tuple[ExtensionDescriptor, Any]] = field(default_factory=dict)
    default_providers: dict[str, tuple[ExtensionDescriptor, Any]] = field(default_factory=dict)
    question_rankers: dict[str, tuple[ExtensionDescriptor, Any]] = field(default_factory=dict)

    def register_constraint(self, descriptor: ExtensionDescriptor, evaluator: Any) -> None:
        self._register(self.constraint_evaluators, descriptor, evaluator)

    def register_question_ranker(self, descriptor: ExtensionDescriptor, ranker: Any) -> None:
        self._register(self.question_rankers, descriptor, ranker)

    def register_value_validator(self, descriptor: ExtensionDescriptor, validator: Any) -> None:
        self._register(self.value_validators, descriptor, validator)

    def register_default_provider(self, descriptor: ExtensionDescriptor, provider: Any) -> None:
        self._register(self.default_providers, descriptor, provider)

    @staticmethod
    def _register(target: dict[str, tuple[ExtensionDescriptor, Any]], descriptor: ExtensionDescriptor, implementation: Any) -> None:
        if not descriptor.name or any(token in descriptor.name for token in ("/", "\\", ":", ".py")):
            raise ValueError("extension names must be stable registry identifiers, not module paths")
        if descriptor.name in target:
            raise ValueError(f"extension is already registered: {descriptor.name}")
        target[descriptor.name] = (descriptor, implementation)

    @property
    def fingerprints(self) -> list[str]:
        collections = (
            self.value_validators,
            self.constraint_evaluators,
            self.default_providers,
            self.question_rankers,
        )
        return sorted(descriptor.fingerprint for items in collections for descriptor, _ in items.values())


__all__ = ["ExtensionDescriptor", "ExtensionRegistry"]
