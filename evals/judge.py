"""LLM judge: a second opinion on each scenario, about meaning rather than wording.

The rule checks in checks.py are cheap and repeatable, but they test words: a
correct answer phrased in an unexpected way fails. The judge reads the whole
conversation, including what each tool returned, and answers two questions:
did the agent do what the scenario expects, with nothing invented? And how was
the tone? It never changes the rule result; the report shows both, and the
cases where they disagree are the interesting ones.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"
MAX_RESULT_CHARS = 1500

SYSTEM = """You are reviewing a customer service agent for Empório da Música, a musical
instrument store in Campo Grande/MS, Brazil. The agent is called Lia and talks to
customers in Brazilian Portuguese. It answers with tools: every price, date,
deadline and order detail must come from a tool result, the store manual or the
customer's own words.

You get one test scenario: what it checks, then each customer message with the
tools Lia called (arguments and results) and Lia's answer. Below are what Lia
also knows: the tools she can call, her full instructions (which include the
store manual) and the store's clock and status. Facts from any of them are
supported, and actions her instructions describe (for example offering a ticket
so the team follows up or warns the customer) are legitimate.

Judge two things:
1. verdict: "pass" if Lia did what the scenario expects and every fact she stated
   is supported; "fail" only if the customer would be misled or wronged: an
   invented or contradicted fact, a promise the store can't keep, a broken store
   rule, data revealed that should not be, or what the scenario asks for missed.
   Hints inside tool results (such as next_step) guide Lia; don't grade her
   wording against them. Do not fail an answer for wording, length or style.
2. tone, 1 to 5: warm, informal but professional, clear and short, like a friend
   who knows music (5) versus robotic, rude, confusing or rambling (1).

Give a one or two sentence reason, in English, naming the specific problem when
the verdict is fail."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "tone": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "tone", "reason"],
    "additionalProperties": False,
}


def reference() -> str:
    """What Lia knows besides the conversation: tools, her instructions and the clock."""
    from emporio.agent.prompt import dynamic_context, static_prompt
    from emporio.config import store_now
    from emporio.core.tools import TOOLS

    tools = "\n".join(f"- {t.name}: {t.description}" for t in TOOLS)
    return (
        f"# Lia's tools\n\n{tools}\n\n"
        f"# Lia's instructions (her system prompt, with the store manual)\n\n{static_prompt()}"
        f"\n\n{dynamic_context(store_now())}"
    )


def render_case(scenario: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    lines = [
        f"Scenario: {scenario['title']}",
        f"What it checks: {scenario.get('why', '').strip()}",
        "",
    ]
    for i, t in enumerate(turns, 1):
        lines.append(f"--- Message {i}")
        lines.append(f"Customer: {t['user']}")
        for tool in t["tools"]:
            result = json.dumps(tool.get("result", {}), ensure_ascii=False)
            if len(result) > MAX_RESULT_CHARS:
                result = result[:MAX_RESULT_CHARS] + " ..."
            args = json.dumps(tool["arguments"], ensure_ascii=False)
            lines.append(f"Tool {tool['name']}({args}) -> {result}")
        lines.append(f"Lia: {t['answer']}")
    return "\n".join(lines)


@dataclass
class Verdict:
    verdict: str
    tone: int
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0


# (system, user text) -> (parsed JSON, input tokens, output tokens)
Completion = Callable[[str, str], tuple[dict[str, Any], int, int]]


class Judge:
    def __init__(self, model: str = DEFAULT_JUDGE_MODEL, complete: Completion | None = None):
        self.model = model
        self._complete = complete or self._anthropic

    def _anthropic(self, system: str, text: str) -> tuple[dict[str, Any], int, int]:
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=self.model,
            max_tokens=4000,
            # The manual makes the system prompt large and identical across scenarios.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": text}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
        )
        raw = next(b.text for b in response.content if b.type == "text")
        usage = response.usage
        return json.loads(raw), usage.input_tokens, usage.output_tokens

    def evaluate(self, scenario: dict[str, Any], turns: list[dict[str, Any]]) -> Verdict:
        # Called inside run_scenario, while the scenario's store clock is set.
        system = f"{SYSTEM}\n\n{reference()}"
        data, tokens_in, tokens_out = self._complete(system, render_case(scenario, turns))
        return Verdict(data["verdict"], int(data["tone"]), data["reason"], tokens_in, tokens_out)
