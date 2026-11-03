"""The agent loop, driven by a scripted LLM (no API calls)."""

from emporio.agent.llm import LLMResponse, ScriptedLLM, ToolCall, to_openai_messages
from emporio.agent.loop import FALLBACK, Agent


def tool(tool_name, **args):
    call = ToolCall(id=f"call_{tool_name}", name=tool_name, arguments=args)
    return LLMResponse(tool_calls=[call])


def say(text):
    return LLMResponse(text=text, input_tokens=100, output_tokens=20)


def test_tool_call_then_grounded_answer(store):
    llm = ScriptedLLM(
        [
            tool("get_product", name="Takamine GD20"),
            say("O Takamine GD20 sai por R$ 2.199,00 (R$ 2.089,05 no PIX)."),
        ]
    )
    agent = Agent(llm, store)
    turn = agent.reply("Quanto custa o Takamine GD20?")

    assert turn.text.startswith("O Takamine GD20")
    assert [t.name for t in turn.tools] == ["get_product"]
    assert turn.tools[0].result["product_id"] == 95
    assert turn.grounding.ok
    assert turn.llm_calls == 2
    # user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant"]


def test_hallucinated_price_triggers_one_retry(store):
    llm = ScriptedLLM(
        [
            tool("get_product", product_id=95),
            say("Sai por R$ 1.999,00."),  # invented
            say("Sai por R$ 2.199,00."),  # corrected
        ]
    )
    agent = Agent(llm, store)
    turn = agent.reply("Preço do GD20?")

    assert turn.text == "Sai por R$ 2.199,00."
    assert turn.rejected_drafts == ["Sai por R$ 1.999,00."]
    # The model saw the correction on its third call...
    correction = llm.calls[2][-1]["content"]
    assert "R$ 1.999,00" in correction
    # ...but neither the bad draft nor the correction stay in memory.
    contents = str(agent.messages)
    assert "1.999,00" not in contents
    assert "Verificação automática" not in contents


def test_repeated_hallucination_falls_back_safely(store):
    llm = ScriptedLLM([say("Custa R$ 10,00."), say("Custa R$ 12,00.")])
    turn = Agent(llm, store).reply("Quanto custa uma guitarra?")
    assert turn.fallback
    assert turn.text == FALLBACK
    assert len(turn.rejected_drafts) == 2


def test_memory_carries_across_turns(store):
    llm = ScriptedLLM([say("Oi! Como posso ajudar?"), say("Claro, me passa o número do pedido.")])
    agent = Agent(llm, store)
    agent.reply("Oi")
    agent.reply("Quero ver meu pedido")
    last_call = llm.calls[-1]
    assert [m["content"] for m in last_call if m["role"] == "user"] == [
        "Oi",
        "Quero ver meu pedido",
    ]


def test_history_is_trimmed_at_turn_boundaries(store):
    llm = ScriptedLLM([say(f"resposta {i}") for i in range(5)])
    agent = Agent(llm, store, max_turns_in_memory=2)
    for i in range(5):
        agent.reply(f"mensagem {i}")
    assert agent.messages[0] == {"role": "user", "content": "mensagem 3"}


def test_thinking_goes_back_within_the_turn_and_is_dropped_after(store):
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    call = {"type": "tool_use", "id": "c1", "name": "get_product", "input": {"product_id": 95}}
    answer = {"type": "text", "text": "Sai por R$ 2.199,00."}
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall("c1", "get_product", {"product_id": 95})],
                blocks=[thinking, call],
            ),
            LLMResponse(text=answer["text"], blocks=[thinking, answer]),
        ]
    )
    agent = Agent(llm, store)
    agent.reply("Preço do GD20?")

    # Second call of the turn: the tool_use turn carries its thinking block untouched.
    assert llm.calls[1][1]["content"] == [thinking, call]
    # After the turn, memory keeps no thinking blocks (it is edited between turns).
    assert "thinking" not in str(agent.messages)
    assert agent.messages[-1]["content"] == [answer]


def test_empty_answer_falls_back(store):
    llm = ScriptedLLM([LLMResponse(text="", stop_reason="refusal")])
    turn = Agent(llm, store).reply("?")
    assert turn.fallback
    assert turn.text == FALLBACK


def test_answer_cut_off_by_max_tokens_falls_back(store):
    llm = ScriptedLLM([LLMResponse(text="O Takamine custa", stop_reason="max_tokens")])
    turn = Agent(llm, store).reply("Quanto custa o GD20?")
    assert turn.fallback


def test_loop_limit(store):
    llm = ScriptedLLM([tool("store_info") for _ in range(3)])
    turn = Agent(llm, store, max_steps=3).reply("?")
    assert turn.fallback
    assert len(turn.tools) == 3


def test_openai_message_conversion():
    messages = [
        {"role": "user", "content": "oi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "vou ver"},
                {"type": "tool_use", "id": "c1", "name": "store_info", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}],
        },
    ]
    out = to_openai_messages("sys", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"]["name"] == "store_info"
    assert out[3]["tool_call_id"] == "c1"
