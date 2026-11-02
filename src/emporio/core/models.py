"""Typed domain models for the store data."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

ProductStatus = Literal["active", "discontinued", "coming_soon"]
OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]


class Category(BaseModel):
    id: int
    name: str
    description: str


class Product(BaseModel):
    id: int
    name: str
    price: float
    category_id: int
    description: str
    stock: int
    status: ProductStatus
    specs: dict[str, str] = Field(default_factory=dict)
    created_at: date

    @property
    def is_available(self) -> bool:
        """Can be sold right now: active in the catalog and in stock."""
        return self.status == "active" and self.stock > 0


class Customer(BaseModel):
    id: int
    name: str
    phone: str
    email: str
    city: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


class Promotion(BaseModel):
    id: int
    product_id: int
    discount_percent: float
    description: str
    is_active: bool


class OrderItem(BaseModel):
    product_id: int
    quantity: int


class Order(BaseModel):
    id: int
    customer_id: int
    order_date: date
    status: OrderStatus
    total: float
    payment_method: str
    tracking_code: str | None = None
    estimated_delivery: date | None = None
    notes: str | None = None
    items: list[OrderItem] = Field(default_factory=list)
