"""Store policies as code.

Every rule in the manual that involves a number (prices, installments, deadlines,
shipping fees, opening hours) lives here as a small pure function. The LLM never
does this arithmetic: it calls a tool, and the tool calls these functions.

Section numbers refer to docs/policies.md.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel

from emporio.core.data import normalize
from emporio.core.models import Order, Product, Promotion

# --- Money -----------------------------------------------------------------

PIX_DISCOUNT = 0.05  # 3: 5% off the list price, cash
COMBINED_PAYMENT_MIN = 2000.00  # 3.1: PIX + card allowed above this amount

# 3.1: (min installments, max installments, minimum installment value)
INSTALLMENT_TIERS: list[tuple[int, int, float]] = [
    (1, 3, 50.00),
    (4, 6, 80.00),
    (7, 12, 100.00),
]
MAX_INSTALLMENTS = 12


def money(value: float) -> float:
    """Round to cents, half up (avoids 2.675 -> 2.67 float surprises)."""
    return float(f"{value + 1e-9:.2f}")


def brl(value: float) -> str:
    """Format as Brazilian currency: R$ 1.299,00."""
    s = f"{money(value):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


class PriceQuote(BaseModel):
    list_price: float
    promotion_name: str | None = None
    promotion_percent: float | None = None
    price: float  # what the customer pays by card, debit or boleto
    pix_price: float  # what the customer pays with PIX
    pix_discount_applies: bool


def price_quote(product: Product, promotion: Promotion | None) -> PriceQuote:
    """Final prices for a product (3, 6.2).

    Promotions never stack: with an active promotion the PIX discount does not
    apply on top of it.
    """
    if promotion is not None:
        promo_price = money(product.price * (1 - promotion.discount_percent / 100))
        return PriceQuote(
            list_price=product.price,
            promotion_name=promotion.description,
            promotion_percent=promotion.discount_percent,
            price=promo_price,
            pix_price=promo_price,
            pix_discount_applies=False,
        )
    return PriceQuote(
        list_price=product.price,
        price=product.price,
        pix_price=money(product.price * (1 - PIX_DISCOUNT)),
        pix_discount_applies=True,
    )


def min_installment_value(n: int) -> float:
    for low, high, minimum in INSTALLMENT_TIERS:
        if low <= n <= high:
            return minimum
    raise ValueError(f"{n} installments is outside 1..{MAX_INSTALLMENTS}")


def installment_allowed(amount: float, n: int) -> bool:
    """1x is always possible; otherwise each installment must reach its tier minimum."""
    if n == 1:
        return amount > 0
    if not 1 <= n <= MAX_INSTALLMENTS:
        return False
    return money(amount / n) >= min_installment_value(n)


def max_installments(amount: float) -> int:
    return max(n for n in range(1, MAX_INSTALLMENTS + 1) if installment_allowed(amount, n))


class Installment(BaseModel):
    count: int
    value: float


class PaymentSimulation(BaseModel):
    amount: float
    pix: float
    pix_discount_applies: bool
    debit: float
    boleto: float
    credit_max_installments: int
    credit_options: list[Installment]
    requested_installments: int | None = None
    requested_allowed: bool | None = None
    combined_payment_allowed: bool
    notes: list[str]


def simulate_payment(
    amount: float,
    *,
    has_promotion: bool = False,
    requested_installments: int | None = None,
) -> PaymentSimulation:
    """Every way to pay an amount (3, 3.1, 6.2). `amount` is the card/boleto price."""
    pix = amount if has_promotion else money(amount * (1 - PIX_DISCOUNT))
    top = max_installments(amount)
    options = [Installment(count=n, value=money(amount / n)) for n in range(1, top + 1)]
    notes = ["Cartão de crédito sem juros."]
    if has_promotion:
        notes.append("Preço promocional: o desconto do PIX não se aplica (promoções não acumulam).")
    if amount > COMBINED_PAYMENT_MIN:
        notes.append(
            "Acima de R$ 2.000,00 é possível combinar formas de pagamento (ex.: PIX + cartão)."
        )
    notes.append("Boleto: compensação em até 3 dias úteis.")

    allowed = None
    if requested_installments is not None:
        allowed = installment_allowed(amount, requested_installments)
        if not allowed:
            notes.append(
                f"{requested_installments}x não é possível: a parcela mínima para essa faixa é "
                f"{brl(min_installment_value(min(max(requested_installments, 1), 12)))}. "
                f"O máximo para este valor é {top}x de {brl(amount / top)}."
            )

    return PaymentSimulation(
        amount=money(amount),
        pix=pix,
        pix_discount_applies=not has_promotion,
        debit=money(amount),
        boleto=money(amount),
        credit_max_installments=top,
        credit_options=options,
        requested_installments=requested_installments,
        requested_allowed=allowed,
        combined_payment_allowed=amount > COMBINED_PAYMENT_MIN,
        notes=notes,
    )


# --- Shipping (5) ------------------------------------------------------------

METRO_CITIES = {"campo grande"}  # 5.1: metropolitan region of Campo Grande
METRO_FREE_SHIPPING_ABOVE = 500.00
METRO_FLAT_FEE = 35.00
# 5.2: large instruments may need a special quote (acoustic drums, digital pianos, double basses)
LARGE_ITEM_CATEGORIES = {3, 4, 8}


class ShippingQuote(BaseModel):
    region: Literal["metro", "other"]
    fee: float | None  # None = calculated by CEP at checkout
    deadline: str
    notes: list[str]


def shipping_quote(
    city: str, subtotal: float, category_ids: set[int] | None = None
) -> ShippingQuote:
    notes: list[str] = []
    if category_ids and category_ids & LARGE_ITEM_CATEGORIES:
        notes.append(
            "Instrumentos de grande porte podem exigir frete especial com cotação individual."
        )

    if normalize(city) in METRO_CITIES:
        free = subtotal > METRO_FREE_SHIPPING_ABOVE
        notes.append("Entrega por motoboy próprio. A loja liga antes de entregar.")
        return ShippingQuote(
            region="metro",
            fee=0.0 if free else METRO_FLAT_FEE,
            deadline="1 a 3 dias úteis",
            notes=notes,
        )
    notes.append(
        "Frete calculado pelo CEP, peso e dimensões no fechamento do pedido. Todos os envios têm "
        "seguro e rastreamento."
    )
    return ShippingQuote(
        region="other",
        fee=None,
        deadline="PAC 5 a 12 dias úteis, SEDEX 2 a 5 dias úteis, Jadlog 3 a 8 dias úteis",
        notes=notes,
    )


# --- Opening hours (2) -------------------------------------------------------

WEEKDAY_HOURS = (time(9, 0), time(18, 0))
SATURDAY_HOURS = (time(9, 0), time(13, 0))


def _easter(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays(year: int) -> dict[date, str]:
    """National holidays plus the Mato Grosso do Sul state holiday."""
    fixed = {
        (1, 1): "Confraternização Universal",
        (4, 21): "Tiradentes",
        (5, 1): "Dia do Trabalho",
        (9, 7): "Independência do Brasil",
        (10, 11): "Criação do Estado de MS",
        (10, 12): "Nossa Senhora Aparecida",
        (11, 2): "Finados",
        (11, 15): "Proclamação da República",
        (11, 20): "Dia da Consciência Negra",
        (12, 25): "Natal",
    }
    result = {date(year, m, d): name for (m, d), name in fixed.items()}
    result[_easter(year) - timedelta(days=2)] = "Sexta-feira Santa"
    return result


def opening_hours(day: date) -> tuple[time, time] | None:
    if day in holidays(day.year) or day.weekday() == 6:
        return None
    return SATURDAY_HOURS if day.weekday() == 5 else WEEKDAY_HOURS


class StoreStatus(BaseModel):
    is_open: bool
    now: str
    today_hours: str
    reason: str | None = None
    next_opening: str | None = None


_WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def store_status(now: datetime) -> StoreStatus:
    today = now.date()
    hours = opening_hours(today)
    holiday = holidays(today.year).get(today)
    is_open = hours is not None and hours[0] <= now.time() < hours[1]

    next_opening = None
    if not is_open:
        day = today
        for _ in range(14):
            h = opening_hours(day)
            if h and (day > today or now.time() < h[0]):
                label = "hoje" if day == today else f"{_WEEKDAYS_PT[day.weekday()]} ({day:%d/%m})"
                next_opening = f"{label} às {h[0]:%H:%M}"
                break
            day += timedelta(days=1)

    return StoreStatus(
        is_open=is_open,
        now=f"{_WEEKDAYS_PT[today.weekday()]}, {now:%d/%m/%Y %H:%M}",
        today_hours=f"{hours[0]:%H:%M} às {hours[1]:%H:%M}" if hours else "fechada",
        reason=f"feriado ({holiday})" if holiday else ("domingo" if today.weekday() == 6 else None),
        next_opening=next_opening,
    )


# --- Returns, exchanges and warranty (4, 8) ----------------------------------

WITHDRAWAL_DAYS = 7  # 4.1, counted from delivery
DEFECT_EXCHANGE_DAYS = 30  # 4.2, counted from purchase
PREFERENCE_EXCHANGE_DAYS = 7  # 4.3, counted from delivery (assumption: same start as 4.1)
LEGAL_WARRANTY_DAYS = 90  # 8.1, counted from delivery

ReturnReason = Literal["arrependimento", "defeito", "preferencia"]


class ReturnOption(BaseModel):
    name: str
    eligible: bool
    deadline: str | None = None
    days_left: int | None = None
    explanation: str


class ReturnAssessment(BaseModel):
    order_id: int
    order_status: str
    purchase_date: str
    delivery_date: str | None
    delivery_date_is_estimate: bool
    reason: str
    options: list[ReturnOption]
    conditions: list[str]
    not_eligible_items: list[str]
    next_step: str


def _option(name: str, start: date, days: int, today: date, explanation: str) -> ReturnOption:
    deadline = start + timedelta(days=days)
    left = (deadline - today).days
    return ReturnOption(
        name=name,
        eligible=left >= 0,
        deadline=f"{deadline:%d/%m/%Y}",
        days_left=max(left, 0),
        explanation=explanation,
    )


def assess_return(order: Order, reason: ReturnReason, today: date) -> ReturnAssessment:
    """Which return/exchange/warranty paths are open for an order (4, 8).

    The data has no actual delivery date, so for delivered orders the estimated
    delivery date is used (documented assumption).
    """
    delivered = order.estimated_delivery if order.status == "delivered" else None
    base = dict(
        order_id=order.id,
        order_status=order.status,
        purchase_date=f"{order.order_date:%d/%m/%Y}",
        delivery_date=f"{delivered:%d/%m/%Y}" if delivered else None,
        delivery_date_is_estimate=delivered is not None,
        reason=reason,
        not_eligible_items=[
            "Instrumentos com personalização ou ajuste sob encomenda (setup, regulagem especial).",
            "Produtos de liquidação com aviso de 'venda final'.",
            "Boquilhas de instrumentos de sopro.",
        ],
    )

    if order.status == "cancelled":
        return ReturnAssessment(
            **base,
            options=[],
            conditions=[],
            next_step="O pedido foi cancelado, não há produto para devolver.",
        )
    if delivered is None:
        return ReturnAssessment(
            **base,
            options=[],
            conditions=[],
            next_step=(
                "O pedido ainda não foi entregue. Os prazos de devolução contam a partir do "
                "recebimento. Para cancelar antes da entrega, encaminhar para a equipe."
            ),
        )

    options: list[ReturnOption] = []
    conditions: list[str] = []

    if reason == "arrependimento":
        options.append(
            _option(
                "Direito de arrependimento (4.1)",
                delivered,
                WITHDRAWAL_DAYS,
                today,
                "Devolução sem justificativa em até 7 dias corridos após o recebimento. Reembolso "
                "na mesma forma de pagamento em até 10 dias úteis, frete de devolução por conta "
                "da loja.",
            )
        )
        conditions = [
            "Embalagem original, sem sinais de uso, com todos os acessórios e manuais.",
        ]
    elif reason == "preferencia":
        options.append(
            _option(
                "Troca por preferência (4.3)",
                delivered,
                PREFERENCE_EXCHANGE_DAYS,
                today,
                "Troca de cor, modelo ou tamanho em até 7 dias, sujeita a disponibilidade. "
                "Diferenças de valor são cobradas ou reembolsadas.",
            )
        )
        conditions = ["Produto em perfeito estado e na embalagem original."]
    else:  # defeito
        options.append(
            _option(
                "Troca por defeito na loja (4.2)",
                order.order_date,
                DEFECT_EXCHANGE_DAYS,
                today,
                "Defeito de fabricação: troca direta em até 30 dias corridos após a compra.",
            )
        )
        options.append(
            _option(
                "Garantia legal (8.1)",
                delivered,
                LEGAL_WARRANTY_DAYS,
                today,
                "90 dias contra defeitos de fabricação a partir do recebimento. Depois da troca "
                "direta, o caso segue pela garantia, e a loja pode intermediar com o fabricante.",
            )
        )
        conditions = [
            "Não cobre mau uso, quedas, umidade excessiva, modificações não autorizadas, desgaste "
            "natural (trastes, cordas, feltros, palhetas) ou danos só estéticos.",
            "A garantia do fabricante (6 meses a 2 anos) continua valendo conforme o certificado.",
        ]

    open_options = [o for o in options if o.eligible]
    if open_options:
        next_step = f"Elegível: {open_options[0].name}. Encaminhar para a equipe dar sequência."
    elif reason == "defeito":
        next_step = (
            "Prazos da loja encerrados. Orientar a acionar a garantia do fabricante; a loja pode "
            "intermediar se o cliente pedir."
        )
    else:
        next_step = "Prazo encerrado. Se houver defeito, avaliar troca por defeito ou garantia."

    return ReturnAssessment(**base, options=options, conditions=conditions, next_step=next_step)
