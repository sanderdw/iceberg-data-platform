"""What one agent run knows: the shared UI state, and server-side context the browser never sets.

AG-UI sends the UI's state with every run. It only holds which model is selected and the last
result id; both are re-checked against the Bridge before use, so a forged state reads nothing
more than the person could read anyway.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..semantic.compiler import ModelRef


class CbiState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: ModelRef | None = None
    lastResultId: str | None = Field(default=None, pattern=r"^res-[a-f0-9]{24}$")

    @field_validator("model", "lastResultId", mode="wrap")
    @classmethod
    def forgiving(cls, value, handler):
        # An outdated or tampered UI state starts over instead of failing the run.
        try:
            return handler(value)
        except ValidationError:
            return None


@dataclass
class CbiDeps:
    state: CbiState
    caller: Any = field(repr=False)
    services: Any = field(repr=False)
    me: dict | None = field(default=None, repr=False)
