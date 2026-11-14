"""Redaction on write: the trace holds no credential and no prompt text.

ADR-0042 is explicit that the agent key and the model-provider credential never enter a
trace, a log line or a prompt. The trace is written by the loop, so this is where that claim
becomes checkable: a tool name, a detail message or a result that carries a token is stored
redacted, and tool arguments are stored as a digest rather than as their text.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_helpers import ACCOUNT, FakeMcp, FakeModel, FixedClock, make_limits

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    ToolCall,
    ToolResult,
    digest_json,
    redact,
)
from ezchain_agent_runtime.trace import FileTraceLog

SECRET = "ezk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def test_the_agent_key_is_redacted_from_every_kind_of_value() -> None:
    assert redact(SECRET) == "[redacted]"
    assert redact({"detail": f"refused for {SECRET}"}) == {"detail": "refused for [redacted]"}
    assert redact([SECRET, "plain"]) == ["[redacted]", "plain"]
    assert redact({"note": "nothing secret"}) == {"note": "nothing secret"}


def test_redaction_does_not_mutate_the_callers_data() -> None:
    payload = {"detail": SECRET}

    redact(payload)

    assert payload == {"detail": SECRET}


def test_an_outcome_detail_carrying_the_key_is_stored_redacted(tmp_path: Path) -> None:
    refused = ToolResult(
        tool=ACCOUNT.name,
        data={"code": "FORBIDDEN", "message": f"key {SECRET} has no scope"},
        is_error=True,
    )
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="Refused.")),
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: refused}),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("equity?")

    text = (tmp_path / f"{outcome.session_id}.jsonl").read_text()
    assert SECRET not in text
    events = FileTraceLog(tmp_path).read(outcome.session_id)
    assert events[0].fields["prompt_hash"] != "equity?"


def test_tool_arguments_are_stored_as_a_digest_not_as_text(tmp_path: Path) -> None:
    arguments = {"symbol": "ETH/USDC", "amount": "250", "note": "buy the dip"}
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments=arguments),)),
            ModelTurn(answer=Answer(summary="Done.")),
        ]
    )
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: ToolResult(tool=ACCOUNT.name, data={})}),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run("buy")

    text = (tmp_path / f"{outcome.session_id}.jsonl").read_text()
    assert "buy the dip" not in text
    stored = json.loads(text.splitlines()[5])
    assert stored["fields"]["arguments_digest"] == digest_json(arguments)
    assert "arguments" not in stored["fields"]
