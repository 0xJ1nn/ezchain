"""The headless command: same loop, same approval gate, and a trace a person can follow."""

from __future__ import annotations

import io
import sys
from decimal import Decimal

import pytest

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    InMemoryKillSwitch,
    ModelRequest,
    ModelTurn,
    NumberClaim,
    ToolCall,
    ToolResult,
)
from ezchain_harness import ExitCode, cli, run_session
from ezchain_harness.trace import PREFIX
from tests.agent_runtime.agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    PLACE_ORDER,
    SCOPED_TOOLS,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)
from tests.harness.helpers import broken_factory, not_a_client, provide

APPROVAL_RESULT = ToolResult(
    tool=PLACE_ORDER.name,
    data={
        "id": "o1",
        "status": "pending_approval",
        "approval_id": "a1",
        "approval_status": "pending",
    },
)


def account_answer() -> ModelTurn:
    return ModelTurn(
        answer=Answer(
            summary="Read from the account.",
            claims=(
                NumberClaim(
                    label="equity",
                    value=Decimal("1000.00"),
                    tool=ACCOUNT.name,
                    path="equity",
                ),
            ),
        )
    )


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, list[str], str]:
    code = cli.main([*argv])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_a_pending_order_reports_awaiting_approval_and_executes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name=PLACE_ORDER.name, arguments={}),))]
    )
    mcp = FakeMcp(results={PLACE_ORDER.name: APPROVAL_RESULT})
    model_spec, toolbox_spec = provide(model, mcp)

    code, lines, _ = run(
        capsys, "--prompt", "place the order", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.AWAITING_APPROVAL
    assert code != 0
    assert f"{PREFIX}end AWAITING_APPROVAL state=awaiting_approval" in lines[-1]
    assert "waits for a person" in lines[-1]
    assert not any(line.startswith(f"{PREFIX}answer") for line in lines)
    assert mcp.calls == [(PLACE_ORDER.name, {})]


def test_the_command_drives_the_real_agent_loop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[AgentLoop] = []

    class SpyLoop(AgentLoop):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            built.append(self)

    monkeypatch.setattr(cli, "AgentLoop", SpyLoop)
    model_spec, toolbox_spec = provide(
        FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))]), FakeMcp()
    )

    code, _, _ = run(capsys, "--prompt", "hello", "--model", model_spec, "--toolbox", toolbox_spec)

    assert code == ExitCode.COMPLETED
    assert len(built) == 1
    assert type(built[0]) is SpyLoop


def test_the_trace_is_written_as_the_session_proceeds() -> None:
    """Each line reaches the stream before the next step runs, not in one block at the end."""
    stream = io.StringIO()
    seen: list[int] = []

    class Watcher:
        def __init__(self, inner: FakeModel) -> None:
            self._inner = inner

        def complete(self, request: ModelRequest) -> ModelTurn:
            seen.append(len(stream.getvalue().splitlines()))
            return self._inner.complete(request)

    result = run_session(
        model=Watcher(FakeModel([ModelTurn(answer=Answer(summary="done"))])),
        toolbox=FakeMcp(),
        prompt="hello",
        out=stream,
    )
    lines = stream.getvalue().splitlines()

    assert result.exit_code is ExitCode.COMPLETED
    assert seen == [3], "session, surface and turn 1 must be on the stream before turn 1 runs"
    assert lines[:3] == [
        lines[0],
        f"{PREFIX}tools 4: " + ", ".join(tool.name for tool in SCOPED_TOOLS),
        f"{PREFIX}turn 1",
    ]
    assert len(lines) > seen[0]


