from __future__ import annotations

import pytest

from taskc.contracts import load_catalog_objects
from taskc.models import CapabilityContract, ContractValidationError


def test_valid_contract_and_digest_are_deterministic(fix_bug_contract: dict) -> None:
    first = load_catalog_objects([fix_bug_contract])
    second = load_catalog_objects([fix_bug_contract])
    assert first.digest == second.digest
    assert first.get("repository.fix_bug", "1.0.0") is not None


def test_unknown_fields_fail_closed(fix_bug_contract: dict) -> None:
    fix_bug_contract["tool"] = "shell"
    with pytest.raises(ContractValidationError) as raised:
        load_catalog_objects([fix_bug_contract])
    assert raised.value.diagnostics[0].code in {"E-CONTRACT-002", "E-CONTRACT-003"}


def test_required_optional_overlap_is_rejected(fix_bug_contract: dict) -> None:
    fix_bug_contract["optional_inputs"]["repository"] = {
        "type": "string",
        "description": "Duplicate.",
    }
    with pytest.raises(ContractValidationError):
        load_catalog_objects([fix_bug_contract])


def test_unknown_conditional_field_is_rejected(build_contract: dict) -> None:
    build_contract["conditional_requirements"][0]["require"] = ["unknown"]
    with pytest.raises(ContractValidationError):
        load_catalog_objects([build_contract])


def test_array_requires_item_type(fix_bug_contract: dict) -> None:
    del fix_bug_contract["optional_inputs"]["reproduction_steps"]["items"]
    with pytest.raises(ContractValidationError):
        load_catalog_objects([fix_bug_contract])


def test_schema_version_and_semver_are_strict(fix_bug_contract: dict) -> None:
    fix_bug_contract["schema_version"] = "1.0"
    fix_bug_contract["version"] = "latest"
    with pytest.raises(ContractValidationError):
        load_catalog_objects([fix_bug_contract])


def test_condition_all_and_any_are_supported() -> None:
    contract = CapabilityContract.model_validate(
        {
            "id": "example.condition",
            "version": "1.0.0",
            "description": "Condition example.",
            "required_inputs": {"a": {"type": "boolean", "description": "A"}},
            "optional_inputs": {
                "b": {"type": "string", "description": "B"},
                "c": {"type": "string", "description": "C"},
            },
            "conditional_requirements": [
                {
                    "when": {
                        "all": [
                            {"field": "a", "equals": True},
                            {"any": [{"field": "b", "exists": True}, {"field": "c", "exists": True}]},
                        ]
                    },
                    "require": ["c"],
                }
            ],
            "deliverables": ["result"],
        }
    )
    assert contract.conditional_requirements[0].when.referenced_fields() == {"a", "b", "c"}

