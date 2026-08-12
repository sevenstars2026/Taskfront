from __future__ import annotations

import re
import unicodedata
from enum import Enum
from string import Formatter
from typing import Any, Literal

from pydantic import Field, model_validator
from jsonschema import Draft202012Validator

from taskc.versions import validate_semver

from .common import JsonObject, JsonValue, StrictModel

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED_INPUTS = {
    "permission",
    "approval",
    "policy",
    "credential",
    "executable_code",
    "workflow_bytecode",
}
_QUESTION_FIELDS = {"field", "description", "choices"}
_EXTENSION_NAME = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_MAX_CONDITION_DEPTH = 10
_MAX_CONDITION_NODES = 100
_VALUE_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "additionalProperties",
}


class TruthValue(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


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


def _validate_value_schema(schema: JsonObject, *, depth: int = 0) -> None:
    if depth > 10:
        raise ValueError("value_schema exceeds the maximum depth")
    unknown = set(schema) - _VALUE_SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"value_schema contains unsupported keywords: {sorted(unknown)}")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ValueError("value_schema properties must be an object")
        for child in properties.values():
            if not isinstance(child, dict):
                raise ValueError("value_schema property definitions must be objects")
            _validate_value_schema(child, depth=depth + 1)
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise ValueError("value_schema items must be an object")
        _validate_value_schema(items, depth=depth + 1)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        _validate_value_schema(additional, depth=depth + 1)


class InputSpec(StrictModel):
    type: Literal["string", "integer", "number", "boolean", "array", "object", "enum"]
    description: str
    items: Literal["string", "integer", "number", "boolean", "object"] | None = None
    enum: list[JsonValue] | None = None
    enum_aliases: dict[str, JsonValue] = Field(default_factory=dict)
    value_schema: JsonObject | None = None
    source_policy: Literal["any", "trusted", "user_confirmed"] = "any"
    default: JsonValue | None = None
    classification: Literal["public", "sensitive", "secret"] = "public"
    question_template: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "InputSpec":
        if not self.description.strip():
            raise ValueError("description must not be blank")
        if self.type == "array" and self.items is None and self.value_schema is None:
            raise ValueError("array inputs must declare items or value_schema")
        if self.type != "array" and self.items is not None:
            raise ValueError("items is only valid for array inputs")
        if self.type == "enum" and not self.enum:
            raise ValueError("enum inputs must declare at least one value")
        if self.type != "enum" and (self.enum is not None or self.enum_aliases):
            raise ValueError("enum and enum_aliases are only valid for enum inputs")
        if self.enum is not None:
            canonical = {repr(item) for item in self.enum}
            if len(canonical) != len(self.enum):
                raise ValueError("enum values must be unique")
            invalid_aliases = [key for key, value in self.enum_aliases.items() if value not in self.enum]
            if invalid_aliases:
                raise ValueError(f"enum aliases target undeclared values: {sorted(invalid_aliases)}")
            normalized_aliases = {
                unicodedata.normalize("NFC", key): value for key, value in self.enum_aliases.items()
            }
            if len(normalized_aliases) != len(self.enum_aliases):
                raise ValueError("enum aliases must remain unique after Unicode NFC normalization")
            object.__setattr__(self, "enum_aliases", normalized_aliases)
        if self.value_schema is not None:
            value_schema = dict(self.value_schema)
            if self.type == "object":
                value_schema.setdefault("additionalProperties", False)
            _validate_value_schema(value_schema)
            try:
                Draft202012Validator.check_schema(value_schema)
            except Exception as exc:
                raise ValueError("value_schema is not a valid Draft 2020-12 schema") from exc
            object.__setattr__(self, "value_schema", value_schema)
        if "default" in self.model_fields_set:
            if self.classification == "secret":
                raise ValueError("secret inputs cannot declare literal defaults")
            if not value_matches_type(self.default, self.type, self.items):
                raise ValueError("default does not match the declared input type")
            if self.type == "enum" and self.default not in (self.enum or []):
                raise ValueError("default is not a declared enum value")
        if self.question_template is not None:
            if not self.question_template.strip():
                raise ValueError("question_template must not be blank")
            placeholders = {
                field_name for _, field_name, _, _ in Formatter().parse(self.question_template)
                if field_name is not None
            }
            unknown = placeholders - _QUESTION_FIELDS
            if unknown:
                raise ValueError(f"question_template uses unsupported placeholders: {sorted(unknown)}")
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
        result: set[str] = set()
        for child in self.all if self.all is not None else self.any or []:
            result.update(child.referenced_fields())
        return result

    def leaves(self) -> list["ConditionExpression"]:
        if self.field:
            return [self]
        result: list[ConditionExpression] = []
        for child in self.all if self.all is not None else self.any or []:
            result.extend(child.leaves())
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
    when: ConditionExpression | None = None
    assert_condition: ConditionExpression | None = Field(default=None, alias="assert")
    evaluator: str | None = None
    evaluator_options: JsonObject = Field(default_factory=dict)
    targets: list[str]
    message: str
    blocking: bool = True

    @model_validator(mode="after")
    def validate_constraint(self) -> "ConstraintSpec":
        if not self.code.startswith("GAP-CONSTRAINT-"):
            raise ValueError("constraint code must start with GAP-CONSTRAINT-")
        if not self.message.strip():
            raise ValueError("constraint message must not be blank")
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("constraint targets must be unique")
        if self.blocking and not self.targets:
            raise ValueError("blocking constraints must declare at least one target")
        if (self.assert_condition is None) == (self.evaluator is None):
            raise ValueError("constraint must declare exactly one of assert or evaluator")
        if self.evaluator is not None and not _EXTENSION_NAME.fullmatch(self.evaluator):
            raise ValueError("constraint evaluator must be a stable extension name")
        return self


