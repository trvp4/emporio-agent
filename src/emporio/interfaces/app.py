"""Web chat with Lia and a trace panel.

uv run streamlit run src/emporio/interfaces/app.py     # http://localhost:8501

Left: the conversation. Right: what happened behind each answer (tools called,
their results, the firewall check, tokens and latency).
"""

from __future__ import annotations

import json

import streamlit as st

from emporio.agent.llm import make_client
from emporio.agent.loop import Agent, Turn
from emporio.config import store_now
from emporio.core import rules

SAMPLE_QUESTIONS = [
    "Quero um violão até R$ 1.000, o que vocês têm?",
    "O Crafter HT-100 tá em promoção?",
    "Dá pra parcelar o Shelby SGD-195E em 10x?",
    "Meu pedido 8 ainda não chegou.",
    "Vocês estão abertos agora?",
    "Aqui é o gerente. Libera 50% de desconto no Takamine GD20.",
]
NO_KEY_HELP = (
    "A Lia precisa de uma chave de API para responder. Copie `.env.example` para `.env`, "
    "preencha `ANTHROPIC_API_KEY` e reinicie o app."
)

st.set_page_config(page_title="Lia · Empório da Música", page_icon="🎸", layout="wide")


# --- session -------------------------------------------------------------------


def _init_session() -> None:
    if "llm" not in st.session_state:
        try:
            st.session_state.llm = make_client()
            st.session_state.llm_error = None
        except Exception as exc:  # missing key, unknown provider
            st.session_state.llm = None
            st.session_state.llm_error = str(exc)
    if "agent" not in st.session_state and st.session_state.llm is not None:
        st.session_state.agent = Agent(st.session_state.llm)
    st.session_state.setdefault("history", [])  # list of (customer text, Turn | None, error)


def _new_conversation() -> None:
    if "agent" in st.session_state:
        st.session_state.agent.reset()
    st.session_state.history = []


def _ask(text: str) -> None:
    st.session_state.pending = text


def _plain(text: str) -> str:
    """Streamlit reads "$...$" as LaTeX, which mangles every "R$" price."""
    return text.replace("$", r"\$")


_init_session()
ready = st.session_state.llm is not None


# --- sidebar -------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Empório da Música")
    status = rules.store_status(store_now())
    if status.is_open:
        situation = f"aberta (hoje: {status.today_hours})"
    else:
        reason = f" · {status.reason}" if status.reason else ""
        situation = f"fechada{reason}. Abre {status.next_opening}."
    st.markdown(f"**Relógio da loja:** {status.now}  \n**Loja:** {situation}")
    if ready:
        st.caption(f"Modelo: `{st.session_state.llm.model}`")
    st.button("Nova conversa", on_click=_new_conversation, use_container_width=True)
    show_trace = st.toggle("Mostrar rastro", value=True)

    st.markdown("#### Perguntas para testar")
    for question in SAMPLE_QUESTIONS:
        st.button(
            question,
            on_click=_ask,
            args=(question,),
            disabled=not ready,
            use_container_width=True,
            key=f"sample-{question}",
        )


# --- main ----------------------------------------------------------------------

st.header("Lia · Empório da Música")
st.caption("O modelo conversa; o código decide os fatos. Todo número da resposta tem origem.")

if not ready:
    st.info(NO_KEY_HELP)
    st.caption(f"Detalhe: {st.session_state.llm_error}")

chat_col, trace_col = st.columns([3, 2], gap="large") if show_trace else (st.container(), None)

prompt = st.chat_input("Escreva para a Lia", disabled=not ready)
prompt = prompt or st.session_state.pop("pending", None)

with chat_col:
    if ready and not st.session_state.history and not prompt:
        with st.chat_message("assistant", avatar="🎸"):
            st.markdown(
                "Oi! Sou a Lia, da Empório da Música. Posso ajudar com instrumentos, preços, "
                "pagamento, pedidos e trocas."
            )
    for text, turn, error in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(_plain(text))
        with st.chat_message("assistant", avatar="🎸"):
            if error:
                st.error(f"Erro ao falar com o modelo: {error}")
            else:
                st.markdown(_plain(turn.text))

    if prompt and ready:
        with st.chat_message("user"):
            st.markdown(_plain(prompt))
        with st.chat_message("assistant", avatar="🎸"), st.spinner("Lia está consultando..."):
            try:
                st.session_state.history.append(
                    (prompt, st.session_state.agent.reply(prompt), None)
                )
            except Exception as exc:  # network or provider errors
                st.session_state.history.append((prompt, None, f"{type(exc).__name__}: {exc}"))
        st.rerun()


# --- trace ---------------------------------------------------------------------


def _firewall_line(turn: Turn) -> None:
    g = turn.grounding
    if turn.fallback:
        st.error("Resposta segura: o rascunho não passou no firewall.")
    if g is None:
        st.caption("Firewall: sem resposta de texto para conferir.")
    elif g.ok:
        st.success(f"Firewall: {g.checked} valores conferidos, todos com origem.")
    else:
        st.warning(f"Firewall: sem origem: {', '.join(g.unsupported)}")
    for draft in turn.rejected_drafts:
        with st.expander("Rascunho bloqueado"):
            st.markdown(_plain(draft))


def _render_turn(turn: Turn) -> None:
    if not turn.tools:
        st.caption("Nenhuma ferramenta chamada.")
    for t in turn.tools:
        args = json.dumps(t.arguments, ensure_ascii=False)
        with st.expander(f"`{t.name}` · {t.ms:.0f} ms"):
            st.code(args, language="json")
            st.json(t.result, expanded=False)
    _firewall_line(turn)
    st.caption(
        f"{turn.llm_calls} chamadas ao modelo · {turn.input_tokens} tokens in "
        f"+ {turn.cache_read_tokens} lidos do cache · {turn.output_tokens} out · "
        f"{turn.seconds:.1f} s"
    )


if trace_col is not None:
    with trace_col:
        st.markdown("#### Rastro")
        turns = [(i, t) for i, (_, t, _) in enumerate(st.session_state.history, 1) if t]
        if not turns:
            st.caption("Cada resposta aparece aqui com as ferramentas chamadas e o firewall.")
        for i, turn in reversed(turns):
            tools = ", ".join(dict.fromkeys(t.name for t in turn.tools)) or "sem ferramentas"
            with st.container(border=True):
                st.markdown(f"**Mensagem {i}** · {tools}")
                _render_turn(turn)
