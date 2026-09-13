from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$")]
Environment = Literal["development", "acceptance", "production"]
ENVIRONMENTS = ("development", "acceptance", "production")
Role = Literal["reader", "writer", "admin", "bucket-admin"]


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    password: str = Field(max_length=1024)


class TeamInput(Input):
    name: Name
    description: str = Field(default="", max_length=280)


class DatabaseInput(Input):
    name: Name
    team: TeamId
    environment: Environment = "development"
    description: str = Field(default="", max_length=280)


class DatabaseMove(Input):
    team: TeamId


class Memberships(Input):
    teams: list[TeamId] = Field(min_length=1, max_length=100)

    @field_validator("teams")
    @classmethod
    def unique_teams(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Select each team only once.")
        return value


class UserInput(Memberships):
    name: Name
    role: Role


class RoleInput(Input):
    role: Role
