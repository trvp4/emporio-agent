"""Web chat with Lia and a trace panel.

uv run streamlit run src/emporio/interfaces/app.py     # http://localhost:8501

Left: the conversation. Right: what happened behind each answer (tools called,
their results, the firewall check, tokens and latency).
"""

from __future__ import annotations

import html
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

# Icons as inline SVG (24x24 grid, stroked), never emoji: they look the same on every system.
_GUITAR = (
    '<path d="M18.5 2.5l3 3-2.2 2.2-3-3z"/>'
    '<path d="M16.4 6.6 11 12"/>'
    '<path d="M10.2 9.3a3.6 3.6 0 0 0-4.1.9 3.4 3.4 0 0 0-.9 2.9 3.6 3.6 0 0 0-2.7 1.4 '
    "3.9 3.9 0 0 0 .4 5.2 3.9 3.9 0 0 0 5.2.4 3.6 3.6 0 0 0 1.4-2.7 3.4 3.4 0 0 0 "
    '2.9-.9 3.6 3.6 0 0 0 .9-4.1"/>'
    '<circle cx="9" cy="15" r="1.4"/>'
)
_PERSON = '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'
_SHIELD = '<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/>'
_CHECK = '<path d="m8.5 12 2.5 2.5 4.5-5"/>'
_CROSS = '<path d="m9.5 9.5 5 5m0-5-5 5"/>'


