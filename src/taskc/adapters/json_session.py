from __future__ import annotations

import re
from pathlib import Path

from taskc.models import CompilationSession

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class JsonFileSessionStore:
    """Example file-backed store. File I/O remains outside the compiler core."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID.fullmatch(session_id):
            raise ValueError("session_id contains unsafe path characters")
        return self.directory / f"{session_id}.json"

    def get(self, session_id: str) -> CompilationSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return CompilationSession.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, session: CompilationSession) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(session.session_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)


__all__ = ["JsonFileSessionStore"]

