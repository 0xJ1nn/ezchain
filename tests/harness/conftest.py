"""Every test in this directory runs with a provider key set and no network.

The key is a fake string with a real shape, so a test that asserts the key never appears in a
log, a record file or a rendered page is testing the redaction path and not a typo.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from providers_helpers import KEY


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    yield
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