def _condition_size(expression: ConditionExpression) -> tuple[int, int]:
    children = expression.all if expression.all is not None else expression.any or []
    if not children:
        return 1, 1
    child_sizes = [_condition_size(child) for child in children]
    return 1 + max(depth for depth, _ in child_sizes), 1 + sum(
        nodes for _, nodes in child_sizes
    )


def _validate_condition_size(expression: ConditionExpression) -> None:
    depth, nodes = _condition_size(expression)
    if depth > _MAX_CONDITION_DEPTH:
        raise ValueError(f"condition depth exceeds {_MAX_CONDITION_DEPTH}")
    if nodes > _MAX_CONDITION_NODES:
        raise ValueError(f"condition node count exceeds {_MAX_CONDITION_NODES}")


def _validate_condition_operands(expression: ConditionExpression, inputs: dict[str, InputSpec]) -> None:
    for leaf in expression.leaves():
        assert leaf.field is not None
        spec = inputs[leaf.field]
        operands: list[JsonValue] = []
        if "equals" in leaf.model_fields_set:
            operands = [leaf.equals]
        elif "not_equals" in leaf.model_fields_set:
            operands = [leaf.not_equals]
        elif "in_values" in leaf.model_fields_set:
            operands = list(leaf.in_values or [])
            if not operands:
                raise ValueError("condition in operator must not be empty")
        for operand in operands:
            if not value_matches_type(operand, spec.type, spec.items):
                raise ValueError(f"condition operand for {leaf.field} does not match its input type")
            if spec.type == "enum" and operand not in (spec.enum or []):
                raise ValueError(f"condition operand for {leaf.field} is not a declared enum value")


def _validate_obvious_contradictions(expression: ConditionExpression) -> None:
    if expression.all is None:
        for child in expression.any or []:
            _validate_obvious_contradictions(child)
        return
    equals_by_field: dict[str, set[str]] = {}
    not_equals_by_field: dict[str, set[str]] = {}
    for child in expression.all:
        if child.field and "equals" in child.model_fields_set:
            equals_by_field.setdefault(child.field, set()).add(repr(child.equals))
        if child.field and "not_equals" in child.model_fields_set:
            not_equals_by_field.setdefault(child.field, set()).add(repr(child.not_equals))
        _validate_obvious_contradictions(child)
    for field_name, values in equals_by_field.items():
        if len(values) > 1 or values & not_equals_by_field.get(field_name, set()):
            raise ValueError(f"condition contains an obvious contradiction for {field_name}")