def test_every_step_is_printed_to_stdout_in_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            account_answer(),
        ]
    )
    model_spec, toolbox_spec = provide(model, FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}))

    code, lines, _ = run(
        capsys, "--prompt", "equity?", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.COMPLETED
    shapes = [line.removeprefix(PREFIX).split(" ", 1)[0] for line in lines]
    assert [line.startswith(PREFIX) for line in lines] == [True] * len(lines)
    assert lines[0].startswith(f"{PREFIX}session ")
    assert lines[1] == f"{PREFIX}tools 4: " + ", ".join(tool.name for tool in SCOPED_TOOLS)
    assert lines[2] == f"{PREFIX}turn 1"
    assert lines[3] == f"{PREFIX}call {ACCOUNT.name} {{}}"
    assert lines[4] == (
        f"{PREFIX}result {ACCOUNT.name} ok "
        'as_of="2026-11-14T09:00:00Z", cash="1000.00", equity="1000.00"'
    )
    assert lines[5] == f"{PREFIX}turn 2"
    assert lines[6] == f"{PREFIX}answer Read from the account."
    assert lines[7].startswith(f"{PREFIX}number equity 1000.00 (source {ACCOUNT.name}.equity, age ")
    assert lines[-1] == f"{PREFIX}end COMPLETED state=done tools=1 turns=2 tokens=0 cost_usd=0.00"
    assert shapes == [
        "session",
        "tools",
        "turn",
        "call",
        "result",
        "turn",
        "answer",
        "number",
        "end",
    ]


def test_a_completed_session_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    model_spec, toolbox_spec = provide(
        FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))]), FakeMcp()
    )

    code, lines, _ = run(
        capsys, "--prompt", "hello", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.COMPLETED
    assert int(ExitCode.COMPLETED) == 0
    assert lines[-1].startswith(f"{PREFIX}end COMPLETED state=done")


def test_a_budget_stop_exits_with_the_stopped_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="never reached")),
        ]
    )
    model_spec, toolbox_spec = provide(model, FakeMcp(results={ACCOUNT.name: ACCOUNT_RESULT}))

    code, lines, _ = run(
        capsys,
        "--prompt",
        "equity?",
        "--model",
        model_spec,
        "--toolbox",
        toolbox_spec,
        "--session-turns",
        "1",
    )

    assert code == ExitCode.STOPPED
    assert int(ExitCode.STOPPED) == 3
    assert lines[-1].startswith(f"{PREFIX}end SESSION_TURNS_EXHAUSTED state=stopped")


def test_a_platform_kill_switch_refusal_exits_with_the_stopped_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    refusal = ToolResult(
        tool="ezchain_place_order",
        data={"code": "KILL_SWITCH_ACTIVE", "message": "entry refused"},
        is_error=True,
    )
    model = FakeModel(
        [ModelTurn(tool_calls=(ToolCall(id="c1", name="ezchain_place_order", arguments={}),))]
    )
    model_spec, toolbox_spec = provide(model, FakeMcp(results={"ezchain_place_order": refusal}))

    code, lines, _ = run(
        capsys, "--prompt", "buy", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.STOPPED
    assert int(ExitCode.STOPPED) == 3
    assert lines[-1].startswith(f"{PREFIX}end KILL_SWITCH_ACTIVE state=stopped")


def test_an_armed_runtime_kill_switch_stops_before_the_first_turn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run_session(
        model=FakeModel([ModelTurn(answer=Answer(summary="never reached"))]),
        toolbox=FakeMcp(),
        prompt="hello",
        limits=make_limits(),
        kill_switch=InMemoryKillSwitch(armed=True, reason="operator halt"),
        clock=FixedClock(),
    )
    lines = capsys.readouterr().out.splitlines()

    assert result.exit_code is ExitCode.STOPPED
    assert not any(line.endswith("turn 1") for line in lines)
    assert lines[-1].startswith(f"{PREFIX}end KILL_SWITCH_ARMED state=stopped")
    assert "operator halt" in lines[-1]


def test_a_failed_session_exits_with_the_failed_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_spec, toolbox_spec = provide(FakeModel([RuntimeError("provider down")]), FakeMcp())

    code, lines, _ = run(
        capsys, "--prompt", "hello", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.FAILED
    assert int(ExitCode.FAILED) == 1
    assert lines[-1].startswith(f"{PREFIX}end MODEL_FAILED state=failed")
    assert "provider down" in lines[-1]


def test_a_tool_result_is_summarised_on_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    big = ToolResult(tool=ACCOUNT.name, data={"blob": "x" * 5_000, "equity": "1000.00"})
    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),)),
            ModelTurn(answer=Answer(summary="done")),
        ]
    )
    model_spec, toolbox_spec = provide(model, FakeMcp(results={ACCOUNT.name: big}))

    _, lines, _ = run(
        capsys, "--prompt", "equity?", "--model", model_spec, "--toolbox", toolbox_spec
    )
    result_line = next(line for line in lines if f"{PREFIX}result " in line)

    assert len(result_line) < 320
    assert result_line.endswith("...")


