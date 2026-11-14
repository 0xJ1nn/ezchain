"""Each test starts and ends with an empty day.

The daily ledger is process-scoped by design, so tests that share a process must clear it
between cases or they would inherit one another's spend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ezchain_agent_runtime import PROCESS_SPEND


@pytest.fixture(autouse=True)
def _empty_day() -> Iterator[None]:
    PROCESS_SPEND.clear()
    yield
    PROCESS_SPEND.clear()