def _svg(paths: str, stroke: str, size: int = 24, background: str | None = None) -> str:
    """An icon; with a background it becomes a round badge (used for avatars)."""
    if background:
        paths = (
            f'<circle cx="12" cy="12" r="12" fill="{background}" stroke="none"/>'
            f'<g transform="translate(4.2 4.2) scale(0.65)">{paths}</g>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{stroke}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    )


LIA_AVATAR = _svg(_GUITAR, "#FFFFFF", 64, background="#B4462B")
USER_AVATAR = _svg(_PERSON, "#7A4A35", 64, background="#EADFD3")

st.set_page_config(page_title="Lia · Empório da Música", page_icon=LIA_AVATAR, layout="wide")

# Light styling on top of the theme in .streamlit/config.toml: brand header, chat
# bubbles, and the trace as compact cards with a firewall badge.
STYLE = """
<style>
.brand {display: flex; align-items: center; gap: 0.9rem; margin: 0.2rem 0 0.4rem;}
.brand .logo {display: flex;}
.from-customer {transform: translateY(-0.4rem);}  /* the HTML block sits lower than a <p> */
.fw svg {vertical-align: -0.2em; margin-right: 0.3rem;}
.brand h1 {font-size: 1.6rem; margin: 0; padding: 0; line-height: 1.2;}
.brand p {margin: 0; color: #7A6A5E; font-size: 0.92rem;}
.tagline {display: inline-block; margin: 0.3rem 0 1rem; padding: 0.2rem 0.7rem;
  border-radius: 999px; background: #F3ECE3; color: #7A4A35; font-size: 0.82rem;}
[data-testid="stChatMessage"] {border-radius: 0.9rem; padding: 0.8rem 1rem;}
[data-testid="stChatMessage"]:has(.from-customer) {
  background: #F3ECE3; max-width: 85%; margin-left: auto; align-items: center;}
[data-testid="stChatMessage"]:not(:has(.from-customer)) {
  background: #FFFFFF; border: 1px solid #EADFD3; max-width: 94%;}
[data-testid="stChatMessage"] strong {color: #8E3520;}
.trace-head {display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem;
  margin-bottom: 0.4rem;}
.trace-head b {margin-right: 0.2rem;}
.chip {font-family: ui-monospace, monospace; font-size: 0.75rem; padding: 0.1rem 0.5rem;
  border-radius: 999px; background: #F3ECE3; color: #5A4A40;}
.fw {display: block; margin: 0.5rem 0 0.3rem; padding: 0.45rem 0.7rem; border-radius: 0.5rem;
  font-size: 0.86rem;}
.fw.ok {background: #E7F3EA; color: #1F5C33;}
.fw.bad {background: #FBEAEA; color: #8A1F1F;}
.status {padding: 0.7rem 0.8rem; border-radius: 0.6rem; background: #FFFFFF;
  border: 1px solid #EADFD3; font-size: 0.88rem; line-height: 1.5;}
.status .dot {display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 50%;
  margin-right: 0.35rem;}
.status .open {background: #2E8B57;} .status .closed {background: #C0392B;}
.status .muted {color: #7A6A5E;}
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)


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


def _customer(text: str) -> None:
    """The customer's words, escaped, in a marked block the CSS styles as their bubble."""
    st.markdown(f'<div class="from-customer">{html.escape(text)}</div>', unsafe_allow_html=True)


def _plain(text: str) -> str:
    """Streamlit reads "$...$" as LaTeX, which mangles every "R$" price."""
    return text.replace("$", r"\$")


_init_session()
ready = st.session_state.llm is not None


# --- sidebar -------------------------------------------------------------------

with st.sidebar:
    status = rules.store_status(store_now())
    if status.is_open:
        situation = f'<span class="dot open"></span><b>Aberta agora</b> · hoje {status.today_hours}'
    else:
        reason = f" ({status.reason})" if status.reason else ""
        situation = (
            f'<span class="dot closed"></span><b>Fechada{reason}</b><br>'
            f'<span class="muted">Abre {status.next_opening}</span>'
        )
    model = f"<br>Modelo: <code>{st.session_state.llm.model}</code>" if ready else ""
    st.markdown(
        f'<div class="status">{situation}<br><span class="muted">Relógio da loja: '
        f"{status.now}{model}</span></div>",
        unsafe_allow_html=True,
    )
    st.write("")
    st.button("Nova conversa", on_click=_new_conversation, type="primary", use_container_width=True)
    show_trace = st.toggle("Mostrar rastro", value=True)

    st.markdown("##### Perguntas para testar")
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

st.markdown(
    f"""
<div class="brand">
  <div class="logo">{_svg(_GUITAR, "#FFFFFF", 48, background="#B4462B")}</div>
  <div>
    <h1>Empório da Música</h1>
    <p>Lia, atendimento virtual · instrumentos musicais em Campo Grande/MS</p>
  </div>
</div>
<span class="tagline">O modelo conversa; o código decide os fatos. Todo número da resposta
tem origem.</span>
""",
    unsafe_allow_html=True,
)

if not ready:
    st.info(NO_KEY_HELP)
    st.caption(f"Detalhe: {st.session_state.llm_error}")

chat_col, trace_col = st.columns([3, 2], gap="large") if show_trace else (st.container(), None)

prompt = st.chat_input("Escreva para a Lia", disabled=not ready)
prompt = prompt or st.session_state.pop("pending", None)

with chat_col:
    if ready and not st.session_state.history and not prompt:
        with st.chat_message("assistant", avatar=LIA_AVATAR):
            st.markdown(
                "Oi! Sou a Lia, da Empório da Música. Posso ajudar com instrumentos, preços, "
                "pagamento, pedidos e trocas."
            )
    for text, turn, error in st.session_state.history:
        with st.chat_message("user", avatar=USER_AVATAR):
            _customer(text)
        with st.chat_message("assistant", avatar=LIA_AVATAR):
            if error:
                st.error(f"Erro ao falar com o modelo: {error}")
            else:
                st.markdown(_plain(turn.text))

    if prompt and ready:
        with st.chat_message("user", avatar=USER_AVATAR):
            _customer(prompt)
        with st.chat_message("assistant", avatar=LIA_AVATAR), st.spinner("Lia está consultando..."):
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
        badge = ("bad", "Resposta segura: o rascunho não passou no firewall.")
    elif g is None:
        badge = ("bad", "Firewall: sem resposta de texto para conferir.")
    elif g.ok:
        badge = ("ok", f"Firewall: {g.checked} valores conferidos, todos com origem.")
    else:
        badge = ("bad", f"Firewall: sem origem: {', '.join(g.unsupported)}")
    kind, text = badge
    color, mark = ("#1F5C33", _CHECK) if kind == "ok" else ("#8A1F1F", _CROSS)
    icon = _svg(_SHIELD + mark, color, 18)
    st.markdown(f'<span class="fw {kind}">{icon}{text}</span>', unsafe_allow_html=True)
    for draft in turn.rejected_drafts:
        with st.expander("Rascunho bloqueado"):
            st.markdown(_plain(draft))


def _render_turn(turn: Turn) -> None:
    if not turn.tools:
        st.caption("Nenhuma ferramenta chamada.")
    for t in turn.tools:
        args = json.dumps(t.arguments, ensure_ascii=False)
        with st.expander(f"`{t.name}` · {t.ms:.0f} ms"):
            st.code(args, language="json", wrap_lines=True)
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
            names = dict.fromkeys(t.name for t in turn.tools) or {"sem ferramentas": None}
            chips = "".join(f'<span class="chip">{n}</span>' for n in names)
            with st.container(border=True):
                st.markdown(
                    f'<div class="trace-head"><b>Mensagem {i}</b>{chips}</div>',
                    unsafe_allow_html=True,
                )
                _render_turn(turn)