def test_a_tool_call_is_printed_before_its_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []

    model = FakeModel(
        [
            ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={"limit": 1}),)),
            ModelTurn(answer=Answer(summary="done")),
        ]
    )
    mcp = FakeMcp(
        results={ACCOUNT.name: ACCOUNT_RESULT},
        on_call=lambda name, arguments: seen.append(name),
    )
    model_spec, toolbox_spec = provide(model, mcp)

    _, lines, _ = run(
        capsys, "--prompt", "equity?", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert lines.index(f'{PREFIX}call {ACCOUNT.name} {{"limit":1}}') < lines.index(
        f"{PREFIX}result {ACCOUNT.name} ok "
        'as_of="2026-11-14T09:00:00Z", cash="1000.00", equity="1000.00"'
    )
    assert seen == [ACCOUNT.name]


def test_a_tool_that_raises_is_traced_and_fails_the_session_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = FakeModel([ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),))])
    model_spec, toolbox_spec = provide(
        model, FakeMcp(results={ACCOUNT.name: RuntimeError("child died")})
    )

    code, lines, _ = run(
        capsys, "--prompt", "equity?", "--model", model_spec, "--toolbox", toolbox_spec
    )

    assert code == ExitCode.FAILED
    assert f"{PREFIX}result {ACCOUNT.name} error child died" in lines
    assert lines[-1].startswith(f"{PREFIX}end MCP_UNREACHABLE state=failed")


def test_a_missing_prompt_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    model_spec, toolbox_spec = provide(FakeModel([]), FakeMcp())

    code, _, err = run(capsys, "--model", model_spec, "--toolbox", toolbox_spec)

    assert code == ExitCode.USAGE
    assert int(ExitCode.USAGE) == 2
    assert "no prompt" in err


def test_blank_and_stdin_prompts_are_resolved(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    model_spec, toolbox_spec = provide(
        FakeModel([ModelTurn(answer=Answer(summary="nothing to report"))]), FakeMcp()
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("  from stdin  \n"))

    code, _, _ = run(capsys, "--model", model_spec, "--toolbox", toolbox_spec)

    assert code == ExitCode.COMPLETED


def test_an_unimportable_model_factory_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, toolbox_spec = provide(FakeModel([]), FakeMcp())

    code, _, err = run(
        capsys, "--prompt", "hello", "--model", "no_such_module:client", "--toolbox", toolbox_spec
    )

    assert code == ExitCode.USAGE
    assert int(ExitCode.USAGE) == 2
    assert "cannot import no_such_module" in err


def test_a_factory_that_returns_no_client_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, toolbox_spec = provide(FakeModel([]), FakeMcp())

    code, _, err = run(
        capsys,
        "--prompt",
        "hello",
        "--model",
        f"{not_a_client.__module__}:not_a_client",
        "--toolbox",
        toolbox_spec,
    )

    assert code == ExitCode.USAGE
    assert int(ExitCode.USAGE) == 2
    assert "no model client" in err


def test_a_factory_that_cannot_start_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, toolbox_spec = provide(FakeModel([]), FakeMcp())

    code, _, err = run(
        capsys,
        "--prompt",
        "hello",
        "--model",
        f"{broken_factory.__module__}:broken_factory",
        "--toolbox",
        toolbox_spec,
    )

    assert code == ExitCode.USAGE
    assert int(ExitCode.USAGE) == 2
    assert "the provider key is missing" in err


def test_a_bad_budget_flag_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    model_spec, toolbox_spec = provide(FakeModel([]), FakeMcp())

    code, _, err = run(
        capsys,
        "--prompt",
        "hello",
        "--model",
        model_spec,
        "--toolbox",
        toolbox_spec,
        "--session-cost-usd",
        "one",
    )

    assert code == ExitCode.USAGE
    assert int(ExitCode.USAGE) == 2
    assert "--session-cost-usd must be a decimal" in err


def test_the_outcome_codes_are_distinct_and_completion_is_the_only_zero() -> None:
    assert int(ExitCode.COMPLETED) == 0
    assert ExitCode.COMPLETED.value == 0
    others = [code for code in ExitCode if code is not ExitCode.COMPLETED]
    assert len({code.value for code in others}) == 4
    assert all(code.value != 0 for code in others)
