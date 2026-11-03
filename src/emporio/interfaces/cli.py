"""Terminal chat with Lia.

uv run emporio-chat            # chat
uv run emporio-chat --trace    # also show every tool call and the firewall check
"""

from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from emporio.agent.llm import make_client
from emporio.agent.loop import Agent, Turn

console = Console()

HELP = "Comandos: /trace liga ou desliga o rastro, /reset começa outra conversa, /sair encerra."


def show_trace(turn: Turn) -> None:
    for t in turn.tools:
        args = json.dumps(t.arguments, ensure_ascii=False)
        result = json.dumps(t.result, ensure_ascii=False)
        if len(result) > 600:
            result = result[:600] + " ..."
        console.print(
            Panel(
                Text(f"{args}\n→ {result}", style="dim"),
                title=f"[bold cyan]{t.name}[/] ({t.ms:.0f} ms)",
                title_align="left",
                border_style="cyan",
            )
        )
    for draft in turn.rejected_drafts:
        console.print(Panel(Text(draft, style="dim"), title="[bold red]rascunho bloqueado[/]",
                            title_align="left", border_style="red"))  # fmt: skip
    g = turn.grounding
    firewall = (
        f"firewall: {g.checked} valores conferidos"
        + (f", sem origem: {', '.join(g.unsupported)}" if g and g.unsupported else "")
        if g
        else "firewall: -"
    )
    console.print(
        f"[dim]{firewall} · {turn.llm_calls} chamadas ao modelo · "
        f"{turn.input_tokens} tokens in + {turn.cache_read_tokens} lidos do cache / "
        f"{turn.output_tokens} out · {turn.seconds:.1f}s[/]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Converse com a Lia, da Empório da Música.")
    parser.add_argument("--trace", action="store_true", help="mostra ferramentas e firewall")
    parser.add_argument("--provider", help="anthropic ou openai (padrão: LLM_PROVIDER)")
    parser.add_argument("--model", help="modelo (padrão: LLM_MODEL)")
    args = parser.parse_args()

    try:
        llm = make_client(args.provider, args.model)
    except Exception as exc:
        console.print(f"[red]Não foi possível iniciar o modelo:[/] {exc}")
        raise SystemExit(1) from exc

    agent = Agent(llm)
    trace = args.trace
    console.print(
        Panel(
            f"Empório da Música · atendimento virtual\n[dim]modelo: {llm.model} · {HELP}[/]",
            border_style="magenta",
        )
    )

    while True:
        try:
            user = console.input("[bold green]você ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not user:
            continue
        if user in {"/sair", "/exit", "/quit"}:
            break
        if user == "/reset":
            agent.reset()
            console.print("[dim]Nova conversa.[/]")
            continue
        if user == "/trace":
            trace = not trace
            console.print(f"[dim]Rastro {'ligado' if trace else 'desligado'}.[/]")
            continue
        if user == "/help":
            console.print(f"[dim]{HELP}[/]")
            continue

        with console.status("[dim]Lia está digitando...[/]"):
            try:
                turn = agent.reply(user)
            except Exception as exc:  # network or provider errors
                console.print(f"[red]Erro ao falar com o modelo:[/] {exc}")
                continue
        if trace:
            show_trace(turn)
        console.print("[bold magenta]Lia ›[/]")
        console.print(Markdown(turn.text))  # bold prices and lists, not raw "**"
        console.print()


if __name__ == "__main__":
    main()
