from __future__ import annotations

from typing import Protocol

from taskc.models import CompilationSession, Diagnostic, StaleSessionError


def _stale(message: str) -> StaleSessionError:
    diagnostic = Diagnostic(
        code="E-SESSION-STALE",
        severity="error",
        phase="session",
        message=message,
    )
    return StaleSessionError(message, [diagnostic])


class SessionStore(Protocol):
    def get(self, session_id: str) -> CompilationSession | None: ...
    def put(self, session: CompilationSession, *, expected_revision: int) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CompilationSession] = {}

    def get(self, session_id: str) -> CompilationSession | None:
        session = self._sessions.get(session_id)
        return session.model_copy(deep=True) if session is not None else None

    def put(self, session: CompilationSession, *, expected_revision: int) -> None:
        current = self._sessions.get(session.session_id)
        if current is None:
            if expected_revision != -1:
                raise _stale("Session does not exist at the expected revision.")
        elif current.revision != expected_revision:
            raise _stale("Session revision changed before the update could be committed.")
        self._sessions[session.session_id] = session.model_copy(deep=True)


__all__ = ["InMemorySessionStore", "SessionStore"]
