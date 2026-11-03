"""The agent's tools.

Each tool is a plain function with a Pydantic input model. The same registry is
used by the LLM loop (tool use), the evaluation suite and the tests. Tools return
JSON-serializable dicts; money comes both as a number and already formatted
(`R$ 1.299,00`) so the model can quote it verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from emporio.config import RUNTIME_DIR, store_now
from emporio.core import rules
from emporio.core.data import BRANDS, StoreData, brands_in, digits, get_store, normalize
from emporio.core.models import Order, Product

STATUS_PT = {
    "pending": "aguardando pagamento",
    "confirmed": "pagamento confirmado, em separação",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
PAYMENT_PT = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto",
}
STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "com",
    "para", "pra", "por", "ate", "que", "tem", "quero", "voces", "vcs", "algum", "alguma",
}  # fmt: skip

STORE_CONTACT = {
    "name": "Empório da Música",
    "address": "Rua 14 de Maio, 3200, Centro, Campo Grande - MS, CEP 79202-333",
    "phone_whatsapp": "(67) 3341-4444",
    "email": "contato@emporiodamusica.com.br",
}


def _payment_label(method: str) -> str:
    if method.startswith("credit_"):
        return f"cartão de crédito em {method.removeprefix('credit_')}"
    return PAYMENT_PT.get(method, method)


def _availability(p: Product) -> str:
    if p.status == "discontinued":
        return "descontinuado"
    if p.status == "coming_soon":
        return "em breve (ainda não disponível para venda)"
    if p.stock == 0:
        return "esgotado no momento"
    return f"disponível ({p.stock} em estoque)"


def _price_block(store: StoreData, p: Product) -> dict[str, Any]:
    promo = store.active_promotion(p.id)
    q = rules.price_quote(p, promo)
    block: dict[str, Any] = {
        "price": q.price,
        "price_text": rules.brl(q.price),
        "pix_price_text": rules.brl(q.pix_price),
        "pix_discount_applies": q.pix_discount_applies,
    }
    if promo:
        block["promotion"] = {
            "name": q.promotion_name,
            "discount_percent": q.promotion_percent,
            "original_price_text": rules.brl(q.list_price),
        }
    return block


def _summary(store: StoreData, p: Product) -> dict[str, Any]:
    return {
        "product_id": p.id,
        "name": p.name,
        "category": store.category_name(p.category_id),
        "availability": _availability(p),
        **_price_block(store, p),
    }


def _contains(text: str, token: str) -> bool:
    if token in text:
        return True
    # Model codes are written many ways ("GD20", "gd 20", "GF-3D"): compare them with
    # spaces and hyphens removed. Only codes, or "'50s guitarra" would contain "sg".
    if any(c.isdigit() for c in token) or "-" in token:
        return token.replace("-", "") in text.replace(" ", "").replace("-", "")
    return False


def _match_score(store: StoreData, p: Product, tokens: list[str]) -> float:
    """Share of the query tokens found in the product.

    Brand names match the product name only: some descriptions cite another brand
    (#135 "Music Man Bass 1X" is described as a Fender Jazz Bass).
    """
    if not tokens:
        return 1.0
    name = normalize(p.name)
    haystack = normalize(
        " ".join([p.name, p.description, store.category_name(p.category_id), *p.specs.values()])
    )
    hits = sum(1 for t in tokens if _contains(name if t in BRANDS else haystack, t))
    return hits / len(tokens)


def _name_score(p: Product, tokens: list[str]) -> float:
    name = normalize(p.name)
    return sum(1 for t in tokens if _contains(name, t)) / len(tokens) if tokens else 0.0


def _tokens(text: str | None) -> list[str]:
    return [t for t in normalize(text or "").split() if t not in STOPWORDS]


def _alternatives(store: StoreData, p: Product, limit: int = 3) -> list[dict[str, Any]]:
    """Available products in the same category, closest in price."""
    candidates = [
        q for q in store.products.values()
        if q.id != p.id and q.category_id == p.category_id and q.is_available
    ]  # fmt: skip
    candidates.sort(key=lambda q: abs(q.price - p.price))
    return [_summary(store, q) for q in candidates[:limit]]


# --- Identity verification ---------------------------------------------------


def _verify(store: StoreData, order_id: int, email: str | None, phone: str | None):
    """Return (order, customer) only if the contact matches the order's customer.

    The failure message is the same whether the order exists or not, so the tool
    never reveals which orders exist (LGPD).
    """
    fail = {
        "verified": False,
        "message": "Não foi possível confirmar os dados. Confira o número do pedido e o e-mail "
        "ou telefone usados na compra.",
    }
    if not email and not phone:
        return None, {
            "verified": False,
            "message": "Para consultar o pedido, peça o número do pedido e o e-mail ou o "
            "telefone usado na compra.",
        }
    order = store.orders.get(order_id)
    customer = store.customers.get(order.customer_id) if order else None
    if not order or not customer:
        return None, fail
    email_ok = bool(email) and email.strip().lower() == customer.email.lower()
    # With the area code (10+ digits) all of it must match; without it, the last 8.
    phone_digits = digits(phone or "")
    if len(phone_digits) > 11 and phone_digits.startswith("55"):  # +55 country code
        phone_digits = phone_digits[2:]
    tail = phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits[-8:]
    phone_ok = len(phone_digits) >= 8 and digits(customer.phone).endswith(tail)
    if not (email_ok or phone_ok):
        return None, fail
    return (order, customer), None


def _order_items(store: StoreData, order: Order) -> list[dict[str, Any]]:
    """Name and category: without the category the model guessed a Kala ukulele was a guitar."""
    items = []
    for it in order.items:
        product = store.products.get(it.product_id)
        items.append(
            {
                "product_id": it.product_id,
                "name": product.name if product else "?",
                "category": store.category_name(product.category_id) if product else "?",
                "quantity": it.quantity,
            }
        )
    return items


# --- Tool inputs -------------------------------------------------------------


class SearchProductsInput(BaseModel):
    query: str | None = Field(
        None, description="Free text: brand, model or feature (ex.: 'Takamine', 'nylon elétrico')."
    )
    category: str | None = Field(
        None, description="Category in free text (ex.: 'violões', 'guitarra', 'ukulele')."
    )
    min_price: float | None = Field(None, description="Minimum price in BRL.")
    max_price: float | None = Field(None, description="Maximum price in BRL.")
    include_unavailable: bool = Field(
        False, description="Also list sold-out, discontinued and coming-soon products."
    )
    limit: int = Field(5, ge=1, le=20, description="Max number of products to return.")


class GetProductInput(BaseModel):
    product_id: int | None = Field(None, description="Product id, when known.")
    name: str | None = Field(None, description="Product name or model, when the id is unknown.")


class OrderAuthInput(BaseModel):
    order_id: int = Field(description="Order number given by the customer.")
    email: str | None = Field(None, description="E-mail used in the purchase.")
    phone: str | None = Field(None, description="Phone used in the purchase.")


class ReturnInput(OrderAuthInput):
    reason: Literal["arrependimento", "defeito", "preferencia"] = Field(
        description="arrependimento = changed their mind; defeito = manufacturing defect; "
        "preferencia = wants another color/model/size."
    )


class SimulatePaymentInput(BaseModel):
    product_id: int | None = Field(
        None,
        ge=1,
        description="Product to price (uses its current price and promotion). Always prefer "
        "this for a single product. Never guess it: take it from search_products or "
        "get_product, and wait for that result before calling this tool.",
    )
    amount: float | None = Field(
        None,
        description="Arbitrary amount in BRL when there is no single product (e.g. a cart "
        "total). The 5% PIX discount is computed on the whole amount.",
    )
    installments: int | None = Field(
        None, description="Number of credit card installments the customer asked for."
    )


class StoreInfoInput(BaseModel):
    city: str | None = Field(None, description="Delivery city, to quote shipping.")
    order_value: float | None = Field(None, description="Order value in BRL, to quote shipping.")
    product_ids: list[int] | None = Field(
        None, description="Products being shipped, so large instruments get the special note."
    )


class EscalateInput(BaseModel):
    category: Literal[
        "reclamacao", "pedido_atrasado", "troca_devolucao", "fora_da_politica", "outro"
    ] = Field(description="Kind of case.")
    summary: str = Field(description="Short summary for the team, in Portuguese.")
    customer_name: str | None = None
    contact: str | None = Field(None, description="Customer phone or e-mail, if given.")
    order_id: int | None = None


# --- Tool functions ----------------------------------------------------------


def search_products(args: SearchProductsInput, store: StoreData) -> dict[str, Any]:
    category = store.find_category(args.category) if args.category else None
    if args.category and category is None:
        return {
            "results": [],
            "message": f"Não encontrei a categoria '{args.category}'. Categorias: "
            + ", ".join(c.name for c in store.categories.values()),
        }

    tokens = _tokens(args.query)
    threshold = 1.0 if len(tokens) <= 1 else 0.5
    scored: list[tuple[float, Product]] = []
    hidden = 0
    for p in store.products.values():
        if category and p.category_id != category.id:
            continue
        price = rules.price_quote(p, store.active_promotion(p.id)).price
        if args.min_price is not None and price < args.min_price:
            continue
        if args.max_price is not None and price > args.max_price:
            continue
        score = _match_score(store, p, tokens)
        if score < threshold:
            continue
        if not p.is_available and not args.include_unavailable:
            hidden += 1
            continue
        scored.append((score, p))

    # Best text match first; ties (or no text) by price, cheapest first.
    scored.sort(key=lambda sp: (-sp[0], sp[1].price))
    results = [_summary(store, p) for _, p in scored[: args.limit]]
    out: dict[str, Any] = {
        "total_matches": len(scored),
        "returned": len(results),
        "results": results,
    }
    if category:
        out["category"] = category.name
        if not any(p.category_id == category.id for p in store.products.values()):
            out["message"] = "No momento não temos produtos nessa categoria."
    if hidden:
        out["unavailable_hidden"] = hidden
    if not scored and (args.min_price is not None or args.max_price is not None):
        out.update(_outside_price_range(store, args, category, tokens, threshold))
    return out


def _outside_price_range(
    store: StoreData, args: SearchProductsInput, category, tokens: list[str], threshold: float
) -> dict[str, Any]:
    """When a budget leaves nothing, say what exists, so "no guitars" is never the answer."""
    available = [
        p for p in store.products.values()
        if p.is_available
        and (category is None or p.category_id == category.id)
        and _match_score(store, p, tokens) >= threshold
    ]  # fmt: skip
    if not available:
        return {}
    prices = {p.id: rules.price_quote(p, store.active_promotion(p.id)).price for p in available}
    low, high = min(prices.values()), max(prices.values())
    target = args.max_price if args.max_price is not None else args.min_price
    closest = sorted(available, key=lambda p: abs(prices[p.id] - target))[:3]
    return {
        "message": f"Nada nessa faixa de preço, mas há {len(available)} produtos disponíveis "
        f"aqui, de {rules.brl(low)} a {rules.brl(high)}. Os mais próximos do orçamento estão "
        "em closest_outside_range.",
        "closest_outside_range": [_summary(store, p) for p in closest],
    }


def get_product(args: GetProductInput, store: StoreData) -> dict[str, Any]:
    product = store.products.get(args.product_id) if args.product_id else None
    if product is None and args.name:
        # Only an unambiguous, complete match counts as found: "Gibson SG" must not
        # come back as the Les Paul just because "Gibson" matched.
        tokens = _tokens(args.name)
        full = [p for p in store.products.values() if _match_score(store, p, tokens) >= 1.0]
        by_name = [p for p in full if _name_score(p, tokens) >= 1.0]
        if len(full) == 1:
            product = full[0]
        elif len(by_name) == 1:
            product = by_name[0]
        elif full:
            options = [p for p in (by_name or full) if p.is_available][:5]
            return {
                "found": False,
                "message": "Mais de um produto corresponde a esse nome. Pergunte qual é.",
                "candidates": [_summary(store, p) for p in options],
            }
    if product is None:
        out: dict[str, Any] = {"found": False, "message": "Produto não encontrado no catálogo."}
        if args.name:
            tokens = _tokens(args.name)
            close = sorted(
                (p for p in store.products.values() if p.is_available),
                key=lambda p: -_match_score(store, p, tokens),
            )
            close = [p for p in close[:3] if _match_score(store, p, tokens) >= 0.5]
            if close:
                out["similar_available"] = [_summary(store, p) for p in close]
        return out

    out: dict[str, Any] = {
        "found": True,
        "product_id": product.id,
        "name": product.name,
        "category": store.category_name(product.category_id),
        "description": product.description,
        "specs": product.specs,
        "availability": _availability(product),
        "can_be_sold": product.is_available,
    }
    named, described = brands_in(product.name), brands_in(product.description)
    if named and described and not named & described:
        other = ", ".join(b.title() for b in sorted(described))
        out["description_warning"] = (
            f"A descrição cita outra marca ({other}). O nome do produto é o registro oficial: "
            "não atribua a ele a marca da descrição."
        )
    if product.status != "discontinued":
        out.update(_price_block(store, product))
    expired = [p for p in store.promotions_for(product.id) if not p.is_active]
    if expired:
        out["expired_promotions"] = [
            {"name": p.description, "discount_percent": p.discount_percent} for p in expired
        ]
    if not product.is_available:
        out["alternatives"] = _alternatives(store, product)
        out["alternatives_basis"] = (
            "Mesma categoria, à venda, ordenadas pelo preço mais próximo. Não indicam "
            "semelhança de modelo, som ou especificação."
        )
    return out


def get_order(args: OrderAuthInput, store: StoreData) -> dict[str, Any]:
    found, error = _verify(store, args.order_id, args.email, args.phone)
    if error:
        return error
    order, customer = found
    today = store_now().date()
    out: dict[str, Any] = {
        "verified": True,
        "customer_first_name": customer.first_name,
        "order_id": order.id,
        "order_date": f"{order.order_date:%d/%m/%Y}",
        "status": STATUS_PT.get(order.status, order.status),
        "items": _order_items(store, order),
        "total_text": rules.brl(order.total),
        "payment": _payment_label(order.payment_method),
    }
    if order.tracking_code:
        out["tracking_code"] = order.tracking_code
        out["tracking_where"] = "site dos Correios ou da Jadlog"
    if order.estimated_delivery:
        out["estimated_delivery"] = f"{order.estimated_delivery:%d/%m/%Y}"
        if order.status == "shipped" and order.estimated_delivery < today:
            out["late"] = True
            out["days_late"] = (today - order.estimated_delivery).days
            out["instruction"] = "Pedido atrasado: peça desculpas e abra um chamado para a equipe."
    if order.notes:
        out["notes"] = order.notes
    return out


def check_return_eligibility(args: ReturnInput, store: StoreData) -> dict[str, Any]:
    found, error = _verify(store, args.order_id, args.email, args.phone)
    if error:
        return error
    order, customer = found
    assessment = rules.assess_return(order, args.reason, store_now().date())
    return {
        "verified": True,
        "customer_first_name": customer.first_name,
        "items": _order_items(store, order),
        **assessment.model_dump(),
    }


def simulate_payment(args: SimulatePaymentInput, store: StoreData) -> dict[str, Any]:
    has_promo = False
    label = None
    if args.product_id is not None:
        product = store.products.get(args.product_id)
        if product is None:
            return {"error": "Produto não encontrado."}
        if not product.is_available:
            return {
                "error": "Este produto não pode ser vendido agora, então não há condições de "
                "pagamento para ele. Use get_product para ver as alternativas.",
                "availability": _availability(product),
                "can_be_sold": False,
            }
        promo = store.active_promotion(product.id)
        amount = rules.price_quote(product, promo).price
        has_promo = promo is not None
        label = product.name
    elif args.amount is not None and args.amount > 0:
        amount = args.amount
    else:
        return {"error": "Informe product_id ou amount."}

    sim = rules.simulate_payment(
        amount, has_promotion=has_promo, requested_installments=args.installments
    )
    top = sim.credit_options[-1]
    out: dict[str, Any] = {
        "product": label,
        "amount_text": rules.brl(sim.amount),
        "pix_text": rules.brl(sim.pix),
        "pix_discount_applies": sim.pix_discount_applies,
        "debit_text": rules.brl(sim.debit),
        "boleto_text": rules.brl(sim.boleto),
        "credit_max": f"{top.count}x de {rules.brl(top.value)} sem juros",
        "credit_options": [f"{o.count}x de {rules.brl(o.value)}" for o in sim.credit_options],
        "combined_payment_allowed": sim.combined_payment_allowed,
        "notes": sim.notes,
    }
    if args.installments is not None:
        out["requested_installments_allowed"] = sim.requested_allowed
    return out


def store_info(args: StoreInfoInput, store: StoreData) -> dict[str, Any]:
    status = rules.store_status(store_now())
    out: dict[str, Any] = {
        **STORE_CONTACT,
        "hours": {
            "segunda a sexta": "09:00 às 18:00",
            "sábado": "09:00 às 13:00",
            "domingo e feriados": "fechado",
        },
        "now": status.now,
        "open_now": status.is_open,
        "payment_methods": "PIX (5% de desconto à vista, não vale sobre promoção), débito, "
        "boleto e cartão de crédito em até 12x sem juros com parcela mínima por faixa.",
        "scope": "Vendemos somente instrumentos musicais, sem acessórios (cordas, palhetas, cabos, "
        "cases, pedais, amplificadores).",
    }
    if status.reason:
        out["closed_reason"] = status.reason
    if status.next_opening:
        out["next_opening"] = status.next_opening
    if args.city:
        categories = {
            store.products[i].category_id for i in args.product_ids or [] if i in store.products
        }
        quote = rules.shipping_quote(args.city, args.order_value or 0, categories)
        out["shipping"] = {
            "region": "região metropolitana de Campo Grande"
            if quote.region == "metro"
            else "outras cidades",
            "fee_text": rules.brl(quote.fee) if quote.fee is not None else "calculado pelo CEP",
            "deadline": quote.deadline,
            "notes": quote.notes,
        }
        if quote.region == "metro" and args.order_value is None:
            out["shipping"]["fee_text"] = "grátis acima de R$ 500,00, senão R$ 35,00"
    return out


def _next_business_moment(start: datetime, hours: float) -> datetime:
    """Add business hours (store opening hours) to a moment."""
    remaining = timedelta(hours=hours)
    t = start
    for _ in range(60):
        h = rules.opening_hours(t.date())
        if h:
            day_start = t.replace(hour=h[0].hour, minute=h[0].minute, second=0, microsecond=0)
            day_end = t.replace(hour=h[1].hour, minute=h[1].minute, second=0, microsecond=0)
            cursor = max(t, day_start)
            if cursor < day_end:
                available = day_end - cursor
                if remaining <= available:
                    return cursor + remaining
                remaining -= available
        t = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return t


def escalate_to_human(args: EscalateInput, store: StoreData) -> dict[str, Any]:
    """Record a case for the team. "24 horas úteis" (7.3) is read as 24 business hours."""
    now = store_now()
    due = _next_business_moment(now, 24)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNTIME_DIR / "tickets.jsonl"
    existing = path.read_text(encoding="utf-8").count("\n") if path.exists() else 0
    ticket_id = f"EM-{now:%y%m%d}-{existing + 1:03d}"
    record = {
        "ticket_id": ticket_id,
        "created_at": now.isoformat(),
        "due_at": due.isoformat(),
        **args.model_dump(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "ticket_id": ticket_id,
        "response_deadline": f"{due:%d/%m/%Y} às {due:%H:%M}",
        "message": "Chamado registrado. A equipe responde em até 24 horas úteis.",
    }


# --- Registry ----------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any, StoreData], dict[str, Any]]

    @property
    def input_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema


TOOLS: list[Tool] = [
    Tool(
        "search_products",
        "Search the catalog by category, price range and/or free text. Returns prices (already "
        "with active promotions) and availability. By default only products that can be sold "
        "now are returned. Use for 'what do you have', budget and comparison questions.",
        SearchProductsInput,
        search_products,
    ),
    Tool(
        "get_product",
        "Full details of one product: price, PIX price, active promotion, expired promotions, "
        "stock, specs and alternatives when it can't be sold. Use for price, availability or "
        "'tell me about X' questions.",
        GetProductInput,
        get_product,
    ),
    Tool(
        "get_order",
        "Status, items, total, tracking and delivery estimate of an order. Requires the order "
        "number AND the e-mail or phone used in the purchase. Never guess them: ask the customer.",
        OrderAuthInput,
        get_order,
    ),
    Tool(
        "check_return_eligibility",
        "Checks which return, exchange or warranty options are open for an order and until when, "
        "following the store policy. Requires order number AND e-mail or phone, plus the reason.",
        ReturnInput,
        check_return_eligibility,
    ),
    Tool(
        "simulate_payment",
        "Payment options for a product or an amount: PIX price, debit, boleto, and the credit "
        "card installments allowed by the minimum-installment rules. Use whenever the customer "
        "asks about installments, PIX or how to pay.",
        SimulatePaymentInput,
        simulate_payment,
    ),
    Tool(
        "store_info",
        "Store address, phone, e-mail, opening hours, whether it is open right now, payment "
        "methods, scope and, if a city is given, shipping fee and delivery time.",
        StoreInfoInput,
        store_info,
    ),
    Tool(
        "escalate_to_human",
        "Opens a ticket for the human team. Use for complaints, late orders, return requests "
        "that need action, anything the policy does not cover, or when the customer asks for a "
        "person. Returns the ticket id and response deadline.",
        EscalateInput,
        escalate_to_human,
    ),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def execute(name: str, arguments: dict[str, Any], store: StoreData | None = None) -> dict[str, Any]:
    """Run a tool by name with raw arguments. Errors come back as data, never raised."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        args = tool.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        return {"error": "Invalid arguments.", "details": json.loads(exc.json())}
    try:
        return tool.fn(args, store or get_store())
    except Exception as exc:  # a tool failure must not crash the conversation
        return {"error": f"Tool failed: {type(exc).__name__}: {exc}"}


__all__ = ["TOOLS", "TOOLS_BY_NAME", "Tool", "execute"]
