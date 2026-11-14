"""No-fake-data answers.

The model does not state numbers: it states *claims* that name a tool and a dotted path
into that tool's recorded result. The runtime resolves each claim against an observation
from this session, refuses a value that differs from what the tool returned, and attaches
the source and the age of the reading. Prose in the summary may not contain digits, so a
fabricated figure cannot hide in a sentence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .session import StopReason


class AnswerError(RuntimeError):
    reason: StopReason

    def __init__(self, message: str) -> None:
        super().__init__(message)


class UnsourcedNumber(AnswerError):
    reason = StopReason.UNSOURCED_NUMBER


class NumberMismatch(AnswerError):
    reason = StopReason.NUMBER_MISMATCH


@dataclass(frozen=True)
class NumberClaim:
    label: str
    value: Decimal
    tool: str
    path: str


@dataclass(frozen=True)
class Answer:
    summary: str
    claims: tuple[NumberClaim, ...] = ()


@dataclass(frozen=True)
class Observation:
    tool: str
    arguments: Mapping[str, Any]
    data: Any
    observed_at: datetime
    is_error: bool = False


@dataclass(frozen=True)
class SourcedNumber:
    label: str
    value: Decimal
    tool: str
    path: str
    observed_at: datetime
    age_seconds: int


@dataclass(frozen=True)
class SourcedAnswer:
    summary: str
    numbers: tuple[SourcedNumber, ...]

    def render(self) -> str:
        lines = [self.summary] if self.summary else []
        for number in self.numbers:
            lines.append(
                f"{number.label}: {number.value} "
                f"(source {number.tool}.{number.path}, age {number.age_seconds}s)"
            )
        return "\n".join(lines)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError:
            return None
    return None


def _resolve(data: Any, path: str) -> Any:
    current = data
    for segment in path.split("."):
        if not segment or not isinstance(current, Mapping) or segment not in current:
            raise UnsourcedNumber(f"no value at {path!r} in the tool result")
        current = current[segment]
    return current


def _observed_at(observation: Observation) -> datetime:
    if isinstance(observation.data, Mapping):
        raw = observation.data.get("as_of")
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                return parsed
    return observation.observed_at


def resolve_answer(
    answer: Answer,
    observations: tuple[Observation, ...],
    now: datetime,
) -> SourcedAnswer:
    if any(character.isdigit() for character in answer.summary):
        raise UnsourcedNumber("the summary contains a digit; state numbers as sourced claims")
    numbers: list[SourcedNumber] = []
    for claim in answer.claims:
        matches = [
            observation
            for observation in observations
            if observation.tool == claim.tool and not observation.is_error
        ]
        if not matches:
            raise UnsourcedNumber(
                f"{claim.label}: no successful {claim.tool!r} result in this session"
            )
        observation = matches[-1]
        observed = _decimal(_resolve(observation.data, claim.path))
        if observed is None:
            raise UnsourcedNumber(f"{claim.label}: {claim.tool}.{claim.path} is not a number")
        if observed != claim.value:
            raise NumberMismatch(
                f"{claim.label}: stated {claim.value} but {claim.tool}.{claim.path} is {observed}"
            )
        observed_at = _observed_at(observation)
        age = max(0, int((now - observed_at).total_seconds()))
        numbers.append(
            SourcedNumber(
                label=claim.label,
                value=observed,
                tool=claim.tool,
                path=claim.path,
                observed_at=observed_at,
                age_seconds=age,
            )
        )
    return SourcedAnswer(summary=answer.summary, numbers=tuple(numbers))


__all__ = [
    "Answer",
    "AnswerError",
    "NumberClaim",
    "NumberMismatch",
    "Observation",
    "SourcedAnswer",
    "SourcedNumber",
    "UnsourcedNumber",
    "resolve_answer",
]
