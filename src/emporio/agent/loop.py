"""The agent loop: tool use, conversation memory and the hallucination firewall.

One customer message goes in, one answer comes out:

    user message -> LLM -> (tool calls -> tool results -> LLM)* -> draft answer
                 -> firewall -> answer   (or a correction and another try)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from emporio.agent.grounding import GroundingReport, build_evidence, check_answer
from emporio.agent.llm import LLMClient
from emporio.agent.prompt import build_system_prompt, policies_text
from emporio.core.data import StoreData, get_store
from emporio.core.tools import TOOLS, execute

CORRECTION = (
    "[Verificação automática do sistema, não é mensagem do cliente] Sua resposta citou valores "
    "que não aparecem nos resultados das ferramentas nem no manual: {values}. Reescreva a "
    "resposta usando somente valores exatamente como as ferramentas retornaram, sem fazer "
    "contas. Se precisar de um dado, chame a ferramenta. Não mencione esta verificação."
)
FALLBACK = (
    "Quero te passar essa informação certinha, então prefiro confirmar com a equipe antes. "
    "Posso abrir um chamado para eles te retornarem?"
)
THINKING_TYPES = {"thinking", "redacted_thinking"}
LOOP_LIMIT = (
    "Desculpe, tive um problema para concluir sua consulta agora. Pode tentar de novo ou, se "
    "preferir, abro um chamado para a equipe te responder."
)


@dataclass
class ToolTrace:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ms: float


@dataclass
class Turn:
    text: str
    tools: list[ToolTrace] = field(default_factory=list)
    grounding: GroundingReport | None = None
    rejected_drafts: list[str] = field(default_factory=list)
    fallback: bool = False
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    seconds: float = 0.0


def _api_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages as sent to the provider (internal flags stripped)."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _without_thinking(message: dict[str, Any]) -> dict[str, Any]:
    """Drop thinking blocks from a finished turn.

    A thinking block is valid only while everything before it is unchanged, and
    memory is edited between turns (rejected drafts removed, old turns trimmed).
    Inside a turn the blocks go back untouched; once the turn ends they are dropped.
    """
    if message["role"] != "assistant" or isinstance(message["content"], str):
        return message
    blocks = [b for b in message["content"] if b["type"] not in THINKING_TYPES]
    return {**message, "content": blocks}


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        store: StoreData | None = None,
        max_steps: int = 8,
        max_grounding_retries: int = 1,
        max_turns_in_memory: int = 20,
    ):
        self.llm = llm
        self.store = store or get_store()
        self.max_steps = max_steps
        self.max_grounding_retries = max_grounding_retries
        self.max_turns_in_memory = max_turns_in_memory
        self.messages: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.messages = []

    # -- memory -----------------------------------------------------------------

    def _trim_history(self) -> None:
        """Keep the last N customer turns. Cuts only at a customer message boundary."""
        starts = [
            i for i, m in enumerate(self.messages)
            if m["role"] == "user" and isinstance(m["content"], str)
        ]  # fmt: skip
        if len(starts) > self.max_turns_in_memory:
            self.messages = self.messages[starts[-self.max_turns_in_memory] :]

    def _evidence(self):
        results, user_texts = [], []
        for m in self.messages:
            if m.get("_rejected"):
                continue
            if m["role"] == "user" and isinstance(m["content"], str):
                user_texts.append(m["content"])
            elif m["role"] == "user":
                results += [b["content"] for b in m["content"] if b["type"] == "tool_result"]
        return build_evidence(results, policies_text(), user_texts)

    # -- one turn ---------------------------------------------------------------

    def reply(self, user_text: str) -> Turn:
        started = time.perf_counter()
        self.messages.append({"role": "user", "content": user_text})
        turn = Turn(text="")
        system = build_system_prompt()
        retries = 0

        for _ in range(self.max_steps):
            response = self.llm.complete(system, _api_view(self.messages), TOOLS)
            turn.llm_calls += 1
            turn.input_tokens += response.input_tokens
            turn.output_tokens += response.output_tokens
            turn.cache_read_tokens += response.cache_read_tokens

            if response.tool_calls:
                self.messages.append(response.as_message())
                results = []
                for call in response.tool_calls:
                    t0 = time.perf_counter()
                    result = execute(call.name, call.arguments, self.store)
                    turn.tools.append(
                        ToolTrace(
                            call.name, call.arguments, result, (time.perf_counter() - t0) * 1000
                        )
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                self.messages.append({"role": "user", "content": results})
                continue

            draft = response.text
            if not draft:  # a refusal, or an answer left only in the thinking
                turn.text, turn.fallback = FALLBACK, True
                self.messages.append({"role": "assistant", "content": FALLBACK})
                break
            report = check_answer(draft, self._evidence())
            turn.grounding = report
            if report.ok:
                self.messages.append(response.as_message())
                turn.text = draft
                break

            turn.rejected_drafts.append(draft)
            if retries >= self.max_grounding_retries:
                turn.text, turn.fallback = FALLBACK, True
                self.messages.append({"role": "assistant", "content": FALLBACK})
                break
            retries += 1
            # Keep the rejected draft and the correction out of long-term memory.
            self.messages.append({**response.as_message(), "_rejected": True})
            self.messages.append(
                {
                    "role": "user",
                    "content": CORRECTION.format(values=", ".join(report.unsupported)),
                    "_rejected": True,
                }
            )
        else:
            turn.text, turn.fallback = LOOP_LIMIT, True
            self.messages.append({"role": "assistant", "content": LOOP_LIMIT})

        self.messages = [_without_thinking(m) for m in self.messages if not m.get("_rejected")]
        self._trim_history()
        turn.seconds = time.perf_counter() - started
        return turn
