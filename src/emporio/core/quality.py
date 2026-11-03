"""Data quality checks.

Runs a set of checks over the store data and the policy manual, and renders the
findings (with the decision taken for each one) to `docs/data_quality.md`.

    uv run python -m emporio.core.quality
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from emporio.config import DOCS_DIR, store_now
from emporio.core.data import StoreData, brands_in, digits, get_store

TRACKING_RE = re.compile(r"^BR[A-Z0-9]{9}BR$")


@dataclass(frozen=True)
class Issue:
    area: str
    finding: str
    decision: str


def _brl(value: float) -> str:
    s = f"{value:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


# Found by reading data/store_policies.pdf. These cannot be detected from the CSVs.
MANUAL_ISSUES = [
    Issue(
        "Manual",
        "The phone is (67) 3341-4444 on the cover and in the company table (1.2), "
        "but section 7 says the WhatsApp channel is (67) 3321-4500.",
        "Use (67) 3341-4444, the number in the official company data, and flag it to the store.",
    ),
    Issue(
        "Manual",
        "Table 1.2 spells the e-mail with an accent (contato@empóriodamusica.com.br); "
        "the cover spells it without.",
        "Use contato@emporiodamusica.com.br. Accented domains are unusual and the cover agrees.",
    ),
    Issue(
        "Manual",
        "The payment table says credit card installments have a R$ 100 minimum, "
        "while rule 3.1 sets R$ 50 (1x-3x), R$ 80 (4x-6x) and R$ 100 (7x-12x).",
        "Follow rule 3.1, the more detailed one. Read '3x without minimum (except below "
        "R$ 50)' as a R$ 50 minimum per installment.",
    ),
    Issue(
        "Manual",
        "The manual claims 'more than 300 instruments'; the catalog has far fewer products.",
        "The agent never quotes a catalog size.",
    ),
    Issue(
        "Manual",
        "The right of withdrawal (4.1) applies to online purchases, "
        "but orders do not say whether they were online or in-store.",
        "Treat every order in the system as online (all have delivery data).",
    ),
]


def run_checks(store: StoreData) -> list[Issue]:
    issues: list[Issue] = list(MANUAL_ISSUES)
    now = store_now().date()

    # Categories with no products.
    used = {p.category_id for p in store.products.values()}
    empty = [c.name for c in store.categories.values() if c.id not in used]
    if empty:
        issues.append(
            Issue(
                "Categories",
                f"{len(empty)} categories have no products: {', '.join(empty)}.",
                "Say there are no items in that category right now and offer to escalate. "
                "Never invent products.",
            )
        )

    # Products: active but out of stock, discontinued with stock.
    for p in store.products.values():
        if p.status == "active" and p.stock == 0:
            issues.append(
                Issue(
                    "Products",
                    f"#{p.id} {p.name} is active with stock 0.",
                    "Treat as temporarily unavailable and suggest similar products in stock.",
                )
            )
        if p.status == "discontinued" and p.stock > 0:
            issues.append(
                Issue(
                    "Products",
                    f"#{p.id} {p.name} is discontinued but has {p.stock} units in stock.",
                    "Never offer it. Suggest an equivalent model, as the manual says (7.3).",
                )
            )

    # Products that are "coming soon" but already appear in orders.
    in_orders: dict[int, list[int]] = defaultdict(list)
    for o in store.orders.values():
        for it in o.items:
            in_orders[it.product_id].append(o.id)
    for p in store.products.values():
        if p.status == "coming_soon" and p.id in in_orders:
            ids = ", ".join(f"#{i}" for i in in_orders[p.id])
            issues.append(
                Issue(
                    "Products x Orders",
                    f"#{p.id} {p.name} is 'coming_soon' but appears in orders {ids}.",
                    "The catalog does not sell it. Orders are shown exactly as recorded.",
                )
            )

    # Products whose description cites a different brand than their name.
    mismatched = []
    for p in store.products.values():
        named, described = brands_in(p.name), brands_in(p.description)
        if named and described and not named & described:
            other = ", ".join(b.title() for b in sorted(described))
            mismatched.append(f"#{p.id} {p.name} (described as {other})")
    if mismatched:
        issues.append(
            Issue(
                "Products",
                f"{len(mismatched)} products name one brand and describe another: "
                + "; ".join(mismatched)
                + ".",
                "The name is the record (catalog and orders use it). Brand searches match the "
                "name only, and the product lookup warns the agent not to repeat the "
                "description's brand.",
            )
        )

    # Referential integrity.
    for o in store.orders.values():
        if o.customer_id not in store.customers:
            issues.append(Issue("Orders", f"Order #{o.id} has an unknown customer.", "Ignore."))
        if not o.items:
            issues.append(Issue("Orders", f"Order #{o.id} has no items.", "Show totals only."))
        for it in o.items:
            if it.product_id not in store.products:
                issues.append(
                    Issue("Orders", f"Order #{o.id} references unknown product.", "Ignore item.")
                )

    # Promotions.
    active = [p for p in store.promotions if p.is_active]
    issues.append(
        Issue(
            "Promotions",
            f"Only {len(active)} of {len(store.promotions)} promotions are active.",
            "Only is_active = 1 counts. Expired promotions are explained, never applied (7.3).",
        )
    )
    multi = defaultdict(list)
    for promo in store.promotions:
        multi[promo.product_id].append(promo.id)
    repeated = [f"#{pid}" for pid, ids in multi.items() if len(ids) > 1]
    if repeated:
        issues.append(
            Issue(
                "Promotions",
                f"Products {', '.join(repeated)} have more than one promotion record.",
                "Never stack. If two were active at once, the biggest discount would win.",
            )
        )

    # Order totals vs. current catalog prices.
    pix_without_discount: list[str] = []
    for o in store.orders.values():
        if not o.items or any(it.product_id not in store.products for it in o.items):
            continue
        catalog = sum(store.products[it.product_id].price * it.quantity for it in o.items)
        if abs(catalog - o.total) > 0.01:
            issues.append(
                Issue(
                    "Orders",
                    f"Order #{o.id}: recorded total {_brl(o.total)} differs from current catalog "
                    f"prices {_brl(catalog)}.",
                    "Prices may have changed since the purchase. Show the recorded total and never "
                    "recompute it.",
                )
            )
        elif o.payment_method == "pix" and o.status != "cancelled":
            pix_without_discount.append(f"#{o.id}")
    if pix_without_discount:
        issues.append(
            Issue(
                "Orders",
                f"Orders {', '.join(pix_without_discount)} were paid with PIX, but their totals "
                "equal full catalog prices (no 5% discount).",
                "Show the recorded total. Never promise a refund; escalate if the customer asks.",
            )
        )

    # Orders: missing delivered date, overdue shipments, tracking format.
    issues.append(
        Issue(
            "Orders",
            "There is no actual delivery date, only estimated_delivery.",
            "For delivered orders, assume delivery happened on estimated_delivery. Return and "
            "warranty deadlines are counted from it.",
        )
    )
    for o in store.orders.values():
        if o.status == "shipped" and o.estimated_delivery and o.estimated_delivery < now:
            days = (now - o.estimated_delivery).days
            issues.append(
                Issue(
                    "Orders",
                    f"Order #{o.id} is 'shipped' and its estimated delivery "
                    f"({o.estimated_delivery:%d/%m/%Y}) passed {days} days ago.",
                    "Report it as late and open a ticket for the team.",
                )
            )
        if o.tracking_code and not TRACKING_RE.match(o.tracking_code):
            issues.append(
                Issue("Orders", f"Order #{o.id} has a malformed tracking code.", "Show as is.")
            )

    # Customers sharing e-mail or phone.
    by_email: dict[str, list[int]] = defaultdict(list)
    by_phone: dict[str, list[int]] = defaultdict(list)
    for c in store.customers.values():
        by_email[c.email.lower()].append(c.id)
        by_phone[digits(c.phone)].append(c.id)
    for email, ids in by_email.items():
        if len(ids) > 1:
            issues.append(
                Issue(
                    "Customers",
                    f"Customers {', '.join(f'#{i}' for i in ids)} share the e-mail {email}.",
                    "Identity is checked with order number + e-mail or phone, never e-mail alone.",
                )
            )
    for ids in by_phone.values():
        if len(ids) > 1:
            issues.append(
                Issue(
                    "Customers",
                    f"Customers {', '.join(f'#{i}' for i in ids)} share a phone number.",
                    "Same as above.",
                )
            )

    return issues


def render_markdown(issues: list[Issue], reference: date) -> str:
    lines = [
        "# Data quality report",
        "",
        "Generated by `python -m emporio.core.quality`. Each finding comes with the decision the "
        "agent follows. The policy manual issues were found by reading the PDF; everything else "
        "is computed from the CSVs.",
        "",
        f"Reference date (`STORE_NOW`): **{reference:%Y-%m-%d}**. The dataset is a snapshot whose "
        "latest order is from 2026-03-22, so deadlines are evaluated relative to this date.",
        "",
        "| # | Area | Finding | Decision |",
        "| --- | --- | --- | --- |",
    ]
    for i, issue in enumerate(issues, 1):
        lines.append(f"| {i} | {issue.area} | {issue.finding} | {issue.decision} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    issues = run_checks(get_store())
    out = DOCS_DIR / "data_quality.md"
    out.write_text(render_markdown(issues, store_now().date()), encoding="utf-8")
    print(f"{len(issues)} findings written to {out.relative_to(DOCS_DIR.parent)}")


if __name__ == "__main__":
    main()
