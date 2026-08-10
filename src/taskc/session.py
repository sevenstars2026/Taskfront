from __future__ import annotations

from typing import Protocol

from taskc.models import CompilationSession


class SessionStore(Protocol):
    def get(self, session_id: str) -> CompilationSession | None: ...

    def put(self, session: CompilationSession) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CompilationSession] = {}

    def get(self, session_id: str) -> CompilationSession | None:
        session = self._sessions.get(session_id)
        return session.model_copy(deep=True) if session is not None else None

    def put(self, session: CompilationSession) -> None:
        self._sessions[session.session_id] = session.model_copy(deep=True)


__all__ = ["InMemorySessionStore", "SessionStore"]

