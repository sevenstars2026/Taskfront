from __future__ import annotations

import time

from taskc.contracts import load_catalog_objects


def test_one_hundred_contracts_validate_within_mvp_target() -> None:
    contracts = [
        {
            "id": f"benchmark.capability_{index}",
            "version": "1.0.0",
            "description": "Benchmark capability.",
            "required_inputs": {
                "value": {"type": "string", "description": "Value."}
            },
            "deliverables": ["result"],
        }
        for index in range(100)
    ]
    durations = []
    for _ in range(5):
        started = time.perf_counter()
        load_catalog_objects(contracts)
        durations.append(time.perf_counter() - started)
    assert sorted(durations)[-2] < 0.250

