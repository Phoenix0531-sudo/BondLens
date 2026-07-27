"""Isolate host OPENAI_* env so local developer keys never leak into unit tests."""

from __future__ import annotations

import os

import pytest

_OPENAI_ENV_PREFIX = "OPENAI_"


@pytest.fixture(autouse=True)
def _clear_host_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every OPENAI_* var before each test.

    Individual tests may re-set specific keys via monkeypatch.setenv.
    Host shells often inject OPENAI_API_KEY/BASE_URL/MODEL for demos; without
    this lock, live-provider side effects can pollute deterministic suites.
    """
    for key in list(os.environ):
        if key.startswith(_OPENAI_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
