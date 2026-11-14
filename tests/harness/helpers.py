"""Factory wiring so the command's ``module:attribute`` flags reach the shared fakes.

:func:`provide` installs the scripted model and fake MCP server that
``tests/agent_runtime/agent_helpers.py`` already defines, and returns the two specs to pass
on the command line. Nothing here opens a socket: the model is a list of turns and the MCP
server is a dictionary of results.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.agent_runtime.agent_helpers import FakeMcp, FakeModel

MODULE = "tests.harness.helpers"


@dataclass
class _Seams:
    model: FakeModel
    toolbox: FakeMcp


_CURRENT: _Seams | None = None


def provide(model: FakeModel, toolbox: FakeMcp) -> tuple[str, str]:
    """Install the seams the factories read and return the two command-line specs."""
    global _CURRENT
    _CURRENT = _Seams(model=model, toolbox=toolbox)
    return f"{MODULE}:model_client", f"{MODULE}:mcp_toolbox"


def _current() -> _Seams:
    if _CURRENT is None:
        raise AssertionError("provide() must run before the command imports the factories")
    return _CURRENT


def model_client() -> FakeModel:
    return _current().model


def mcp_toolbox() -> FakeMcp:
    return _current().toolbox


def not_a_client() -> object:
    return object()


def broken_factory() -> FakeModel:
    raise RuntimeError("the provider key is missing")


__all__ = [
    "broken_factory",
    "mcp_toolbox",
    "model_client",
    "not_a_client",
    "provide",
]
