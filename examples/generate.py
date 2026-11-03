"""Generate the example conversations in this folder with the real agent.

uv run python -m examples.generate            # model from .env
uv run python -m examples.generate --model claude-sonnet-5-5

Each conversation is a fresh session with the store clock pinned to the dataset
snapshot. The transcripts are written as they come out of the model, unedited,
together with the tools each answer used and the firewall check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

# Tickets opened while generating must not land in the real runtime/ folder.
os.environ.setdefault("EMPORIO_RUNTIME_DIR", tempfile.mkdtemp(prefix="emporio-examples-"))
os.environ["STORE_NOW"] = "2026-03-25T10:30:00"

from emporio.agent.llm import make_client  # noqa: E402
from emporio.agent.loop import Agent, Turn  # noqa: E402
from emporio.core.data import get_store  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parent

CONVERSATIONS = [
    {
        "slug": "01-catalog-budget",
        "title": "Catalog search within a budget",
        "shows": "A catalog query (suggested in the brief). Only products that can be sold "
        "are listed, inside the budget, and the PIX price comes from the payment rules.",
        "messages": [
            "Oi! Quais opções de violões vocês têm até R$ 1000?",
            "Gostei do Tagima. Quanto fica no PIX?",
        ],
    },
    {
        "slug": "02-price-and-installments",
        "title": "Price and installments, with a policy rule applied",
        "shows": "A price query (suggested in the brief), then an installment request the "
        "minimum-installment rule (manual 3.1) refuses, with the best allowed option instead.",
        "messages": [
            "Quanto custa o Takamine GD20?",
            "E o Shelby SGD-195E, dá pra fazer em 10x?",
        ],
    },
    {
        "slug": "03-store-info-and-out-of-scope",
        "title": "Store information and an out-of-scope question",
        "shows": "General store information (suggested in the brief), shipping for another "
        "city, and a question outside the store's scope.",
        "messages": [
            "Qual o endereço da loja?",
            "Vocês entregam em Dourados?",
            "Aproveitando, você sabe me dizer quem ganhou a Copa de 2002?",
        ],
    },
    {
        "slug": "04-return-request",
        "title": "Return request: identity check and the withdrawal deadline",
        "shows": "The brief's return question. The agent asks for the order and contact before "
        "looking at any personal data, finds the 7-day window closed, and points to the "
        "warranty that is still open.",
        "messages": [
            "Me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7, e-mail leticia.rocha@jmail.com",
            "Na verdade o ukulele veio com a tarraxa com defeito. Muda alguma coisa?",
        ],
    },
    {
        "slug": "05-late-order-escalation",
        "title": "Late order: tracking plus a ticket for the team",
        "shows": "A real-time order lookup. The order is 25 days past its delivery estimate, "
        "so the agent apologizes, shares the tracking code and opens a ticket with a deadline "
        "counted in business hours.",
        "messages": [
            "Oi, meu pedido 8 ainda não chegou e já passou muito do prazo.",
            "anacarol.ferreira@coldmail.com",
        ],
    },
]


def _behind(turn: Turn) -> str:
    lines = []
    for t in turn.tools:
        args = json.dumps(t.arguments, ensure_ascii=False)
        lines.append(f"- `{t.name}` `{args}`")
    if not turn.tools:
        lines.append("- No tool call: answered from the prompt (persona, manual, store context).")
    g = turn.grounding
    if turn.rejected_drafts:
        lines.append(f"- Firewall blocked {len(turn.rejected_drafts)} draft(s) before this answer.")
    if g is not None and g.checked == 0:
        lines.append("- Firewall: no prices, percentages, installments or stock to check.")
    elif g is not None:
        status = "all grounded" if g.ok else f"unsupported: {', '.join(g.unsupported)}"
        values = "1 value" if g.checked == 1 else f"{g.checked} values"
        lines.append(f"- Firewall: {values} checked, {status}.")
    calls = "1 model call" if turn.llm_calls == 1 else f"{turn.llm_calls} model calls"
    lines.append(f"- {calls}, {turn.seconds:.1f} s.")
    tools = ", ".join(dict.fromkeys(t.name for t in turn.tools)) or "no tools"
    return (
        f"<details><summary>Behind this answer: {tools}</summary>\n\n"
        + "\n".join(lines)
        + "\n\n</details>"
    )


def render(conv: dict, turns: list[tuple[str, Turn]], model: str) -> str:
    out = [
        f"# {conv['title']}",
        "",
        f"**What it shows:** {conv['shows']}",
        "",
        f"_Real transcript, not edited. Model `{model}`, generated on {date.today():%Y-%m-%d} "
        "with `uv run python -m examples.generate`. Store clock pinned to Wednesday "
        "2026-03-25 10:30, right after the dataset's last order._",
        "",
        "---",
        "",
    ]
    for user, turn in turns:
        out += [f"**Cliente:** {user}", "", f"**Lia:** {turn.text}", "", _behind(turn), ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the example conversations.")
    parser.add_argument("--provider", help="anthropic or openai (default: LLM_PROVIDER)")
    parser.add_argument("--model", help="model (default: LLM_MODEL)")
    parser.add_argument("--only", help="slugs, comma-separated")
    args = parser.parse_args(argv)

    try:
        llm = make_client(args.provider, args.model)
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    store = get_store()
    wanted = set(args.only.split(",")) if args.only else None
    for conv in CONVERSATIONS:
        if wanted and conv["slug"] not in wanted:
            continue
        agent = Agent(llm, store)
        turns = [(m, agent.reply(m)) for m in conv["messages"]]
        path = EXAMPLES_DIR / f"{conv['slug']}.md"
        path.write_text(render(conv, turns, llm.model), encoding="utf-8")
        print(f"{path.name}: {len(turns)} turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
