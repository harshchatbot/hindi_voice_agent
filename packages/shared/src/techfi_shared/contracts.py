from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SessionScope(BaseModel):
    """Opaque internal identifiers; never derive them from customer contact data."""

    model_config = ConfigDict(frozen=True)
    tenant_id: UUID
    session_id: UUID = Field(default_factory=uuid4)


class CallOutcome(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    OPTED_OUT = "opted_out"
    TIMED_OUT = "timed_out"


class CallResult(BaseModel):
    scope: SessionScope
    outcome: CallOutcome


class CallAuthorization(Protocol):
    """Future policy boundary: consent, DND and tenant checks before a transport opens."""

    async def authorize(self, scope: SessionScope) -> None: ...
