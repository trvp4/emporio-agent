"""Builds the system prompt from the versioned template in prompt.md.

The prompt has two parts:
- a static part (persona, rules and the full policy manual), identical on every
  turn so providers can cache it;
- a small dynamic part with the current date, time and whether the store is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from string import Template

from emporio.config import DOCS_DIR, store_now
from emporio.core import rules

TEMPLATE_PATH = Path(__file__).with_name("prompt.md")
POLICIES_PATH = DOCS_DIR / "policies.md"


@dataclass(frozen=True)
class SystemPrompt:
    static: str
    dynamic: str

    def __str__(self) -> str:
        return f"{self.static}\n\n{self.dynamic}"


@lru_cache(maxsize=1)
def policies_text() -> str:
    return POLICIES_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def static_prompt() -> str:
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    # safe_substitute: the text is full of "R$", which is not a placeholder.
    return template.safe_substitute(policies=policies_text()).strip()


def dynamic_context(now: datetime) -> str:
    status = rules.store_status(now)
    if status.is_open:
        situation = f"aberta agora (hoje: {status.today_hours})"
    else:
        reason = f" ({status.reason})" if status.reason else ""
        situation = f"fechada agora{reason}. A equipe volta {status.next_opening}."
    return f"# Contexto atual\n\n- Agora: {status.now}\n- Loja: {situation}"


def build_system_prompt(now: datetime | None = None) -> SystemPrompt:
    return SystemPrompt(static=static_prompt(), dynamic=dynamic_context(now or store_now()))
