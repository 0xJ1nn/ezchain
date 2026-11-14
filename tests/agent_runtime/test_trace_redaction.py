"""Redaction on write: the trace holds no known credential shape and no prompt text.

ADR-0042 is explicit that the agent key and the model-provider credential never enter a trace, a
log line or a prompt. The trace is written by the loop, so this is where that claim becomes
checkable — and checkable *literally*: a secret is driven through the transport failure path,
which stores ``str(exc)`` in the outcome detail, and the stored detail is asserted to contain
``[redacted]``. The test just below it monkeypatches redaction away and asserts the same
assertion fails, so the file cannot go green while redaction does nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_helpers import ACCOUNT, FakeMcp, FakeModel, FixedClock, make_limits

import ezchain_agent_runtime.trace.events as trace_events
from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    ToolCall,
    ToolResult,
    TraceEncodingError,
    TraceEvent,
    TraceEventKind,
    digest_json,
    redact,
)
from ezchain_agent_runtime.trace import FileTraceLog, canonical

SECRET = "ezk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
HERE = Path(__file__).resolve().parent


def test_the_agent_key_is_redacted_from_every_kind_of_value() -> None:
    assert redact(SECRET) == "[redacted]"
    assert redact({"detail": f"refused for {SECRET}"}) == {"detail": "refused for [redacted]"}
    assert redact([SECRET, "plain"]) == ["[redacted]", "plain"]
    assert redact({"note": "nothing secret"}) == {"note": "nothing secret"}


def test_redaction_does_not_mutate_the_callers_data() -> None:
    payload = {"detail": SECRET}

    redact(payload)

    assert payload == {"detail": SECRET}


def test_every_known_credential_shape_is_redacted() -> None:
    samples = {
        "ezchain agent key": "ezk_" + "a" * 32,
        "model provider key": "sk-proj-" + "b" * 32,
        "github classic": "ghp_" + "c" * 36,
        "github oauth": "gho_" + "d" * 36,
        "github fine grained": "github_pat_" + "e" * 30,
        "gitlab": "glpat-" + "f" * 24,
        "npm": "npm_" + "g" * 32,
        "aws access key id": "AKIA" + "H" * 16,
        "aws secret": "AWS_SECRET_ACCESS_KEY=" + "i" * 40,
        "slack": "xoxb-" + "1" * 12 + "-" + "j" * 12,
        "jwt": "eyJ" + "k" * 20 + "." + "l" * 20 + "." + "m" * 20,
        "api key header": "x-api-key: " + "n" * 24,
        "token query": "?token=" + "o" * 24,
        "bearer": "Bearer " + "p" * 24,
    }
    for label, sample in samples.items():
        redacted = redact(f"value {sample} end")
        assert "[redacted]" in redacted, label
        assert sample not in redacted, label


def test_a_credential_shaped_mapping_key_is_redacted_too() -> None:
    payload = {SECRET: 1, "plain": f"uses {SECRET}"}

    assert redact(payload) == {"[redacted]": 1, "plain": "uses [redacted]"}


def test_two_field_names_that_collapse_once_redacted_are_refused() -> None:
    with pytest.raises(TraceEncodingError, match="collapse"):
        redact({SECRET: "a", "[redacted]": "b"})


def _transport_failure_events(root: Path) -> list[TraceEvent]:
    """A tool call whose transport fails: the loop stores ``str(exc)`` in the outcome detail."""
    model = FakeModel([ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),))])
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results={ACCOUNT.name: RuntimeError(f"upstream said {SECRET}")}),
        limits=make_limits(),
        clock=FixedClock(),
        trace=FileTraceLog(root),
    )
    outcome = loop.run("equity?")
    return list(FileTraceLog(root).read(outcome.session_id))


def test_a_secret_in_a_stored_detail_is_literally_redacted(tmp_path: Path) -> None:
    events = _transport_failure_events(tmp_path)

    outcome = events[-1]
    assert outcome.kind is TraceEventKind.OUTCOME
    detail = outcome.fields["detail"]
    assert isinstance(detail, str)
    assert detail == "ezchain_get_account failed: upstream said [redacted]"
    assert SECRET not in detail
    stored = (tmp_path / f"{outcome.session_id}.jsonl").read_text()
    assert "[redacted]" in stored
    assert SECRET not in stored


def test_the_stored_detail_assertion_fails_when_redaction_is_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assertion above is about stored text, not a digest: prove it can go red."""
    monkeypatch.setattr(trace_events, "redact", lambda value: value)

    detail = _transport_failure_events(tmp_path)[-1].fields["detail"]

    assert isinstance(detail, str) and SECRET in detail
    with pytest.raises(AssertionError):
        assert "[redacted]" in detail


def test_the_redaction_test_itself_fails_under_the_noredact_plugin(tmp_path: Path) -> None:
    """``-p noredact`` turns redaction off for a whole run; that run must go red."""
    completed = subprocess.run(  # noqa: S603 - the same interpreter, one test, no shell
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "noredact",
            "--no-header",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{Path(__file__).name}::test_a_secret_in_a_stored_detail_is_literally_redacted",
        ],
        cwd=HERE,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
            "PYTHONPATH": f"{HERE.parent.parent / 'services' / 'agent-runtime' / 'src'}",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "1 failed" in completed.stdout


def test_a_token_in_a_refused_tool_result_is_stored_redacted(tmp_path: Path) -> None:
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


def test_a_tool_name_carrying_a_key_is_stored_redacted() -> None:
    event = TraceEvent.create(
        session_id="s1",
        sequence=0,
        kind=TraceEventKind.TOOL_CALL,
        at=datetime(2026, 11, 14, tzinfo=UTC),
        fields={"tool": f"mcp_{SECRET}", "note": "plain"},
        previous_hash="0" * 64,
    )

    assert event.fields["tool"] == "mcp_[redacted]"
    assert SECRET not in canonical(event.record())


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
    events = FileTraceLog(tmp_path).read(outcome.session_id)
    call = next(event for event in events if event.fields.get("call_id") == "c1")
    assert call.fields["arguments_digest"] == digest_json(arguments)
    assert "arguments" not in call.fields
    assert json.dumps(call.fields, sort_keys=True).count("buy the dip") == 0
