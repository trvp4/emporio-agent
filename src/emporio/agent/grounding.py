"""Hallucination firewall.

Before an answer reaches the customer, every factual number in it (money,
percentages, installment counts, stock quantities, dates) must appear in a trusted
source: a tool result from this conversation, the policy manual, the store's
current date and status, or what the customer said. Anything else is treated
as invented.

This is deliberately strict and simple: the model is told to copy values from
tools and never compute, so a number without a source is a bug, not a style issue.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_AMOUNT = r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)"
# "R$ 1.299,00" and "1.299 reais"
MONEY_RES = [re.compile(r"R\$\s*" + _AMOUNT), re.compile(_AMOUNT + r"\s*reais\b", re.IGNORECASE)]
PERCENT_RE = re.compile(r"(\d{1,3}(?:,\d+)?)\s*%")
# "10x" and "10 vezes"
INSTALLMENTS_RE = re.compile(r"\b(\d{1,2})\s*(?:x|vezes)\b", re.IGNORECASE)
STOCK_RE = re.compile(
    r"\b(\d+)\s+(?:unidades?|em estoque|dispon[ií]ve(?:l|is)|pe[cç]as?)", re.IGNORECASE
)
# dd/mm, dd/mm/yy or dd/mm/yyyy, not part of a longer number such as a law ("8.078/90").
DATE_RE = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
# "violão 3/4", "tamanho 1/2": instrument sizes, not dates.
SIZE_BEFORE_RE = re.compile(
    r"(tamanho|viol[aã]o|violino|violoncelo|cello|baixo|guitarra)\s*$", re.IGNORECASE
)

Date = tuple[int, int, int | None]  # day, month, year (None when not written)


def _num(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def _date(m: re.Match[str]) -> Date | None:
    day, month = int(m.group(1)), int(m.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    if SIZE_BEFORE_RE.search(m.string[max(0, m.start() - 12) : m.start()]):
        return None
    year = m.group(3)
    if year is None:
        return day, month, None
    return day, month, int(year) + 2000 if len(year) == 2 else int(year)


@dataclass
class Evidence:
    money: set[float] = field(default_factory=set)
    numbers: set[float] = field(default_factory=set)
    dates: set[Date] = field(default_factory=set)

    def add_text(self, text: str) -> None:
        for regex in MONEY_RES:
            for m in regex.finditer(text):
                self.money.add(round(_num(m.group(1)), 2))
        for regex in (PERCENT_RE, INSTALLMENTS_RE, STOCK_RE):
            for m in regex.finditer(text):
                self.numbers.add(_num(m.group(1)))
        for m in DATE_RE.finditer(text):
            if d := _date(m):
                self.dates.add(d)

    def has_date(self, date: Date) -> bool:
        """Same day and month; the year must match too when both sides write it."""
        day, month, year = date
        return any(
            d == day and m == month and (year is None or y is None or y == year)
            for d, m, y in self.dates
        )

    def add_json(self, value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, float):  # prices come as floats
            self.money.add(round(value, 2))
            self.numbers.add(value)
        elif isinstance(value, int):  # ids, stock, days left: counts, never money
            self.numbers.add(float(value))
        elif isinstance(value, str):
            self.add_text(value)
        elif isinstance(value, dict):
            for v in value.values():
                self.add_json(v)
        elif isinstance(value, list | tuple):
            for v in value:
                self.add_json(v)

    def has_money(self, value: float) -> bool:
        return any(abs(value - m) < 0.005 for m in self.money)


@dataclass
class GroundingReport:
    ok: bool
    checked: int
    unsupported: list[str]


def build_evidence(
    tool_results: Iterable[Any], policy_text: str, user_texts: Iterable[str]
) -> Evidence:
    ev = Evidence()
    for result in tool_results:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                ev.add_text(result)
                continue
        ev.add_json(result)
    ev.add_text(policy_text)
    for text in user_texts:
        ev.add_text(text)
    return ev


def check_answer(answer: str, evidence: Evidence) -> GroundingReport:
    unsupported: list[str] = []
    checked = 0

    for regex in MONEY_RES:
        for m in regex.finditer(answer):
            checked += 1
            if not evidence.has_money(round(_num(m.group(1)), 2)):
                unsupported.append(m.group(0))

    for regex in (PERCENT_RE, INSTALLMENTS_RE, STOCK_RE):
        for m in regex.finditer(answer):
            if regex is PERCENT_RE and _num(m.group(1)) == 100:
                continue  # "100% original", "100% Birch": not a discount
            checked += 1
            if _num(m.group(1)) not in evidence.numbers:
                unsupported.append(m.group(0))

    for m in DATE_RE.finditer(answer):
        if (d := _date(m)) is None:
            continue
        checked += 1
        if not evidence.has_date(d):
            unsupported.append(m.group(0))

    return GroundingReport(ok=not unsupported, checked=checked, unsupported=unsupported)
