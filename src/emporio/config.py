"""Runtime configuration: paths, timezone and the store's notion of "now"."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("EMPORIO_DATA_DIR", REPO_ROOT / "data"))
DOCS_DIR = REPO_ROOT / "docs"
RUNTIME_DIR = Path(os.getenv("EMPORIO_RUNTIME_DIR", REPO_ROOT / "runtime"))

# Campo Grande/MS is UTC-4 all year.
STORE_TZ = ZoneInfo("America/Campo_Grande")


def store_now() -> datetime:
    """Current time at the store.

    The dataset is a snapshot (latest order on 2026-03-22). Deadlines such as
    "7 days after delivery" only make sense relative to that snapshot, so the
    clock can be pinned with STORE_NOW. Without it, the real clock is used.
    """
    pinned = os.getenv("STORE_NOW", "").strip()
    if pinned:
        dt = datetime.fromisoformat(pinned)
        return dt if dt.tzinfo else dt.replace(tzinfo=STORE_TZ)
    return datetime.now(STORE_TZ)
