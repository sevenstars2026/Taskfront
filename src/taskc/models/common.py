from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeAliasType

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
JsonPrimitive = str | int | FiniteFloat | bool | None
JsonValue = TypeAliasType(
    "JsonValue",
    JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"],
)
JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    """Base class for all public wire models."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )
