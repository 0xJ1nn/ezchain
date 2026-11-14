"""Headless entry point for the agent runtime: one session from a terminal.

The command builds the real :class:`~ezchain_agent_runtime.AgentLoop` — the same class, the
same budgets, the same kill switch and the same approval behaviour the panel drives — and
prints each step as the loop takes it. It implements no loop of its own and decides nothing
about an order: a result that waits for a person moves the session to ``awaiting_approval``
inside the loop, and the command only reports it and leaves with a non-zero code.

Both seams the loop takes are supplied explicitly, as ``module:attribute`` factories, so the
command needs no provider SDK of its own: ``--model`` returns a ``ModelClient`` and
``--toolbox`` an ``McpToolbox``.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from typing import Any, TextIO, cast
from uuid import uuid4

from ezchain_agent_runtime import (
    AgentLoop,
    BudgetLimits,
    Clock,
    KillSwitch,
    McpToolbox,
    ModelClient,
    RuntimeConfig,
    SessionOutcome,
    SessionState,
    utcnow,
)

from .trace import Trace, TracedModel, TracedToolbox


class ExitCode(IntEnum):
    """The command's whole contract with a caller.

    ``COMPLETED`` is the only success. A session that stopped (kill switch or budget), one
    that failed, and one that is waiting for a person are three different non-zero codes so
    a script can tell them apart without parsing the trace.
    """

    COMPLETED = 0
    FAILED = 1
    USAGE = 2
    STOPPED = 3
    AWAITING_APPROVAL = 4


DEFAULT_LIMITS = BudgetLimits(
    session_turns=8,
    session_tokens=10_000,
    session_cost_usd=Decimal("1"),
    daily_turns=50,
    daily_tokens=100_000,
    daily_cost_usd=Decimal("10"),
)

_OUTCOMES: dict[SessionState, ExitCode] = {
    SessionState.DONE: ExitCode.COMPLETED,
    SessionState.FAILED: ExitCode.FAILED,
    SessionState.STOPPED: ExitCode.STOPPED,
    SessionState.AWAITING_APPROVAL: ExitCode.AWAITING_APPROVAL,
}


class ConfigError(ValueError):
    """The command cannot start: a bad flag, an unimportable factory or an empty prompt."""


def exit_code(outcome: SessionOutcome) -> ExitCode:
    try:
        return _OUTCOMES[outcome.state]
    except KeyError:
        raise ValueError(f"the loop returned a non-terminal state: {outcome.state.value}") from None


def _session_id(trace: Trace) -> Callable[[], str]:
    def mint() -> str:
        session_id = uuid4().hex
        trace.line(f"session {session_id}")
        return session_id

    return mint


@dataclass(frozen=True)
class RunResult:
    outcome: SessionOutcome
    exit_code: ExitCode


def run_session(
    *,
    model: ModelClient,
    toolbox: McpToolbox,
    prompt: str,
    limits: BudgetLimits = DEFAULT_LIMITS,
    kill_switch: KillSwitch | None = None,
    clock: Clock = utcnow,
    out: TextIO | None = None,
) -> RunResult:
    """Run one session on the real loop and print its trace to ``out``."""
    trace = Trace(sys.stdout if out is None else out)
    loop = AgentLoop(
        model=TracedModel(model, trace),
        toolbox=TracedToolbox(toolbox, trace),
        limits=limits,
        kill_switch=kill_switch,
        clock=clock,
        config=RuntimeConfig(session_id_factory=_session_id(trace)),
    )
    outcome = loop.run(prompt)
    if outcome.answer is not None:
        trace.answer(outcome.answer)
    trace.end(outcome)
    return RunResult(outcome=outcome, exit_code=exit_code(outcome))


def _factory(spec: str) -> Callable[[], Any]:
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ConfigError(f"--model/--toolbox take module:attribute, not {spec!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import {module_name}: {exc}") from exc
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ConfigError(f"{spec} is not a zero-argument factory")
    return cast(Callable[[], Any], factory)


def _built(spec: str) -> Any:
    factory = _factory(spec)
    try:
        return factory()
    except Exception as exc:
        # A factory that cannot build its client has not started a session: the command
        # reports it as unusable configuration instead of a traceback and a half trace.
        raise ConfigError(f"{spec} failed: {type(exc).__name__}: {exc}") from exc


def load_model(spec: str) -> ModelClient:
    client = _built(spec)
    if not callable(getattr(client, "complete", None)):
        raise ConfigError(f"{spec} returned no model client (no complete method)")
    return cast(ModelClient, client)


def load_toolbox(spec: str) -> McpToolbox:
    toolbox = _built(spec)
    for method in ("list_tools", "call_tool"):
        if not callable(getattr(toolbox, method, None)):
            raise ConfigError(f"{spec} returned no MCP toolbox (no {method} method)")
    return cast(McpToolbox, toolbox)


def _decimal(flag: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{flag} must be a decimal") from exc
    return value


def build_limits(args: argparse.Namespace) -> BudgetLimits:
    try:
        return BudgetLimits(
            session_turns=int(args.session_turns),
            session_tokens=int(args.session_tokens),
            session_cost_usd=_decimal("--session-cost-usd", str(args.session_cost_usd)),
            daily_turns=int(args.daily_turns),
            daily_tokens=int(args.daily_tokens),
            daily_cost_usd=_decimal("--daily-cost-usd", str(args.daily_cost_usd)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def resolve_prompt(raw: str | None) -> str:
    if raw is None:
        try:
            raw = sys.stdin.read()
        except OSError as exc:
            raise ConfigError("no prompt: pass --prompt or write one to stdin") from exc
    if not raw.strip():
        raise ConfigError("no prompt: pass --prompt or write one to stdin")
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezchain-harness",
        description=(
            "Run one ezCHAIN agent session on the real AgentLoop and print each step. "
            "Exits 0 completed, 1 failed, 2 usage, 3 stopped, 4 awaiting approval."
        ),
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="the session prompt (default: read stdin)",
    )
    parser.add_argument(
        "--model",
        required=True,
        metavar="MODULE:ATTRIBUTE",
        help="zero-argument factory returning an ezchain_agent_runtime ModelClient",
    )
    parser.add_argument(
        "--toolbox",
        required=True,
        metavar="MODULE:ATTRIBUTE",
        help="zero-argument factory returning an ezchain_agent_runtime McpToolbox",
    )
    parser.add_argument(
        "--session-turns", type=int, default=DEFAULT_LIMITS.session_turns, metavar="N"
    )
    parser.add_argument(
        "--session-tokens", type=int, default=DEFAULT_LIMITS.session_tokens, metavar="N"
    )
    parser.add_argument(
        "--session-cost-usd", default=str(DEFAULT_LIMITS.session_cost_usd), metavar="DECIMAL"
    )
    parser.add_argument("--daily-turns", type=int, default=DEFAULT_LIMITS.daily_turns, metavar="N")
    parser.add_argument(
        "--daily-tokens", type=int, default=DEFAULT_LIMITS.daily_tokens, metavar="N"
    )
    parser.add_argument(
        "--daily-cost-usd", default=str(DEFAULT_LIMITS.daily_cost_usd), metavar="DECIMAL"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    errors = sys.stderr if stderr is None else stderr
    args = build_parser().parse_args(argv)
    try:
        limits = build_limits(args)
        prompt = resolve_prompt(cast(str | None, args.prompt))
        model = load_model(cast(str, args.model))
        toolbox = load_toolbox(cast(str, args.toolbox))
    except ConfigError as exc:
        print(f"ezchain-harness: {exc}", file=errors)
        return ExitCode.USAGE
    result = run_session(model=model, toolbox=toolbox, prompt=prompt, limits=limits, out=stdout)
    return result.exit_code


__all__ = [
    "DEFAULT_LIMITS",
    "ConfigError",
    "ExitCode",
    "RunResult",
    "build_limits",
    "build_parser",
    "exit_code",
    "load_model",
    "load_toolbox",
    "main",
    "resolve_prompt",
    "run_session",
]


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
