"""Protection HTTP surface: arm CRUD, cancel, profile resolution (issue #103).

Test-first: the router, service and tables do not exist yet, so these fail at
import. Arming checks the ledger position, so an arm can never protect a
position the account does not hold.
"""

from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session
from ezchain_api.models import LedgerEntryRow
from ezchain_api.routers import protection

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
