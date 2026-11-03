"""Run the evaluation suite against a real model.

uv run python -m evals.run                                  # model from .env
uv run python -m evals.run --model claude-sonnet-5-5        # another model
uv run python -m evals.run --only pagamento,pedido-atrasado # categories or ids

Each scenario is a fresh conversation. Results go to evals/results/ as JSON
(everything, including answers and tool calls) and Markdown (the report).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Tickets opened during the run must not land in the real runtime/ folder.
os.environ.setdefault("EMPORIO_RUNTIME_DIR", tempfile.mkdtemp(prefix="emporio-evals-"))

import yaml  # noqa: E402

from emporio.agent.llm import LLMClient, make_client  # noqa: E402
from emporio.agent.loop import Agent  # noqa: E402
from emporio.core.data import StoreData, get_store  # noqa: E402
from emporio.core.tools import TOOLS_BY_NAME  # noqa: E402
from evals.checks import check_turn, validate_scenarios  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = EVALS_DIR / "scenarios.yaml"
RESULTS_DIR = EVALS_DIR / "results"
DEFAULT_STORE_NOW = "2026-03-25T10:30:00"


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    scenarios = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = validate_scenarios(scenarios, set(TOOLS_BY_NAME))
    if problems:
        raise ValueError("Cenários inválidos:\n" + "\n".join(problems))
    return scenarios


def select(scenarios: list[dict[str, Any]], only: str | None) -> list[dict[str, Any]]:
    if not only:
        return scenarios
    wanted = {w.strip() for w in only.split(",") if w.strip()}
    return [s for s in scenarios if s["id"] in wanted or s["category"] in wanted]


def run_scenario(scenario: dict[str, Any], llm: LLMClient, store: StoreData) -> dict[str, Any]:
    previous_now = os.environ.get("STORE_NOW")
    os.environ["STORE_NOW"] = scenario.get("store_now", DEFAULT_STORE_NOW)
    agent = Agent(llm, store)
    turns: list[dict[str, Any]] = []
    error = None
    try:
        for spec in scenario["turns"]:
            turn = agent.reply(spec["user"])
            checks = check_turn(turn, spec.get("expect", {}))
            turns.append(
                {
                    "user": spec["user"],
                    "answer": turn.text,
                    "passed": all(c.ok for c in checks),
                    "checks": [asdict(c) for c in checks],
                    "tools": [{"name": t.name, "arguments": t.arguments} for t in turn.tools],
                    "rejected_drafts": turn.rejected_drafts,
                    "fallback": turn.fallback,
                    "llm_calls": turn.llm_calls,
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                    "cache_read_tokens": turn.cache_read_tokens,
                    "seconds": round(turn.seconds, 3),
                }
            )
    except Exception as exc:  # one broken scenario must not stop the run
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if previous_now is None:
            os.environ.pop("STORE_NOW", None)
        else:
            os.environ["STORE_NOW"] = previous_now

    return {
        "id": scenario["id"],
        "category": scenario["category"],
        "title": scenario["title"],
        "passed": error is None and all(t["passed"] for t in turns),
        "error": error,
        "turns": turns,
    }


def _p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=20)[-1]


def summarize(results: list[dict[str, Any]], model: str) -> dict[str, Any]:
    turns = [t for r in results for t in r["turns"]]
    checks = [c for t in turns for c in t["checks"]]
    latencies = [t["seconds"] for t in turns]
    by_category: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r["passed"])

    return {
        "model": model,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "scenarios": len(results),
        "scenarios_passed": sum(r["passed"] for r in results),
        "turns": len(turns),
        "checks": len(checks),
        "checks_passed": sum(c["ok"] for c in checks),
        "errors": sum(r["error"] is not None for r in results),
        "firewall_blocks": sum(len(t["rejected_drafts"]) for t in turns),
        "fallbacks": sum(t["fallback"] for t in turns),
        "latency_mean_s": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "latency_p95_s": round(_p95(latencies), 2),
        "llm_calls": sum(t["llm_calls"] for t in turns),
        "input_tokens": sum(t["input_tokens"] for t in turns),
        "output_tokens": sum(t["output_tokens"] for t in turns),
        "cache_read_tokens": sum(t["cache_read_tokens"] for t in turns),
        "by_category": {k: {"passed": sum(v), "total": len(v)} for k, v in by_category.items()},
    }


def render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    s = summary
    lines = [
        f"# Avaliação · {s['model']}",
        "",
        f"Rodada em {s['run_at']}. **{s['scenarios_passed']} de {s['scenarios']} cenários "
        f"passaram** ({s['checks_passed']} de {s['checks']} verificações).",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        f"| Turnos | {s['turns']} |",
        f"| Latência média por turno | {s['latency_mean_s']} s |",
        f"| Latência p95 por turno | {s['latency_p95_s']} s |",
        f"| Chamadas ao modelo | {s['llm_calls']} |",
        f"| Tokens de entrada (dos quais em cache) | {s['input_tokens']} "
        f"({s['cache_read_tokens']}) |",
        f"| Tokens de saída | {s['output_tokens']} |",
        f"| Rascunhos bloqueados pelo firewall | {s['firewall_blocks']} |",
        f"| Respostas seguras (fallback) | {s['fallbacks']} |",
        f"| Erros de execução | {s['errors']} |",
        "",
        "## Por categoria",
        "",
        "| Categoria | Passaram |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v['passed']}/{v['total']} |" for k, v in s["by_category"].items()]
    lines += [
        "",
        "## Cenários",
        "",
        "| Cenário | Resultado | O que falhou |",
        "| --- | --- | --- |",
    ]
    for r in results:
        failed = [
            f"{c['name']} ({c['detail']})" for t in r["turns"] for c in t["checks"] if not c["ok"]
        ]
        if r["error"]:
            failed.insert(0, f"erro: {r['error']}")
        status = "passou" if r["passed"] else "falhou"
        detail = "; ".join(failed).replace("|", "/")
        lines.append(f"| `{r['id']}` {r['title']} | {status} | {detail} |")

    failures = [r for r in results if not r["passed"]]
    if failures:
        lines += ["", "## Conversas que falharam", ""]
        for r in failures:
            lines += [f"### {r['id']}", ""]
            for t in r["turns"]:
                tools = ", ".join(x["name"] for x in t["tools"]) or "nenhuma"
                lines += [
                    f"**Cliente:** {t['user']}",
                    "",
                    f"**Lia:** {t['answer']}",
                    "",
                    f"_Ferramentas: {tools}_",
                    "",
                ]
            if r["error"]:
                lines += [f"_Erro: {r['error']}_", ""]
    return "\n".join(lines) + "\n"


def save(summary: dict[str, Any], results: list[dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}_{re.sub(r'[^A-Za-z0-9._-]+', '-', summary['model'])}"
    data = {"summary": summary, "results": results}
    (out_dir / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = out_dir / f"{name}.md"
    md.write_text(render_markdown(summary, results), encoding="utf-8")
    return md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roda a suíte de avaliação da Lia.")
    parser.add_argument("--provider", help="anthropic ou openai (padrão: LLM_PROVIDER)")
    parser.add_argument("--model", help="modelo (padrão: LLM_MODEL)")
    parser.add_argument("--only", help="ids ou categorias, separados por vírgula")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="pasta dos resultados")
    parser.add_argument("--no-save", action="store_true", help="só mostra, não grava arquivos")
    args = parser.parse_args(argv)

    scenarios = select(load_scenarios(), args.only)
    if not scenarios:
        print("Nenhum cenário corresponde a --only.", file=sys.stderr)
        return 2

    try:
        llm = make_client(args.provider, args.model)
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    store = get_store()
    results = []
    started = time.perf_counter()
    for i, scenario in enumerate(scenarios, 1):
        result = run_scenario(scenario, llm, store)
        results.append(result)
        mark = "ok   " if result["passed"] else "FALHA"
        print(f"[{i:2}/{len(scenarios)}] {mark} {scenario['id']}", flush=True)

    summary = summarize(results, llm.model)
    print(
        f"\n{summary['scenarios_passed']}/{summary['scenarios']} cenários · "
        f"{summary['checks_passed']}/{summary['checks']} verificações · "
        f"latência média {summary['latency_mean_s']} s · "
        f"{time.perf_counter() - started:.0f} s no total"
    )
    if not args.no_save:
        print(f"Relatório: {save(summary, results, args.out)}")
    return 0 if summary["scenarios_passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    sys.exit(main())
