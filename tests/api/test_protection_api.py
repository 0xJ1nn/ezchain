"""Protection HTTP surface: arm CRUD, cancel, profile resolution (issue #103).

Test-first: the router, service and tables do not exist yet, so these fail at
import. Arming checks the ledger position, so an arm can never protect a
position the account does not hold.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from ezchain_api.models import (
    AppendOnlyViolation,
    LedgerEntryRow,
    ProtectionArmRow,
    ProtectionEventRow,
)
from ezchain_api.routers import protection
from ezchain_api.services import protection as protection_service
from ezchain_api.services.protection import ProtectionServiceError

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"

ARMS = "/api/v1/protection/arms"
PROFILES = "/api/v1/protection/profiles"


def _hold_position(session: Session) -> None:
    from ezchain_core.ledger import position_account

    session.add(
        LedgerEntryRow(
            group_id="g1",
            account=position_account(CHAIN, TOKEN),
            asset=TOKEN,
            amount=Decimal("100"),
            usd_value=Decimal("100"),
            kind="buy",
        )
    )
    session.commit()


def test_arm_crud_and_cancel(client_for, session: Session) -> None:
    _hold_position(session)
    client = client_for(protection.router)

    created = client.post(
        ARMS,
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "token_symbol": "TEST",
            "kind": "stop_loss",
            "params": {"price_usd": "90"},
        },
    )
    assert created.status_code == 201
    arm_id = created.json()["id"]
    assert created.json()["status"] == "armed"
    assert created.json()["mode"] == "paper"

    listed = client.get(ARMS)
    assert any(row["id"] == arm_id for row in listed.json())

    patched = client.patch(f"{ARMS}/{arm_id}", json={"params": {"price_usd": "85"}})
    assert patched.status_code == 200
    assert patched.json()["params"]["price_usd"] == "85"

    cancelled = client.post(f"{ARMS}/{arm_id}/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # A cancelled arm cannot be edited or re-cancelled into a live one.
    assert client.patch(f"{ARMS}/{arm_id}", json={"params": {"price_usd": "1"}}).status_code == 409


def test_arm_without_a_position_fails_closed(client_for, session: Session) -> None:
    client = client_for(protection.router)
    response = client.post(
        ARMS,
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "token_symbol": "TEST",
            "kind": "stop_loss",
            "params": {"price_usd": "1"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NO_POSITION"


def test_profiles_global_vs_per_chain(client_for, session: Session) -> None:
    client = client_for(protection.router)

    smart = client.post(
        PROFILES,
        json={
            "name": "Guarded",
            "chain": None,
            "kind": "smart",
            "params": {"stop_loss_pct": "8", "trailing_pct": "5"},
            "rules": ["Stop out at 8% below entry.", "Trail 5% below the highest price."],
        },
    )
    assert smart.status_code == 201
    assert smart.json()["kind"] == "smart"
    assert "Stop out" in smart.json()["rules"][0]

    manual = client.post(
        PROFILES,
        json={
            "name": "Tight",
            "chain": CHAIN,
            "kind": "manual",
            "params": {"stop_loss_pct": "5"},
        },
    )
    assert manual.status_code == 201

    effective = client.get(f"{PROFILES}/effective", params={"chain": CHAIN})
    assert effective.status_code == 200
    # The per-chain profile overrides the global one entirely.
    assert effective.json()["profile"]["name"] == "Tight"
    assert effective.json()["profile"]["stop_loss_pct"] == "5"

    other = client.get(f"{PROFILES}/effective", params={"chain": "base"})
    assert other.json()["profile"]["name"] == "Guarded"

    # A smart profile without inspectable rules is rejected, never magic.
    rejected = client.post(
        PROFILES,
        json={"name": "Magic", "chain": None, "kind": "smart", "params": {"stop_loss_pct": "3"}},
    )
    assert rejected.status_code == 422


def _create_arm(client, *, price: str = "90") -> dict:
    created = client.post(
        ARMS,
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "token_symbol": "TEST",
            "kind": "stop_loss",
            "params": {"price_usd": price},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_version_conflict_returns_409(client_for, session: Session) -> None:
    _hold_position(session)
    client = client_for(protection.router)
    arm = _create_arm(client)
    assert arm["version"] == 1

    first = client.patch(
        f"{ARMS}/{arm['id']}",
        json={"params": {"price_usd": "85"}, "expected_version": 1},
    )
    assert first.status_code == 200
    assert first.json()["version"] == 2

    # A stale writer loses; its payload is not applied.
    stale = client.patch(
        f"{ARMS}/{arm['id']}",
        json={"params": {"price_usd": "10"}, "expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PROTECTION_CONFLICT"

    # If-Match carries the same precondition.
    current = client.patch(
        f"{ARMS}/{arm['id']}",
        json={"params": {"price_usd": "80"}},
        headers={"If-Match": '"2"'},
    )
    assert current.status_code == 200
    assert current.json()["version"] == 3

    # Clearing is guarded by the same version.
    assert (
        client.post(f"{ARMS}/{arm['id']}/cancel", json={"expected_version": 2}).status_code == 409
    )
    cancelled = client.post(f"{ARMS}/{arm['id']}/cancel", json={"expected_version": 3})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["version"] == 4

    # A malformed If-Match is refused, never silently ignored.
    malformed = client.patch(
        f"{ARMS}/{arm['id']}", json={"params": {"price_usd": "1"}}, headers={"If-Match": "abc"}
    )
    assert malformed.status_code == 400
    assert malformed.json()["detail"]["code"] == "INVALID_PRECONDITION"


def test_a_stale_reader_cannot_overwrite_a_newer_version(
    client_for, session: Session, engine
) -> None:
    """The version check is a conditional update, not read-check-write."""
    _hold_position(session)
    client = client_for(protection.router)
    arm = _create_arm(client)

    # This session loads version 1 and keeps that snapshot while a second
    # writer commits version 2. It is the same stale identity map a second API
    # worker would hold between its read and its write.
    with Session(engine) as stale:
        stale_arm = stale.get(ProtectionArmRow, arm["id"])
        assert stale_arm is not None
        assert stale_arm.version == 1
        won = client.patch(
            f"{ARMS}/{arm['id']}",
            json={"params": {"price_usd": "85"}, "expected_version": 1},
        )
        assert won.status_code == 200
        assert won.json()["version"] == 2

        with pytest.raises(ProtectionServiceError) as caught:
            protection_service.patch_arm(stale, arm["id"], {"price_usd": "10"}, expected_version=1)
    assert caught.value.code == "PROTECTION_CONFLICT"
    current = session.get(ProtectionArmRow, arm["id"])
    assert current is not None
    assert current.version == 2
    assert current.params == {"price_usd": "85"}


def test_event_trail_is_ordered_and_append_only(client_for, session: Session) -> None:
    _hold_position(session)
    client = client_for(protection.router)
    arm = _create_arm(client)
    patched = client.patch(f"{ARMS}/{arm['id']}", json={"params": {"price_usd": "85"}})
    assert patched.status_code == 200
    assert client.post(f"{ARMS}/{arm['id']}/cancel", json={}).status_code == 200

    trail = client.get(f"{ARMS}/{arm['id']}/events")
    assert trail.status_code == 200
    events = trail.json()
    # Newest first, by the per-arm sequence, not by wall-clock time.
    assert [event["action"] for event in events] == ["cancelled", "updated", "armed"]
    assert [event["seq"] for event in events] == [3, 2, 1]
    assert events[0]["rule_snapshot"] == {"price_usd": "85"}
    assert events[1]["rule_snapshot"] == {
        "before": {"price_usd": "90"},
        "after": {"price_usd": "85"},
    }
    assert events[2]["rule_snapshot"] == {"price_usd": "90"}

    newest = client.get(f"{ARMS}/{arm['id']}/events", params={"limit": 1}).json()
    assert [event["seq"] for event in newest] == [3]

    # The audit trail has no HTTP mutation path.
    assert client.delete(f"{ARMS}/{arm['id']}/events").status_code == 405
    assert client.put(f"{ARMS}/{arm['id']}/events").status_code == 405

    # Nor an ORM one: update and delete both fail closed.
    row = session.get(ProtectionEventRow, events[0]["id"])
    assert row is not None
    row.reason = "tampered"
    with pytest.raises(AppendOnlyViolation):
        session.commit()
    session.rollback()

    row = session.get(ProtectionEventRow, events[0]["id"])
    assert row is not None
    session.delete(row)
    with pytest.raises(AppendOnlyViolation):
        session.commit()
    session.rollback()

    unchanged = session.get(ProtectionEventRow, events[0]["id"])
    assert unchanged is not None
    assert unchanged.reason is None
    assert unchanged.action == "cancelled"


def test_bulk_updates_and_deletes_cannot_touch_the_trail(client_for, session: Session) -> None:
    """Bulk statements do not populate dirty/deleted; the statement itself is stopped."""
    _hold_position(session)
    client = client_for(protection.router)
    arm = _create_arm(client)

    with pytest.raises(AppendOnlyViolation):
        session.execute(update(ProtectionEventRow).values(reason="tampered"))
    session.rollback()

    with pytest.raises(AppendOnlyViolation):
        session.execute(delete(ProtectionEventRow))
    session.rollback()

    trail = client.get(f"{ARMS}/{arm['id']}/events").json()
    assert [(event["action"], event["reason"]) for event in trail] == [("armed", None)]


def test_an_update_event_carries_the_rule_it_replaced(client_for, session: Session) -> None:
    """The trail can reconstruct the previous rule, not only the new one."""
    _hold_position(session)
    client = client_for(protection.router)
    arm = _create_arm(client, price="90")

    patched = client.patch(f"{ARMS}/{arm['id']}", json={"params": {"price_usd": "85"}})
    assert patched.status_code == 200

    updated = next(
        event
        for event in client.get(f"{ARMS}/{arm['id']}/events").json()
        if event["action"] == "updated"
    )
    assert updated["rule_snapshot"]["before"] == {"price_usd": "90"}
    assert updated["rule_snapshot"]["after"] == {"price_usd": "85"}
