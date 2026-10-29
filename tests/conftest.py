"""bakery/tests/conftest.py - keep every test off the real database

BAKERY_DATABASE_URL outranks Database(directory=...); unset, each test
gets its own SQLite file under tmp_path.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import pytest

from database import URL_VARIABLE


@pytest.fixture(autouse=True)
def no_database_from_environment(monkeypatch):
    """Unset BAKERY_DATABASE_URL for the test."""
    monkeypatch.delenv(URL_VARIABLE, raising=False)
