"""The evaluation suite itself: scenario file, checks and runner (no API calls)."""

import os

import yaml

from emporio.agent.llm import LLMResponse, ScriptedLLM, ToolCall
from emporio.agent.loop import Turn
from evals.checks import check_turn, validate_scenarios
from evals.run import (
    cost_usd,
    load_scenarios,
    render_markdown,
    run_scenario,
    select,
    summarize,
)


def tool(tool_name, **args):
    return LLMResponse(
        tool_calls=[ToolCall(id=f"call_{tool_name}", name=tool_name, arguments=args)]
    )


def say(text):
    return LLMResponse(text=text)


def scenario(sid):
    return next(s for s in load_scenarios() if s["id"] == sid)


def test_scenario_file_is_valid():
    scenarios = load_scenarios()
    assert len(scenarios) >= 20
    assert {s["category"] for s in scenarios} >= {"catalogo", "pagamento", "pedido", "troca"}


def test_unquoted_money_in_yaml_is_rejected():
    scenarios = yaml.safe_load(
        "- {id: x, category: c, title: t, turns: [{user: oi, expect: {contains: [R$ 114,14]}}]}"
    )
    assert any("sem centavos" in p for p in validate_scenarios(scenarios, set()))


def test_select_by_id_or_category():
    scenarios = load_scenarios()
    assert [s["id"] for s in select(scenarios, "pedido-cancelado")] == ["pedido-cancelado"]
    assert all(s["category"] == "troca" for s in select(scenarios, "troca"))


def test_text_checks_ignore_case_accents_and_currency_spacing():
    turn = Turn(text="No PIX fica **R$439,20**. Não está disponível no momento.")
    checks = check_turn(
        turn,
        {
            "contains": ["R$ 439,20"],
            "contains_any": [["nao esta disponivel"], ["xyz", "momento"]],
            "not_contains": ["R$ 417,24"],
        },
    )
    assert all(c.ok for c in checks), checks


def test_fallback_fails_unless_allowed():
    turn = Turn(text="...", fallback=True)
    assert not check_turn(turn, {})[0].ok
    assert check_turn(turn, {"allow_fallback": True}) == []


def test_grounded_answer_passes(store):
    llm = ScriptedLLM(
        [
            tool("simulate_payment", product_id=107, installments=10),
            say("Em 10x não dá, mas consigo em até 7x de R$ 114,14 sem juros."),
        ]
    )
    result = run_scenario(scenario("pagamento-10x-recusado"), llm, store)
    assert result["passed"], result
    assert result["turns"][0]["tools"][0]["name"] == "simulate_payment"


def test_missing_tool_and_value_fail(store):
    llm = ScriptedLLM([say("Dá sim, em 10x!")])
    result = run_scenario(scenario("pagamento-10x-recusado"), llm, store)
    failed = {c["name"] for c in result["turns"][0]["checks"] if not c["ok"]}
    assert not result["passed"]
    assert {"tool:simulate_payment", "contains:R$ 114,14"} <= failed


def test_scenario_clock_is_applied_and_restored(store):
    seen = {}

    def answer(messages):
        seen["result"] = messages[-1]["content"][0]["content"]
        return say("Hoje estamos fechados pelo feriado. Voltamos sábado (04/04) às 09:00.")

    before = os.environ.get("STORE_NOW")
    llm = ScriptedLLM([tool("store_info"), answer])
    result = run_scenario(scenario("loja-feriado"), llm, store)
    assert result["passed"], result
    assert '"open_now": false' in seen["result"]
    assert os.environ.get("STORE_NOW") == before


def test_runner_error_is_recorded_not_raised(store):
    result = run_scenario(scenario("loja-horario"), ScriptedLLM([]), store)
    assert not result["passed"]
    assert "ran out of steps" in result["error"]


def test_cost_at_list_prices():
    tokens = {
        "input_tokens": 1_000_000,
        "cache_write_tokens": 0,
        "cache_read_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    }
    assert cost_usd("claude-haiku-5-5", tokens) == 0.61  # 0.10 + 0.01 + 0.50
    assert cost_usd("scripted", tokens) is None


def test_summary_and_report(store):
    ok = run_scenario(
        scenario("catalogo-preco-gd20"),
        ScriptedLLM([tool("get_product", name="Takamine GD20"), say("Sai por R$ 2.199,00.")]),
        store,
    )
    bad = run_scenario(scenario("loja-horario"), ScriptedLLM([]), store)
    summary = summarize([ok, bad], "scripted")
    assert summary["scenarios_passed"] == 1
    assert summary["errors"] == 1
    report = render_markdown(summary, [ok, bad])
    assert "1 de 2 cenários" in report
    assert "loja-horario" in report
