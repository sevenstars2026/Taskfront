from __future__ import annotations

from typing import Protocol

from .events import TelemetryEvent


class TelemetryRecorder(Protocol):
    def record(self, event: TelemetryEvent) -> None: ...


class NullTelemetryRecorder:
    def record(self, event: TelemetryEvent) -> None:
        return None


class InMemoryTelemetryRecorder:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def record(self, event: TelemetryEvent) -> None:
        self.events.append(event)

