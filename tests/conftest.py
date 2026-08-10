from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def workspace_tmp() -> Path:
    """Windows-safe temp directory inside the writable workspace."""
    root = Path.cwd() / "test-runtime"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def fix_bug_contract() -> dict:
    return {
        "schema_version": "0.1",
        "id": "repository.fix_bug",
        "version": "1.0.0",
        "description": "Diagnose and fix a reproducible repository defect.",
        "examples": ["Fix the checkout bug", "Repair this failing test"],
        "negative_examples": ["Improve repository build speed"],
        "required_inputs": {
            "repository": {
                "type": "string",
                "description": "Repository or workspace.",
                "must_be_explicit": True,
            },
            "problem_description": {
                "type": "string",
                "description": "Observed incorrect behavior.",
            },
        },
        "optional_inputs": {
            "reproduction_steps": {
                "type": "array",
                "items": "string",
                "description": "Steps that reproduce the problem.",
            }
        },
        "deliverables": ["code_patch", "verification_report"],
    }


@pytest.fixture
def build_contract() -> dict:
    return {
        "schema_version": "0.1",
        "id": "repository.optimize_build",
        "version": "1.0.0",
        "description": "Improve repository build speed.",
        "examples": ["Make the build faster", "Reduce CI compile time"],
        "negative_examples": ["Improve runtime API performance"],
        "required_inputs": {
            "repository": {"type": "string", "description": "Repository."},
            "optimization_goal": {
                "type": "enum",
                "description": "Build stage.",
                "enum": ["local_build", "ci_build", "both"],
            },
        },
        "optional_inputs": {
            "ci_provider": {"type": "string", "description": "CI provider."},
            "measure": {
                "type": "boolean",
                "description": "Measure before and after.",
                "default": True,
            },
        },
        "conditional_requirements": [
            {
                "when": {"field": "optimization_goal", "in": ["ci_build", "both"]},
                "require": ["ci_provider"],
            }
        ],
        "deliverables": ["optimization_plan", "code_changes", "measurement"],
    }
