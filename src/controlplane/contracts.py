"""Closed input contracts: actor and source authority come from credentials, never JSON."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Stage = Literal["Lead", "MQL", "SQL", "Opportunity", "Customer"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Attributes(Contract):
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    phone: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    owner: str | None = Field(default=None, min_length=1, max_length=64)
    lifecycle: Stage | None = None
    score: int | None = Field(default=None, strict=True, ge=0, le=100)
    consent: bool | None = Field(default=None, strict=True)
    suppressed: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def explicit_values(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Supply at least one non-null attribute; deletion is a separate governance operation")
        return self

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        # Synthetic identity convention; no provider-specific dot/plus rewriting.
        return value.casefold() if value else value


class SourceEvent(Contract):
    event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    subject: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    sequence: int = Field(strict=True, ge=1)
    attributes: Attributes


class Proposal(Contract):
    request_id: UUID
    customer_id: UUID
    expected_revision: int = Field(strict=True, ge=0)
    action: Literal["sync_profile", "send_marketing", "set_consent"]
    rationale: str = Field(min_length=1, max_length=500)
    template: Literal["welcome", "product_update"] = "welcome"


class Approval(Contract):
    expected_revision: int = Field(strict=True, ge=0)


class Resolution(Contract):
    disposition: Literal["acknowledge", "requeue"]
    note: str = Field(min_length=5, max_length=500)


class Link(Contract):
    source: Literal["crm", "consent", "scoring", "privacy"]
    subject: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    customer_id: UUID
    expected_revision: int = Field(strict=True, ge=0)
    evidence: str = Field(min_length=10, max_length=500)


class Mutation(Contract):
    customer_id: UUID
    action: Literal["sync_profile", "send_marketing"]
    revision: int = Field(strict=True, ge=0)
    payload: dict


class FailurePlan(Contract):
    key: UUID
    modes: list[Literal["429", "500", "422", "timeout_before", "timeout_after"]] = Field(max_length=10)
