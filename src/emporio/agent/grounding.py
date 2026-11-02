"""Hallucination firewall.

Before an answer reaches the customer, every factual number in it (money,
percentages, installment counts, stock quantities) must appear in a trusted
source: a tool result from this conversation, the policy manual, or what the
customer said. Anything else is treated as invented.

This is deliberately strict and simple: the model is told to copy values from
tools and never compute, so a number without a source is a bug, not a style issue.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

MONEY_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)")
PERCENT_RE = re.compile(r"(\d{1,3}(?:,\d+)?)\s*%")
INSTALLMENTS_RE = re.compile(r"\b(\d{1,2})\s*x\b", re.IGNORECASE)
STOCK_RE = re.compile(r"\b(\d+)\s+(?:unidades?|em estoque)", re.IGNORECASE)


def _num(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


@dataclass
class Evidence:
    money: set[float] = field(default_factory=set)
    numbers: set[float] = field(default_factory=set)

    def add_text(self, text: str) -> None:
        for m in MONEY_RE.finditer(text):
            self.money.add(round(_num(m.group(1)), 2))
        for regex in (PERCENT_RE, INSTALLMENTS_RE, STOCK_RE):
            for m in regex.finditer(text):
                self.numbers.add(_num(m.group(1)))

    def add_json(self, value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            self.money.add(round(float(value), 2))
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

    for m in MONEY_RE.finditer(answer):
        checked += 1
        if not evidence.has_money(round(_num(m.group(1)), 2)):
            unsupported.append(m.group(0))

    for regex in (PERCENT_RE, INSTALLMENTS_RE, STOCK_RE):
        for m in regex.finditer(answer):
            checked += 1
            if _num(m.group(1)) not in evidence.numbers:
                unsupported.append(m.group(0))

    return GroundingReport(ok=not unsupported, checked=checked, unsupported=unsupported)
