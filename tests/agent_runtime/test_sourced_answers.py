"""Every number in an answer is resolved from a tool result, with its source and age."""

from __future__ import annotations

from decimal import Decimal

from agent_helpers import (
    ACCOUNT,
    ACCOUNT_RESULT,
    STATE,
    FakeMcp,
    FakeModel,
    FixedClock,
    make_limits,
)

from ezchain_agent_runtime import (
    AgentLoop,
    Answer,
    ModelTurn,
    NumberClaim,
    SessionState,
    StopReason,
    ToolCall,
    ToolResult,
)

TOOL_TURN = ModelTurn(tool_calls=(ToolCall(id="c1", name=ACCOUNT.name, arguments={}),))


def _answer(value: str, *, tool: str = ACCOUNT.name, path: str = "equity") -> ModelTurn:
    return ModelTurn(
        answer=Answer(
            summary="The account is unchanged.",
            claims=(NumberClaim(label="equity", value=Decimal(value), tool=tool, path=path),),
        )
    )


def _run(
    answer: ModelTurn,
    *,
    results: dict[str, ToolResult] | None = None,
    clock: FixedClock | None = None,
) -> tuple[object, FakeModel]:
    model = FakeModel([TOOL_TURN, answer])
    loop = AgentLoop(
        model=model,
        toolbox=FakeMcp(results=results or {ACCOUNT.name: ACCOUNT_RESULT}),
        limits=make_limits(),
        clock=clock or FixedClock(),
    )
    return loop.run("what is my equity?"), model


def test_a_stated_equity_matching_the_account_read_model_is_sourced_with_its_age() -> None:
    outcome, _model = _run(_answer("1000.00"))

    assert outcome.state is SessionState.DONE
    assert outcome.reason is StopReason.COMPLETED
    assert outcome.answer is not None
    number = outcome.answer.numbers[0]
    assert number.value == Decimal("1000.00")
    assert number.tool == ACCOUNT.name
    assert number.path == "equity"
    assert number.age_seconds == 30
    rendered = outcome.answer.render()
    assert "ezchain_get_account.equity" in rendered
    assert "age 30s" in rendered


def test_a_stated_equity_that_differs_from_the_account_read_model_fails() -> None:
    outcome, _model = _run(_answer("1234.56"))

    assert outcome.state is SessionState.FAILED
    assert outcome.reason is StopReason.NUMBER_MISMATCH
    assert outcome.answer is None
    assert "1234.56" in (outcome.detail or "")
    assert "1000.00" in (outcome.detail or "")


def test_a_claim_with_no_tool_result_in_this_session_is_refused() -> None:
    outcome, _model = _run(_answer("1000.00", tool=STATE.name, path="equity"))

    assert outcome.reason is StopReason.UNSOURCED_NUMBER
    assert outcome.state is SessionState.FAILED
    assert STATE.name in (outcome.detail or "")


def test_a_claim_naming_a_path_the_tool_did_not_return_is_refused() -> None:
    outcome, _model = _run(_answer("1000.00", path="positions.SOL"))

    assert outcome.reason is StopReason.UNSOURCED_NUMBER
    assert "positions.SOL" in (outcome.detail or "")


def test_a_number_written_into_the_summary_is_refused() -> None:
    turn = ModelTurn(
        answer=Answer(
            summary="Your equity is 1000.00 today.",
            claims=(
                NumberClaim(
                    label="equity", value=Decimal("1000.00"), tool=ACCOUNT.name, path="equity"
                ),
            ),
        )
    )
    outcome, _model = _run(turn)

    assert outcome.reason is StopReason.UNSOURCED_NUMBER
    assert outcome.state is SessionState.FAILED


def test_a_failed_tool_result_cannot_back_a_claim() -> None:
    failed = ToolResult(
        tool=ACCOUNT.name,
        data={"equity": "1000.00", "code": "API_UNREACHABLE"},
        is_error=True,
    )
    outcome, _model = _run(_answer("1000.00"), results={ACCOUNT.name: failed})

    assert outcome.reason is StopReason.UNSOURCED_NUMBER


def test_age_falls_back_to_receipt_when_the_payload_has_no_as_of() -> None:
    result = ToolResult(tool=ACCOUNT.name, data={"equity": "1000.00"})
    outcome, _model = _run(_answer("1000.00"), results={ACCOUNT.name: result})

    assert outcome.answer is not None
    assert outcome.answer.numbers[0].age_seconds == 0


def test_every_number_in_a_multi_claim_answer_carries_its_own_source() -> None:
    result = ToolResult(
        tool=ACCOUNT.name,
        data={
            "equity": "1000.00",
            "cash": "250.00",
            "as_of": "2026-11-14T09:00:00Z",
        },
    )
    turn = ModelTurn(
        answer=Answer(
            summary="Two readings from the account.",
            claims=(
                NumberClaim(
                    label="equity", value=Decimal("1000.00"), tool=ACCOUNT.name, path="equity"
                ),
                NumberClaim(label="cash", value=Decimal("250.00"), tool=ACCOUNT.name, path="cash"),
            ),
        )
    )
    outcome, _model = _run(turn, results={ACCOUNT.name: result})

    assert outcome.answer is not None
    assert [number.path for number in outcome.answer.numbers] == ["equity", "cash"]
    rendered = outcome.answer.render()
    assert "source ezchain_get_account.cash" in rendered
