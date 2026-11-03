"""Deterministic checks for one turn of a scenario.

No LLM judge: every check is a rule over the answer text and the tools called,
so a run is cheap, repeatable and comparable across models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from emporio.agent.loop import Turn
from emporio.core.data import normalize

KNOWN_CHECKS = {
    "tools",
    "no_tools",
    "tool_args",
    "contains",
    "contains_any",
    "not_contains",
    "allow_fallback",
}
SPLIT_MONEY_RE = re.compile(r"^R\$ ?[\d.]+$")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _norm(text: str) -> str:
    return normalize(text).replace("r$ ", "r$")


def _arg_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return _norm(actual) == _norm(expected)
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(actual - expected) < 0.005
    return actual == expected


def _groups(value: list[Any]) -> list[list[str]]:
    """`contains_any` takes one list of options or a list of such lists."""
    if not value:
        return []
    if all(isinstance(v, list) for v in value):
        return value
    return [value]


def check_turn(turn: Turn, expect: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    answer = _norm(turn.text)
    called = [t.name for t in turn.tools]

    if not expect.get("allow_fallback"):
        results.append(
            CheckResult(
                "no_fallback",
                not turn.fallback,
                "resposta segura no lugar da resposta" if turn.fallback else "",
            )
        )

    for name in expect.get("tools", []):
        results.append(
            CheckResult(f"tool:{name}", name in called, f"chamadas: {called or 'nenhuma'}")
        )

    for name in expect.get("no_tools", []):
        results.append(CheckResult(f"no_tool:{name}", name not in called, f"chamadas: {called}"))

    for name, args in expect.get("tool_args", {}).items():
        calls = [t.arguments for t in turn.tools if t.name == name]
        ok = any(all(_arg_matches(c.get(k), v) for k, v in args.items()) for c in calls)
        results.append(CheckResult(f"args:{name}", ok, f"esperado {args}, recebido {calls}"))

    for text in expect.get("contains", []):
        results.append(
            CheckResult(f"contains:{text}", _norm(str(text)) in answer, "ausente na resposta")
        )

    for group in _groups(expect.get("contains_any", [])):
        ok = any(_norm(str(t)) in answer for t in group)
        results.append(
            CheckResult(f"contains_any:{' | '.join(map(str, group))}", ok, "nenhuma opção")
        )

    for text in expect.get("not_contains", []):
        results.append(
            CheckResult(f"not_contains:{text}", _norm(str(text)) not in answer, "presente")
        )

    return results


def validate_scenarios(scenarios: list[dict[str, Any]], tool_names: set[str]) -> list[str]:
    """Problems in the scenario file (empty when it is valid)."""
    problems: list[str] = []
    seen: set[str] = set()
    for s in scenarios:
        sid = s.get("id", "?")
        if sid in seen:
            problems.append(f"{sid}: id repetido")
        seen.add(sid)
        for key in ("id", "category", "title", "turns"):
            if key not in s:
                problems.append(f"{sid}: falta '{key}'")
        for i, turn in enumerate(s.get("turns", []), 1):
            if not turn.get("user"):
                problems.append(f"{sid} turno {i}: falta 'user'")
            expect = turn.get("expect", {})
            for key in set(expect) - KNOWN_CHECKS:
                problems.append(f"{sid} turno {i}: verificação desconhecida '{key}'")
            names = [*expect.get("tools", []), *expect.get("no_tools", [])]
            names += list(expect.get("tool_args", {}))
            for name in names:
                if name not in tool_names:
                    problems.append(f"{sid} turno {i}: ferramenta desconhecida '{name}'")
            texts = [*expect.get("contains", []), *expect.get("not_contains", [])]
            texts += [t for g in _groups(expect.get("contains_any", [])) for t in g]
            for text in texts:
                # In a YAML flow list, an unquoted "R$ 114,14" splits into "R$ 114" and "14".
                if SPLIT_MONEY_RE.match(str(text)):
                    problems.append(f"{sid} turno {i}: '{text}' sem centavos; use aspas")
    return problems
