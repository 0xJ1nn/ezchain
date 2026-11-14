"""One switch that turns redaction off, so a test can prove it depends on it.

Loaded explicitly (``pytest -p noredact``) this replaces :func:`redact` with the identity in
every module that imported it, which is how ``test_trace_redaction`` demonstrates its assertions
are live rather than vacuous. It is never loaded by a normal run.
"""

from __future__ import annotations

import ezchain_agent_runtime.trace as trace_package
import ezchain_agent_runtime.trace.events as trace_events
import ezchain_agent_runtime.trace.replay as trace_replay


def pytest_configure(config: object) -> None:
    for module in (trace_events, trace_replay, trace_package):
        module.redact = lambda value: value  # type: ignore[attr-defined]
