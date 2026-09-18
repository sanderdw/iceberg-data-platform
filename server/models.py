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


class Membership(Input):
    team: TeamId
    role: Role


class Memberships(Input):
    memberships: list[Membership] = Field(min_length=1, max_length=100)

    @field_validator("memberships")
    @classmethod
    def unique_teams(cls, value):
        teams = [m.team for m in value]
        if len(set(teams)) != len(teams):
            raise ValueError("Select each team only once.")
        return value

    @property
    def teams(self):
        return [m.team for m in self.memberships]

    @property
    def roles(self):
        return {m.team: m.role for m in self.memberships}


class UserInput(Memberships):
    name: Name
