"""Each test starts and ends with an empty day, and imports this checkout.

The daily ledger is process-scoped by design, so tests that share a process must clear it
between cases or they would inherit one another's spend.

The import path is pinned before the first package import because this suite's whole claim
is that it drives **this** checkout's loop, MCP server and API. The development venv
installs the packages editable with absolute paths, so inside a git worktree those imports
resolve to the other checkout and a green suite would say nothing about the code under
review. Prepending the repo's own source roots makes "the real thing" mean this tree, in a
worktree or not.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    "packages/core-py/src",
    "packages/adapters/src",
    "packages/sdk-py/src",
    "apps/api/src",
    "apps/mcp/src",
    "services/agent-runtime/src",
)

for _relative in reversed(SOURCE_ROOTS):
    _path = str(ROOT / _relative)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ezchain_agent_runtime import PROCESS_SPEND  # noqa: E402  (after the path pin)


@pytest.fixture(autouse=True)
def _empty_day() -> Iterator[None]:
    PROCESS_SPEND.clear()
    yield
    PROCESS_SPEND.clear()
