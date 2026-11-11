import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sku = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Item(Model):
    sku: Sku
    quantity: Annotated[int, Field(strict=True, ge=1, le=1000)]


class OrderRequest(Model):
    customer_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    items: Annotated[list[Item], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def unique_skus(self):
        if len({item.sku for item in self.items}) != len(self.items):
            raise ValueError("Duplicate SKUs are not allowed")
        return self

    def fingerprint(self) -> str:
        data = self.model_dump()
        data["items"] = sorted(data["items"], key=lambda item: item["sku"])
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Product(Model):
    sku: Sku
    name: str
    unit_price_cents: int = Field(ge=0)
    available: int = Field(ge=0)
    currency: Literal["INR"] = "INR"


class Reservation(Model):
    status: Literal["confirmed", "rejected"]
    total_cents: int = Field(ge=0)
    reason: str | None = None


class Order(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    request: OrderRequest
    status: Literal["pending", "confirmed", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_cents: int | None = None
    currency: Literal["INR"] = "INR"
    reason: str | None = None


class Event(Model):
    id: str
    type: Literal["order.created.v1"] = "order.created.v1"
    order_id: str
    occurred_at: datetime
    schema_version: Literal[1] = 1


def order_event(order: Order) -> Event:
    return Event(id=f"order-created-{order.id}", order_id=order.id, occurred_at=order.created_at)


class Conflict(Exception):
    """An idempotency key was reused with different input."""


class NotFound(Exception):
    """A resource does not exist."""
