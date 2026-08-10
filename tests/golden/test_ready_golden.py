from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from taskc import TaskCompiler


def test_ready_result_matches_golden(fix_bug_contract: dict) -> None:
    compiler = TaskCompiler.from_contracts(
        [fix_bug_contract],
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        id_generator=lambda: "fixed-id",
    )
    result = compiler.compile(
        "Fix checkout",
        {"repository": "current", "problem_description": "Checkout fails"},
    )
    fixture = Path(__file__).parents[1] / "fixtures" / "golden_ready.json"
    assert result.model_dump(mode="json") == json.loads(fixture.read_text(encoding="utf-8"))

