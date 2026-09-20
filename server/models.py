from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,47}$")]
TeamId = Annotated[str, Field(pattern=r"^team-[a-f0-9]{32}$")]
DatabaseId = Annotated[str, Field(pattern=r"^db-[a-f0-9]{32}$")]
ShareId = Annotated[str, Field(pattern=r"^share-[a-f0-9]{32}$")]
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


def identifier_part(value):
    if value in (".", "..") or "\x1f" in value or "\0" in value:
        raise ValueError("Invalid identifier.")
    return value


class ShareObject(Input):
    kind: Literal["table", "view"]
    namespace: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=256)

    @field_validator("namespace")
    @classmethod
    def namespace_parts(cls, value):
        return [identifier_part(part) for part in value]

    @field_validator("name")
    @classmethod
    def object_name(cls, value):
        return identifier_part(value)


def share_objects(value):
    if value is None:
        return value
    keys = [(o.kind, tuple(o.namespace), o.name) for o in value]
    if len(set(keys)) != len(keys):
        raise ValueError("Select each table or view only once.")
    # A view is only a definition: the recipient's engine reads its tables with the same credential.
    if not any(o.kind == "table" for o in value):
        raise ValueError("Select the tables a shared view reads.")
    return sorted(value, key=lambda o: (o.namespace, o.name, o.kind))


def share_expiry(value):
    if value is None:
        return value
    if value.tzinfo is None or value <= datetime.now(UTC):
        raise ValueError("Choose an expiry in the future, with a time zone.")
    return value.astimezone(UTC).replace(microsecond=0)


class ShareInput(Input):
    database: DatabaseId
    name: Name
    recipient: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=280)
    objects: list[ShareObject] = Field(min_length=1, max_length=50)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    objects_valid = field_validator("objects")(share_objects)
    expiry_valid = field_validator("expires_at")(share_expiry)


class ShareUpdate(Input):
    """Only the supplied fields change; `expiresAt: null` removes the expiry."""

    recipient: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=280)
    objects: list[ShareObject] | None = Field(default=None, min_length=1, max_length=50)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    objects_valid = field_validator("objects")(share_objects)
    expiry_valid = field_validator("expires_at")(share_expiry)
