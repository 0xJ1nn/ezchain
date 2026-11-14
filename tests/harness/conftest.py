"""Every test in this directory runs with a provider key set, no network, and an empty day.

The key is a fake string with a real shape, so a test that asserts the key never appears in a
log, a record file or a rendered page is exercising the redaction path rather than a typo. The
daily ledger is process-scoped, so a case that spends a token would otherwise leak into the
next one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from providers_helpers import KEY

from ezchain_agent_runtime import PROCESS_SPEND


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    yield
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _empty_day() -> Iterator[None]:
    PROCESS_SPEND.clear()
    yield
    PROCESS_SPEND.clear()
