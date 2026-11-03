"""Smoke tests for the Streamlit app, driven by a scripted model (no API calls)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from emporio.agent.llm import LLMResponse, ScriptedLLM, ToolCall

APP = str(Path(__file__).resolve().parents[1] / "src" / "emporio" / "interfaces" / "app.py")


def test_without_api_key_the_chat_is_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "chave de API" in at.info[0].value
    assert at.chat_input[0].disabled


def test_answer_and_trace_with_scripted_model():
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="get_product", arguments={"product_id": 95})]
            ),
            LLMResponse(text="O Takamine GD20 sai por R$ 2.199,00."),
        ]
    )
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["llm"] = llm
    at.session_state["llm_error"] = None
    at.run()
    at.chat_input[0].set_value("Quanto custa o Takamine GD20?").run()

    assert not at.exception
    answers = [m.markdown[0].value for m in at.chat_message if m.name == "assistant"]
    assert r"O Takamine GD20 sai por R\$ 2.199,00." in answers  # "$" escaped, not LaTeX
    assert any("get_product" in e.label for e in at.expander)
    assert "todos com origem" in at.success[0].value
