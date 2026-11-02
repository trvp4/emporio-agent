import os
import tempfile

import pytest

# Pin the store clock for deterministic tests, regardless of the local .env.
os.environ["STORE_NOW"] = "2026-03-25T10:30:00"
os.environ["EMPORIO_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="emporio-tests-")


@pytest.fixture(scope="session")
def store():
    from emporio.core.data import load_store

    return load_store()
