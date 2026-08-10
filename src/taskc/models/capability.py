from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator

from .common import JsonValue, StrictModel

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def value_matches_type(value: JsonValue, type_name: str, items: str | None = None) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        if not isinstance(value, list):
            return False
        return items is None or all(value_matches_type(item, items) for item in value)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "enum":
        return True
    return False


class InputSpec(StrictModel):
    type: Literal["string", "integer", "number", "boolean", "array", "object", "enum"]
    description: str
    items: Literal["string", "integer", "number", "boolean", "object"] | None = None
    enum: list[JsonValue] | None = None
    must_be_explicit: bool = False
    default: JsonValue | None = None
    sensitive: bool = False
    question_template: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "InputSpec":
        if self.type == "array" and self.items is None:
            raise ValueError("array inputs must declare items")
        if self.type != "array" and self.items is not None:
            raise ValueError("items is only valid for array inputs")
        if self.type == "enum" and not self.enum:
            raise ValueError("enum inputs must declare at least one value")
        if self.type != "enum" and self.enum is not None:
            raise ValueError("enum values are only valid for enum inputs")
        if "default" in self.model_fields_set:
            if not value_matches_type(self.default, self.type, self.items):
                raise ValueError("default does not match the declared input type")
            if self.type == "enum" and self.default not in (self.enum or []):
                raise ValueError("default is not a declared enum value")
        if self.question_template is not None and not self.question_template.strip():
            raise ValueError("question_template must not be blank")
        return self


class ConditionExpression(StrictModel):
    field: str | None = None
    equals: JsonValue | None = None
    not_equals: JsonValue | None = None
    in_values: list[JsonValue] | None = Field(default=None, alias="in")
    exists: bool | None = None
    all: list["ConditionExpression"] | None = None
    any: list["ConditionExpression"] | None = None

    @model_validator(mode="after")
    def validate_expression(self) -> "ConditionExpression":
        operator_fields = {
            "equals": "equals" in self.model_fields_set,
            "not_equals": "not_equals" in self.model_fields_set,
            "in": "in_values" in self.model_fields_set,
            "exists": "exists" in self.model_fields_set,
            "all": "all" in self.model_fields_set,
            "any": "any" in self.model_fields_set,
        }
        selected = [name for name, present in operator_fields.items() if present]
        if len(selected) != 1:
            raise ValueError("condition must contain exactly one supported operator")
        operator = selected[0]
        if operator in {"all", "any"}:
            children = self.all if operator == "all" else self.any
            if self.field is not None or not children:
                raise ValueError(f"{operator} requires children and does not accept field")
        elif not self.field:
            raise ValueError(f"{operator} requires field")
        return self

    def referenced_fields(self) -> set[str]:
        if self.field:
            return {self.field}
        children = self.all if self.all is not None else self.any or []
        result: set[str] = set()
        for child in children:
            result.update(child.referenced_fields())
        return result


class ConditionalRequirement(StrictModel):
    when: ConditionExpression
    require: list[str]

    @model_validator(mode="after")
    def require_nonempty(self) -> "ConditionalRequirement":
        if not self.require or len(self.require) != len(set(self.require)):
            raise ValueError("require must contain unique field names")
        return self


class ConstraintSpec(StrictModel):
    code: str
    assert_condition: ConditionExpression = Field(alias="assert")
    message: str
    blocking: bool = True


class CapabilityContract(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    id: str
    version: str
    description: str
    examples: list[str] = []
    negative_examples: list[str] = []
    required_inputs: dict[str, InputSpec]
    optional_inputs: dict[str, InputSpec] = {}
    conditional_requirements: list[ConditionalRequirement] = []
    constraints: list[ConstraintSpec] = []
    deliverables: list[str]

    @model_validator(mode="after")
    def validate_contract(self) -> "CapabilityContract":
        if not _CAPABILITY_ID.fullmatch(self.id):
            raise ValueError("id must be a lower-case dot-separated capability name")
        if not _SEMVER.fullmatch(self.version):
            raise ValueError("version must be SemVer")
        if not self.description.strip():
            raise ValueError("description must not be blank")
        if not self.required_inputs:
            raise ValueError("required_inputs must not be empty")
        overlap = set(self.required_inputs) & set(self.optional_inputs)
        if overlap:
            raise ValueError(f"required_inputs and optional_inputs overlap: {sorted(overlap)}")
        declared = set(self.required_inputs) | set(self.optional_inputs)
        for requirement in self.conditional_requirements:
            unknown_refs = requirement.when.referenced_fields() - declared
            unknown_required = set(requirement.require) - declared
            if unknown_refs:
                raise ValueError(f"condition references unknown fields: {sorted(unknown_refs)}")
            if unknown_required:
                raise ValueError(f"condition requires unknown fields: {sorted(unknown_required)}")
        for constraint in self.constraints:
            unknown_refs = constraint.assert_condition.referenced_fields() - declared
            if unknown_refs:
                raise ValueError(f"constraint references unknown fields: {sorted(unknown_refs)}")
        if not self.deliverables or any(not item.strip() for item in self.deliverables):
            raise ValueError("deliverables must contain non-blank values")
        return self

    @property
    def all_inputs(self) -> dict[str, InputSpec]:
        return {**self.required_inputs, **self.optional_inputs}


def evaluate_condition(expression: ConditionExpression, values: dict[str, Any]) -> bool:
    if expression.all is not None:
        return all(evaluate_condition(child, values) for child in expression.all)
    if expression.any is not None:
        return any(evaluate_condition(child, values) for child in expression.any)
    assert expression.field is not None
    present = expression.field in values
    value = values.get(expression.field)
    if "exists" in expression.model_fields_set:
        return present is bool(expression.exists)
    if not present:
        return False
    if "equals" in expression.model_fields_set:
        return value == expression.equals
    if "not_equals" in expression.model_fields_set:
        return value != expression.not_equals
    if "in_values" in expression.model_fields_set:
        return value in (expression.in_values or [])
    return False

