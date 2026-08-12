from __future__ import annotations

from taskc import TaskCompiler
from taskc.telemetry import InMemoryTelemetryRecorder


def test_telemetry_records_stages_without_user_text(fix_bug_contract: dict) -> None:
    recorder = InMemoryTelemetryRecorder()
    compiler = TaskCompiler.from_contracts([fix_bug_contract], telemetry=recorder)
    compiler.compile(
        "Fix private failure details",
        {"repository": "current", "problem_description": "private failure details"},
    )
    names = [event.name for event in recorder.events]
    assert names[0] == "compilation.started"
    assert names[-1] == "compilation.ready"
    serialized = "".join(event.model_dump_json() for event in recorder.events)
    assert "private failure details" not in serialized
