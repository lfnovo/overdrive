from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .stages import Stage


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Unit(Model):
    name: str = Field(min_length=1, max_length=150)
    archived: bool = False


class Source(Model):
    name: str = Field(min_length=1, max_length=150)
    archived: bool = False


class User(Model):
    name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=254)
    active: bool = True
    admin: bool = False
    units: list[str] = []

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        if "@" not in value or any(c.isspace() for c in value):
            raise ValueError("Invalid email")
        return value.lower()


class Organization(Model):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(default="", max_length=200)
    archived: bool = False


class Contact(Model):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=60)
    organization: str | None = None
    archived: bool = False


class Deal(Model):
    title: str = Field(min_length=1, max_length=200)
    deal_so_far: str = Field(
        default="",
        max_length=50000,
        description="Living Markdown summary: context, current status, decisions, open questions and next steps. Updated by humans or agents; not an event log.",
    )
    unit: str | None = None
    organization: str | None = None
    source: str | None = None
    owner: str | None = None
    stage: Stage = "New Lead"
    outcome: Literal["open", "won", "lost"] = "open"
    loss_reason: str = Field(default="", max_length=2000)
    value_cents: int = Field(default=0, ge=0, le=10**14)
    expected_close: date | None = None
    archived: bool = False


class Task(Model):
    deal: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    owner: str
    due: date | None = None
    done: bool = False
    primary: bool = False


class Note(Model):
    deal: str
    content: str = Field(min_length=1, max_length=100000)


class Attachment(Model):
    deal: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    url: str = Field(default="", max_length=2000)
    deprecated: bool = False

    @field_validator("url")
    @classmethod
    def valid_link(cls, value):
        from urllib.parse import urlparse

        if value:
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or any(c.isspace() or ord(c) < 32 for c in value)
            ):
                raise ValueError("Use a valid HTTP or HTTPS link without embedded credentials")
        return value


MODELS = {
    "unit": Unit,
    "source": Source,
    "user": User,
    "organization": Organization,
    "contact": Contact,
    "deal": Deal,
    "task": Task,
    "note": Note,
    "attachment": Attachment,
}
REFERENCES = {
    "unit": "unit",
    "source": "source",
    "organization": "organization",
    "owner": "user",
    "deal": "deal",
    "created_by": "user",
    # Legacy archive/backup references; not accepted by commercial models.
    "attachment": "attachment",
    "current_proposal": "proposal_version",
}


class SaveEnvelope(Model):
    data: dict
    version: int | None = Field(default=None, ge=1)
    context_deal: str | None = None
