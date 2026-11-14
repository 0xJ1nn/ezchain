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

    ``owner`` is the user the principal acts for: the authenticated subject for a person
    or a key (a key id changes when a key is reissued, the subject does not), and the
    creating principal's owner for a strategy order. The automation tier and its consent
    are bound to this value, not to the deployment workspace, so one user's opt-in never
    authorizes another user's automated order.

    ``creator_kind`` is set only for a strategy order: the kind of principal that created
    the strategy. The order stays attributed to the strategy, while the gate can tell
    whether the automation behind it belongs to a person or an agent.
    """

    kind: str
    id: str | None
    owner: str | None = None
    creator_kind: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTOR_KINDS:
            raise ValueError(f"unknown actor kind {self.kind!r}")
        if (self.id is None) != (self.kind == ACTOR_UNKNOWN):
            raise ValueError("only an unknown actor may omit its id")
        if self.owner is not None and not self.owner:
            raise ValueError("an actor owner cannot be empty")
        if self.creator_kind is not None and self.creator_kind not in ACTOR_KINDS:
            raise ValueError(f"unknown creator kind {self.creator_kind!r}")

    @property
    def principal(self) -> str:
        """The user this actor acts for, or ``""`` when nothing names one."""
        return self.owner or self.id or ""


UNKNOWN_ACTOR = OrderActor(ACTOR_UNKNOWN, None)


def from_principal(principal: PrincipalLike | None) -> OrderActor:
    """The actor named by an authenticated caller.

    The deployment token acts as the human operator and is addressed by its subject. A
    scoped key is an agent and is addressed by the key id, so revoking and reissuing a key
    leaves the attribution of past orders intact. A principal with neither an id nor a
    subject cannot be attributed and is unknown rather than named by a placeholder.

    ``owner`` is the principal's subject in both cases: it is the user identity the
    automation tier is bound to, and it does not change when a key is reissued.
    """
    if principal is None:
        return UNKNOWN_ACTOR
    if principal.legacy:
        subject = getattr(principal, "subject", "") or ""
        return OrderActor(ACTOR_HUMAN, subject, owner=subject) if subject else UNKNOWN_ACTOR
    identifier = principal.key_id or principal.subject
    if not identifier:
        return UNKNOWN_ACTOR
    owner = principal.subject or identifier
    return OrderActor(ACTOR_AGENT, identifier, owner=owner)


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
