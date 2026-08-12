from __future__ import annotations

import re
import stat
import threading
from pathlib import Path

from taskc.models import CompilationSession, Diagnostic, InvalidInputError, StaleSessionError
from taskc.security import session_contains_sensitive_literals

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_LOCK_GUARD = threading.Lock()
_FILE_LOCKS: dict[Path, threading.RLock] = {}


def _file_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _LOCK_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.RLock())


class JsonFileSessionStore:
    """CAS file store. Sensitive literals require a host-provided encrypted store."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID.fullmatch(session_id):
            raise ValueError("session_id contains unsafe path characters")
        return self.directory / f"{session_id}.json"

    def get(self, session_id: str) -> CompilationSession | None:
        path = self._path(session_id)
        with _file_lock(path):
            if not path.exists():
                return None
            return CompilationSession.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, session: CompilationSession, *, expected_revision: int) -> None:
        if session_contains_sensitive_literals(session):
            diagnostic = Diagnostic(
                code="E-INPUT-SENSITIVE-SESSION",
                severity="error",
                phase="session_persistence",
                message="The JSON session store refuses sensitive literal values.",
            )
            raise InvalidInputError(diagnostic.message, [diagnostic])
        target = self._path(session.session_id)
        with _file_lock(target):
            self.directory.mkdir(parents=True, exist_ok=True)
            if target.exists():
                current = CompilationSession.model_validate_json(target.read_text(encoding="utf-8"))
                if current.revision != expected_revision:
                    diagnostic = Diagnostic(
                        code="E-SESSION-STALE",
                        severity="error",
                        phase="session_persistence",
                        message="Session revision changed before file replacement.",
                    )
                    raise StaleSessionError(diagnostic.message, [diagnostic])
            elif expected_revision != -1:
                diagnostic = Diagnostic(
                    code="E-SESSION-STALE",
                    severity="error",
                    phase="session_persistence",
                    message="Session file does not exist at the expected revision.",
                )
                raise StaleSessionError(diagnostic.message, [diagnostic])
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                session.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
            )
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            temporary.replace(target)


__all__ = ["JsonFileSessionStore"]
