import os

import pytest

# Pin the store clock for deterministic tests, regardless of the local .env.
os.environ["STORE_NOW"] = "2026-03-25T10:30:00"


@pytest.fixture(scope="session")
def store():
    from emporio.core.data import load_store

    return load_store()
