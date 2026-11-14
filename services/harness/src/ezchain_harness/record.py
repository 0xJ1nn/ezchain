"""The recorded session: which model this session ran, and what each turn cost.

Two things a session must be able to prove afterwards: the exact model id it ran, and that no
credential reached the record. The id is a constructor argument of the record and the provider
clients refuse a record whose id differs from their own, so the pinned id cannot drift away
from the artefact that is written down.

Append-only by interface: ``add_turn`` is the only mutation and there is no update, delete or
re-render path. :meth:`SessionRecord.render` is the JSON a panel or a log line shows, and it is
redacted on the way out — the record never holds a key in the first place, and the renderer
does not rely on that.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .redact import redact

RECORD_SCHEMA = "ezchain.harness.session.v1"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RecordedTurn:
    index: int
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    tool_calls: int
    stop: str
    latency_ms: int


class SessionRecord:
    """One session's pinned model and its turns."""

    __slots__ = ("_model_id", "_provider", "_session_id", "_clock", "_turns", "_started_at")

    def __init__(
        self,
        *,
        session_id: str,
        provider: str,
        model_id: str,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not session_id:
            raise ValueError("a session record needs a session id")
        if not model_id:
            raise ValueError("a session record needs the model id it is pinned to")
        self._session_id = session_id
        self._provider = provider
        self._model_id = model_id
        self._clock = clock
        self._turns: list[RecordedTurn] = []
        self._started_at = clock()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def started_at(self) -> datetime:
        return self._started_at

    @property
    def turns(self) -> tuple[RecordedTurn, ...]:
        return tuple(self._turns)

    @property
    def cost_usd(self) -> Decimal:
        return sum((Decimal(turn.cost_usd) for turn in self._turns), Decimal("0"))

    def add_turn(
        self,
        *,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Decimal,
        tool_calls: int,
        stop: str,
        latency_ms: int,
    ) -> RecordedTurn:
        if model_id != self._model_id:
            raise ValueError(
                f"session {self._session_id} is pinned to {self._model_id!r}, not {model_id!r}"
            )
        turn = RecordedTurn(
            index=len(self._turns),
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=str(cost_usd),
            tool_calls=tool_calls,
            stop=stop,
            latency_ms=latency_ms,
        )
        self._turns.append(turn)
        return turn

    def to_dict(self, *, secrets: Sequence[str] = ()) -> dict[str, Any]:
        def scrub(value: str) -> str:
            return redact(value, secrets=secrets)

        return {
            "schema": RECORD_SCHEMA,
            "session_id": scrub(self._session_id),
            "provider": scrub(self._provider),
            "model_id": scrub(self._model_id),
            "started_at": self._started_at.isoformat(),
            "turns": [
                {
                    "index": turn.index,
                    "model_id": scrub(turn.model_id),
                    "prompt_tokens": turn.prompt_tokens,
                    "completion_tokens": turn.completion_tokens,
                    "cost_usd": turn.cost_usd,
                    "tool_calls": turn.tool_calls,
                    "stop": scrub(turn.stop),
                    "latency_ms": turn.latency_ms,
                }
                for turn in self._turns
            ],
        }

    def render(self, *, secrets: Sequence[str] = ()) -> str:
        return json.dumps(self.to_dict(secrets=secrets), sort_keys=True, indent=2)

    def write(self, path: str | Path, *, secrets: Sequence[str] = ()) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(secrets=secrets) + "\n", encoding="utf-8")
        return target


__all__ = ["RECORD_SCHEMA", "RecordedTurn", "SessionRecord"]
