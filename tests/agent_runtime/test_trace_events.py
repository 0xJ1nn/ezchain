"""What one recorded session contains: every step, with the fields the ADR names.

ADR-0042's audit trail names session, step, prompt hash, model id, tool, arguments, result
summary, latency, tokens, cost and approval id. This file walks a real recorded session and
pins each field's presence and type, so a later edit that drops one fails here rather than in
an incident.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    APPROVALS,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    ModelUsage,
    RuntimeConfig,
    ToolCall,
    ToolResult,
    digest_json,
    digest_text,
)
from ezchain_agent_runtime.trace import FileTraceLog, TraceEvent, TraceEventKind

PROMPT = "what is my equity?"


def _record(tmp_path: Path, model: FakeModel, *, results=None) -> tuple[list[TraceEvent], str]:
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results=results or {ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(),
        clock=FixedClock(),
        config=RuntimeConfig(model_id="provider/model-v1"),
        trace=FileTraceLog(tmp_path),
    )
    outcome = loop.run(PROMPT)
    return list(FileTraceLog(tmp_path).read(outcome.session_id)), outcome.session_id


def _kinds(events: list[TraceEvent]) -> list[str]:
    return [event.kind.value for event in events]


def test_a_session_records_every_step_it_took(tmp_path: Path) -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={"account": "main"}),),
                usage=ModelUsage(prompt_tokens=9, completion_tokens=3, cost_usd=Decimal("0.01")),
            ),
            ModelTurn(
                answer=Answer(summary="Read from the account."),
                usage=ModelUsage(prompt_tokens=5, completion_tokens=2, cost_usd=Decimal("0.02")),
            ),
        ]
    )
    events, _session_id = _record(tmp_path, model)

    assert _kinds(events) == [
        "session_start",
        "kill_switch",
        "turn",
        "budget_debit",
        "kill_switch",
        "tool_call",
        "kill_switch",
        "turn",
        "budget_debit",
        "outcome",
    ]


def test_the_session_start_records_the_prompt_hash_and_the_model(tmp_path: Path) -> None:
    events, _session_id = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    )

    start = events[0]
    assert start.kind is TraceEventKind.SESSION_START
    assert start.fields["prompt_hash"] == digest_text(PROMPT)
    assert start.fields["model_id"] == "provider/model-v1"
    assert isinstance(start.at, datetime)


def test_a_turn_records_tokens_cost_latency_and_what_it_returned(tmp_path: Path) -> None:
    model = FakeModel(
        [
            ModelTurn(
                tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),),
                usage=ModelUsage(prompt_tokens=9, completion_tokens=3, cost_usd=Decimal("0.01")),
            ),
            ModelTurn(answer=Answer(summary="Done.")),
        ]
    )
    events, _session_id = _record(tmp_path, model)

    turn = events[2]
    assert turn.kind is TraceEventKind.TURN
    assert turn.fields["index"] == 0
    assert turn.fields["model_id"] == "provider/model-v1"
    assert (turn.fields["prompt_tokens"], turn.fields["completion_tokens"]) == (9, 3)
    assert turn.fields["tokens"] == 12
    assert turn.fields["cost_usd"] == "0.01"
    assert turn.fields["tool_calls"] == 1
    assert turn.fields["has_answer"] is False
    assert isinstance(turn.fields["latency_ms"], int)


def test_a_tool_call_records_the_name_the_arguments_digest_and_the_status(
    tmp_path: Path,
) -> None:
    arguments = {"account": "main"}
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments=arguments),)),
            ModelTurn(answer=Answer(summary="Done.")),
        ]
    )
    events, _session_id = _record(tmp_path, model)

    call = events[5]
    assert call.kind is TraceEventKind.TOOL_CALL
    assert call.fields["call_id"] == "c1"
    assert call.fields["tool"] == ACCOUNT.name
    assert call.fields["arguments_digest"] == digest_json(arguments)
    assert call.fields["result_digest"] == digest_json(ACCOUNT_RESULT.data)
    assert call.fields["status"] == "ok"
    assert call.fields["code"] is None
    assert isinstance(call.fields["latency_ms"], int)


def test_a_budget_debit_records_what_it_spent_and_the_running_totals(tmp_path: Path) -> None:
    model = FakeModel(
        [
            ModelTurn(
                answer=Answer(summary="nothing to report"),
                usage=ModelUsage(prompt_tokens=7, completion_tokens=3, cost_usd=Decimal("0.02")),
            )
        ]
    )
    events, _session_id = _record(tmp_path, model)

    debit = events[3]
    assert debit.kind is TraceEventKind.BUDGET_DEBIT
    assert (debit.fields["turns"], debit.fields["tokens"]) == (1, 10)
    assert debit.fields["cost_usd"] == "0.02"
    assert debit.fields["session"] == {"turns": 1, "tokens": 10, "cost_usd": "0.02"}
    assert debit.fields["daily"] == {"turns": 1, "tokens": 10, "cost_usd": "0.02"}


def test_every_kill_switch_observation_is_recorded_even_when_it_is_clear(tmp_path: Path) -> None:
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="Done.")),
        ]
    )
    events, _session_id = _record(tmp_path, model)

    observed = [event for event in events if event.kind is TraceEventKind.KILL_SWITCH]
    assert len(observed) == 3
    assert all(event.fields["armed"] is False for event in observed)


def test_the_outcome_records_the_verdict_the_spend_and_the_approval(tmp_path: Path) -> None:
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name=APPROVALS.name, arguments={}),))]
    )
    pending = ToolResult(
        tool=APPROVALS.name,
        data={"status": "pending_approval", "approval_id": "ap-42"},
    )
    events, _session_id = _record(tmp_path, model, results={APPROVALS.name: pending})

    outcome = events[-1]
    assert outcome.kind is TraceEventKind.OUTCOME
    assert outcome.fields["state"] == "awaiting_approval"
    assert outcome.fields["reason"] == "AWAITING_APPROVAL"
    assert outcome.fields["approval_id"] == "ap-42"
    assert outcome.fields["turns"] == 1
    assert isinstance(outcome.fields["numbers"], int)


def test_a_refused_call_is_recorded_with_its_code_and_no_result(tmp_path: Path) -> None:
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name="shell", arguments={"cmd": "ls"}),))]
    )
    events, _session_id = _record(tmp_path, model)

    call = events[5]
    assert call.fields["status"] == "refused"
    assert call.fields["code"] == "TOOL_OUT_OF_SCOPE"
    assert call.fields["result_digest"] is None


def test_an_event_sequence_is_dense_and_starts_at_zero(tmp_path: Path) -> None:
    events, _session_id = _record(
        tmp_path, FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))])
    )

    assert [event.sequence for event in events] == list(range(len(events)))
