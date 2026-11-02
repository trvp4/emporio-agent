"""Loads the store CSVs into typed, indexed in-memory data.

The dataset is tiny (65 products, 20 orders), so an in-memory repository is the
simplest thing that works. Every access goes through `StoreData`, which makes
swapping in a real database later a local change.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from emporio.config import DATA_DIR
from emporio.core.models import Category, Customer, Order, OrderItem, Product, Promotion


def normalize(text: str) -> str:
    """Lowercase, strip accents and collapse spaces. Used for search and matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _opt_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass
class StoreData:
    categories: dict[int, Category]
    products: dict[int, Product]
    customers: dict[int, Customer]
    promotions: list[Promotion]
    orders: dict[int, Order]
    _promos_by_product: dict[int, list[Promotion]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        grouped: dict[int, list[Promotion]] = defaultdict(list)
        for promo in self.promotions:
            grouped[promo.product_id].append(promo)
        self._promos_by_product = dict(grouped)

    def active_promotion(self, product_id: int) -> Promotion | None:
        """The promotion currently valid for a product, if any.

        Only `is_active = 1` counts. If more than one were active at once (not the
        case in the data), the biggest discount wins, since promotions never stack.
        """
        active = [p for p in self._promos_by_product.get(product_id, []) if p.is_active]
        return max(active, key=lambda p: p.discount_percent, default=None)

    def promotions_for(self, product_id: int) -> list[Promotion]:
        return list(self._promos_by_product.get(product_id, []))

    def category_name(self, category_id: int) -> str:
        cat = self.categories.get(category_id)
        return cat.name if cat else "Sem categoria"

    def find_category(self, query: str) -> Category | None:
        """Match a category from free text ("violão", "baterias", "sax"...).

        Tolerant to accents, case and plurals. Keywords are checked in order,
        so more specific ones ("contrabaixo", "violao") win over shorter ones
        they contain ("baixo", "viola").
        """
        tokens = normalize(query).split()
        for keyword, category_id in _CATEGORY_KEYWORDS:
            if any(tok.startswith(keyword) for tok in tokens):
                return self.categories.get(category_id)
        q = normalize(query)
        for cat in self.categories.values():
            if q and q in normalize(cat.name):
                return cat
        return None


# (keyword prefix, category_id), most specific first.
_CATEGORY_KEYWORDS: list[tuple[str, int]] = [
    ("contrabaixo", 8),
    ("violao", 5),
    ("violoes", 5),
    ("violino", 8),
    ("violoncelo", 8),
    ("viola", 8),
    ("cello", 8),
    ("orquestra", 8),
    ("guitarra", 1),
    ("baixo", 2),
    ("bateria", 3),
    ("percuss", 3),
    ("teclado", 4),
    ("piano", 4),
    ("sintetizador", 4),
    ("synth", 4),
    ("midi", 4),
    ("ukulele", 9),
    ("flauta", 6),
    ("clarinete", 6),
    ("sax", 6),
    ("oboe", 6),
    ("madeira", 6),
    ("trompete", 7),
    ("trombone", 7),
    ("tuba", 7),
    ("trompa", 7),
    ("metais", 7),
    ("metal", 7),
]


def load_store(data_dir: Path = DATA_DIR) -> StoreData:
    categories = {
        int(r["category_id"]): Category(
            id=int(r["category_id"]), name=r["name"], description=r["description"]
        )
        for r in _rows(data_dir / "categories.csv")
    }

    products = {}
    for r in _rows(data_dir / "products.csv"):
        products[int(r["product_id"])] = Product(
            id=int(r["product_id"]),
            name=r["name"],
            price=float(r["price_brl"]),
            category_id=int(r["category_id"]),
            description=r["description"],
            stock=int(r["stock_quantity"]),
            status=r["status"],
            specs=json.loads(r["specs"]) if r["specs"] else {},
            created_at=date.fromisoformat(r["created_at"]),
        )

    customers = {
        int(r["customer_id"]): Customer(
            id=int(r["customer_id"]),
            name=r["name"],
            phone=r["phone"],
            email=r["email"],
            city=r["city"],
        )
        for r in _rows(data_dir / "customers.csv")
    }

    promotions = [
        Promotion(
            id=int(r["promotion_id"]),
            product_id=int(r["product_id"]),
            discount_percent=float(r["discount_percent"]),
            description=r["description"],
            is_active=r["is_active"] == "1",
        )
        for r in _rows(data_dir / "promotions.csv")
    ]

    items: dict[int, list[OrderItem]] = defaultdict(list)
    for r in _rows(data_dir / "order_items.csv"):
        items[int(r["order_id"])].append(
            OrderItem(product_id=int(r["product_id"]), quantity=int(r["quantity"]))
        )

    orders = {}
    for r in _rows(data_dir / "orders.csv"):
        oid = int(r["order_id"])
        orders[oid] = Order(
            id=oid,
            customer_id=int(r["customer_id"]),
            order_date=date.fromisoformat(r["order_date"]),
            status=r["status"],
            total=float(r["total_brl"]),
            payment_method=r["payment_method"],
            tracking_code=r["tracking_code"] or None,
            estimated_delivery=_opt_date(r["estimated_delivery"]),
            notes=r["notes"] or None,
            items=items.get(oid, []),
        )

    return StoreData(
        categories=categories,
        products=products,
        customers=customers,
        promotions=promotions,
        orders=orders,
    )


@lru_cache(maxsize=1)
def get_store() -> StoreData:
    """Process-wide cached store, loaded once."""
    return load_store()
