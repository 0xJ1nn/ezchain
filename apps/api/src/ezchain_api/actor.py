"""Who placed an order.

The actor is never a request field. The API edge resolves it from the authenticated
principal, the order service derives it from the strategy attribution the engine sets,
and anything else is recorded as ``unknown``. An actor that cannot be determined from a
server-side fact is never guessed: a null ``actor_kind`` on a row written before the
columns existed reads as unknown, which is the honest answer for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ACTOR_HUMAN = "human"
ACTOR_AGENT = "agent"
ACTOR_STRATEGY = "strategy"
ACTOR_UNKNOWN = "unknown"
ACTOR_KINDS = frozenset({ACTOR_HUMAN, ACTOR_AGENT, ACTOR_STRATEGY, ACTOR_UNKNOWN})


class PrincipalLike(Protocol):
    """The part of an authenticated principal this module attributes an order to."""

    @property
    def subject(self) -> str: ...

    @property
    def key_id(self) -> str | None: ...

    @property
    def legacy(self) -> bool: ...


@dataclass(frozen=True)
class OrderActor:
    """The placing principal: a kind and the id that kind is addressed by.

    ``id`` is the deployment token's subject for a human operator, the agent key's id for
    a scoped key, and the strategy id for an engine order. It is null exactly when the
    kind is ``unknown``.
    """

    kind: str
    id: str | None

    def __post_init__(self) -> None:
        if self.kind not in ACTOR_KINDS:
            raise ValueError(f"unknown actor kind {self.kind!r}")
        if (self.id is None) != (self.kind == ACTOR_UNKNOWN):
            raise ValueError("only an unknown actor may omit its id")


UNKNOWN_ACTOR = OrderActor(ACTOR_UNKNOWN, None)


def from_principal(principal: PrincipalLike | None) -> OrderActor:
    """The actor named by an authenticated caller.

    The deployment token acts as the human operator and is addressed by its subject. A
    scoped key is an agent and is addressed by the key id, so revoking and reissuing a key
    leaves the attribution of past orders intact. A principal with neither an id nor a
    subject cannot be attributed and is unknown rather than named by a placeholder.
    """
    if principal is None:
        return UNKNOWN_ACTOR
    if principal.legacy:
        subject = getattr(principal, "subject", "") or ""
        return OrderActor(ACTOR_HUMAN, subject) if subject else UNKNOWN_ACTOR
    identifier = principal.key_id or principal.subject
    return OrderActor(ACTOR_AGENT, identifier) if identifier else UNKNOWN_ACTOR


__all__ = [
    "ACTOR_AGENT",
    "ACTOR_HUMAN",
    "ACTOR_KINDS",
    "ACTOR_STRATEGY",
    "ACTOR_UNKNOWN",
    "OrderActor",
    "PrincipalLike",
    "UNKNOWN_ACTOR",
    "from_principal",
]