class CapabilityContract(StrictModel):
    schema_version: Literal["0.2"]
    id: str
    version: str
    description: str
    examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    required_inputs: dict[str, InputSpec]
    optional_inputs: dict[str, InputSpec] = Field(default_factory=dict)
    conditional_requirements: list[ConditionalRequirement] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    question_ranker: str | None = None
    deliverables: list[str]

    @model_validator(mode="after")
    def validate_contract(self) -> "CapabilityContract":
        if not _CAPABILITY_ID.fullmatch(self.id):
            raise ValueError("id must be a lower-case dot-separated capability name")
        validate_semver(self.version)
        if not self.description.strip():
            raise ValueError("description must not be blank")
        declared = set(self.required_inputs) | set(self.optional_inputs)
        invalid_names = sorted(name for name in declared if not _INPUT_NAME.fullmatch(name))
        if invalid_names:
            raise ValueError(f"input names are invalid: {invalid_names}")
        reserved = sorted(declared & _RESERVED_INPUTS)
        if reserved:
            raise ValueError(f"contract declares reserved input names: {reserved}")
        overlap = set(self.required_inputs) & set(self.optional_inputs)
        if overlap:
            raise ValueError(f"required_inputs and optional_inputs overlap: {sorted(overlap)}")
        inputs = self.all_inputs
        if self.question_ranker is not None and not _EXTENSION_NAME.fullmatch(self.question_ranker):
            raise ValueError("question_ranker must be a stable extension name")
        for requirement in self.conditional_requirements:
            unknown_refs = requirement.when.referenced_fields() - declared
            unknown_required = set(requirement.require) - declared
            if unknown_refs:
                raise ValueError(f"condition references unknown fields: {sorted(unknown_refs)}")
            if unknown_required:
                raise ValueError(f"condition requires unknown fields: {sorted(unknown_required)}")
            _validate_condition_operands(requirement.when, inputs)
            _validate_obvious_contradictions(requirement.when)
            _validate_condition_size(requirement.when)
        for constraint in self.constraints:
            expressions = (
                [constraint.assert_condition] if constraint.assert_condition is not None else []
            )
            if constraint.when is not None:
                expressions.append(constraint.when)
            for expression in expressions:
                assert expression is not None
                unknown_refs = expression.referenced_fields() - declared
                if unknown_refs:
                    raise ValueError(f"constraint references unknown fields: {sorted(unknown_refs)}")
                _validate_condition_operands(expression, inputs)
                _validate_obvious_contradictions(expression)
                _validate_condition_size(expression)
            unknown_targets = set(constraint.targets) - declared
            if unknown_targets:
                raise ValueError(f"constraint targets unknown fields: {sorted(unknown_targets)}")
        if not self.deliverables or any(not item.strip() for item in self.deliverables):
            raise ValueError("deliverables must contain non-blank values")
        if any(not item.strip() for item in [*self.examples, *self.negative_examples]):
            raise ValueError("examples must not contain blank values")
        return self

    @property
    def all_inputs(self) -> dict[str, InputSpec]:
        return {**self.required_inputs, **self.optional_inputs}


def evaluate_condition(expression: ConditionExpression, values: dict[str, Any]) -> TruthValue:
    if expression.all is not None:
        children = [evaluate_condition(child, values) for child in expression.all]
        if TruthValue.FALSE in children:
            return TruthValue.FALSE
        return TruthValue.TRUE if all(item is TruthValue.TRUE for item in children) else TruthValue.UNKNOWN
    if expression.any is not None:
        children = [evaluate_condition(child, values) for child in expression.any]
        if TruthValue.TRUE in children:
            return TruthValue.TRUE
        return TruthValue.FALSE if all(item is TruthValue.FALSE for item in children) else TruthValue.UNKNOWN
    assert expression.field is not None
    present = expression.field in values
    value = values.get(expression.field)
    if "exists" in expression.model_fields_set:
        return TruthValue.TRUE if present is bool(expression.exists) else TruthValue.FALSE
    if not present:
        return TruthValue.UNKNOWN
    if "equals" in expression.model_fields_set:
        return TruthValue.TRUE if value == expression.equals else TruthValue.FALSE
    if "not_equals" in expression.model_fields_set:
        return TruthValue.TRUE if value != expression.not_equals else TruthValue.FALSE
    if "in_values" in expression.model_fields_set:
        return TruthValue.TRUE if value in (expression.in_values or []) else TruthValue.FALSE
    return TruthValue.UNKNOWN


__all__ = [
    "CapabilityContract",
    "ConditionalRequirement",
    "ConditionExpression",
    "ConstraintSpec",
    "InputSpec",
    "TruthValue",
    "evaluate_condition",
    "value_matches_type",
]
