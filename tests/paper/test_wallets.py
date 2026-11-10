"""Paper wallets: identity per chain, settlement attribution and ledger-derived balances."""

from __future__ import annotations

import random
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow, PaperWalletRow
from ezchain_api.services import paper
from ezchain_core.ledger import buy_entries, cash_account

from .conftest import (
    CHAIN,
    TOKEN,
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)

STARTING_CASH = Decimal("10000")


def _wallets(session: Session) -> dict[str, dict[str, object]]:
    return {str(row["chain"]): row for row in paper.wallets(session)}


def _cash_sum(session: Session) -> Decimal:
    return sum(
        (
            Decimal(str(row.usd_value))
            for row in session.scalars(
                select(LedgerEntryRow).where(LedgerEntryRow.account == cash_account())
            )
        ),
        Decimal(0),
    )


def _wallet_cash_sum(session: Session, wallet_id: str) -> Decimal:
    rows = session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.account == cash_account()))
    return sum(
        (
            Decimal(str(row.usd_value))
            for row in rows
            if row.wallet_id == wallet_id
            or (row.wallet_id is None and wallet_id == paper.SETTLEMENT_WALLET_ID)
        ),
        Decimal(0),
    )


def test_genesis_creates_the_settlement_wallet_with_the_starting_cash(session: Session) -> None:
    """The genesis journal is booked once, into a wallet that owns the starting cash."""
    paper.ensure_genesis(session, STARTING_CASH)

    wallets = _wallets(session)
    settlement = wallets["settlement"]
    assert settlement["asset"] == "USD"
    assert settlement["balance_usd"] == "10000"

    # One wallet per chain, each starting empty: a wallet declares where cash may settle,
    # it does not mint its own.
    chain_wallets = {chain for chain in wallets if chain != "settlement"}
    assert CHAIN in chain_wallets
    assert all(Decimal(str(wallets[chain]["balance_usd"])) == 0 for chain in chain_wallets)
    assert sum(
        (Decimal(str(row["balance_usd"])) for row in wallets.values()), Decimal(0)
    ) == _cash_sum(session)


def test_buy_settles_to_the_wallet_for_its_chain(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))

    order = place(session, settings, dex, jupiter, make_request(amount_usd="200", key="buy-wallet"))

    assert order.status == "filled"
    wallets = _wallets(session)
    blast = wallets[CHAIN]
    # 200 notional plus the 30 bps paper fee leave the chain's wallet.
    assert Decimal(str(blast["balance_usd"])) == Decimal("-200.6")
    # The genesis wallet funded no trade itself.
    assert wallets["settlement"]["balance_usd"] == "10000"

    ledger_wallet_ids = {
        row.wallet_id
        for row in session.scalars(
            select(LedgerEntryRow).where(
                LedgerEntryRow.group_id == order.id,
                LedgerEntryRow.account == cash_account(),
            )
        )
    }
    assert ledger_wallet_ids == {blast["id"]}


def test_sell_settles_proceeds_back_to_the_chain_wallet(session, settings, dex, jupiter) -> None:
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    place(session, settings, dex, jupiter, make_request(amount_usd="200", key="buy-before-sell"))

    dex.set(make_snapshot(price=Decimal("2500"), liquidity=Decimal("500000")))
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="sell-wallet"),
    )

    assert sell.status == "filled"
    wallets = _wallets(session)
    # -200.6 on the buy, +100 proceeds, -0.3 fee on the sell.
    assert Decimal(str(wallets[CHAIN]["balance_usd"])) == Decimal("-100.9")
    assert wallets["settlement"]["balance_usd"] == "10000"
    assert (
        sum((Decimal(str(row["balance_usd"])) for row in wallets.values()), Decimal(0))
        == _cash_sum(session)
        == Decimal("9899.1")
    )


def test_unknown_chain_fails_closed_with_a_typed_reason(session, settings, dex, jupiter) -> None:
    """A chain with no wallet is refused before a quote, a fill or a journal row."""
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(chain="nowhere", key="unknown-chain-wallet"),
    )

    assert order.status == "rejected"
    assert order.reason == "WALLET_UNKNOWN"
    assert order.reasons is not None
    assert order.reasons[0]["code"] == "WALLET_UNKNOWN"
    assert order.reasons[0]["detail"] == {"chain": "nowhere"}
    assert dex.calls == 0
    assert session.scalar(select(PaperFillRow)) is None
    assert session.scalar(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)) is None


def test_unknown_settlement_asset_fails_closed(session, settings, dex, jupiter) -> None:
    """A wallet that does not settle in the ledger asset cannot receive its cash legs."""
    paper.ensure_genesis(session, STARTING_CASH)
    wallet = session.scalar(select(PaperWalletRow).where(PaperWalletRow.chain == CHAIN))
    assert wallet is not None
    wallet.asset = "EUR"
    session.commit()

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="25", key="unknown-asset-wallet"),
    )

    assert order.status == "rejected"
    assert order.reason == "SETTLEMENT_ASSET_UNKNOWN"
    assert order.reasons is not None
    assert order.reasons[0]["detail"] == {"chain": CHAIN, "asset": "EUR"}
    # The refusal happens before the quote: no market read, no fill, no journal.
    assert dex.calls == 0
    assert session.scalar(select(PaperFillRow)) is None
    assert session.scalar(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)) is None


def _orphaned_position(session: Session, monkeypatch) -> None:
    """Open a held position, then remove its chain's wallet declaration.

    ``ensure_wallets`` heals a declared chain, so a held position's chain can have no
    wallet row only when the deployment stopped declaring it (or a journal was restored
    without its wallets). The journal below carries the cash attribution the wallet had
    before it was deleted, so the read model has something to report as unallocated.
    """
    paper.ensure_genesis(session, STARTING_CASH)
    for entry in buy_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=Decimal("50"),
        price_usd=Decimal("2"),
        fee_bps=30,
    ):
        row = entry.to_row("seeded-position")
        if row["account"] == cash_account():
            row["wallet_id"] = f"{paper.WALLET_PREFIX}{CHAIN}"
        session.add(LedgerEntryRow(**row))
    session.commit()

    wallet = session.scalar(select(PaperWalletRow).where(PaperWalletRow.chain == CHAIN))
    assert wallet is not None
    session.delete(wallet)
    session.commit()
    monkeypatch.setattr(
        paper,
        "_seeded_wallets",
        lambda: [
            (
                paper.SETTLEMENT_WALLET_ID,
                paper.SETTLEMENT_CHAIN,
                paper.SETTLEMENT_ASSET,
                "Paper settlement",
            )
        ],
    )


@pytest.mark.parametrize("reduce_only", [True, False], ids=["reduce-only", "held-position"])
def test_a_sell_survives_a_chain_with_no_declared_wallet(
    session, settings, dex, jupiter, monkeypatch, reduce_only: bool
) -> None:
    """A missing wallet row is an entry condition and cannot trap the exit it left behind.

    The position is sold with the flag and, separately, as a plain sell sized to the whole
    position. Both are exits, both settle to the settlement wallet, and an entry on the
    undeclared chain is still refused with the truthful reason the client needs.
    """
    _orphaned_position(session, monkeypatch)
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell", amount_usd="100", key="exit-no-declared-wallet", reduce_only=reduce_only
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")
    cash_rows = list(
        session.scalars(
            select(LedgerEntryRow).where(
                LedgerEntryRow.group_id == sell.id,
                LedgerEntryRow.account == cash_account(),
            )
        )
    )
    assert {row.wallet_id for row in cash_rows} == {paper.SETTLEMENT_WALLET_ID}

    entry = place(
        session, settings, dex, jupiter, make_request(amount_usd="25", key="entry-undeclared")
    )
    assert entry.status == "rejected"
    assert entry.reason == "WALLET_UNKNOWN"
    assert entry.reasons is not None
    assert entry.reasons[0]["detail"] == {"chain": CHAIN}


def test_a_sell_settles_to_the_settlement_wallet_when_the_chain_wallet_cannot_hold_usd(
    session, settings, dex, jupiter
) -> None:
    """A wrong-asset wallet is an entry refusal, never a trapped exit.

    The chain wallet declares another settlement asset, so it cannot receive USD; the
    exit still fills and its cash falls back to the settlement wallet, while an entry is
    still refused with the truthful reason the client needs.
    """
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    assert (
        place(
            session, settings, dex, jupiter, make_request(amount_usd="100", key="asset-buy")
        ).status
        == "filled"
    )
    wallet = session.scalar(select(PaperWalletRow).where(PaperWalletRow.chain == CHAIN))
    assert wallet is not None
    wallet.asset = "EUR"
    session.commit()

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="sell", amount_usd="100", key="asset-exit", reduce_only=True),
    )

    assert sell.status == "filled"
    cash_rows = list(
        session.scalars(
            select(LedgerEntryRow).where(
                LedgerEntryRow.group_id == sell.id,
                LedgerEntryRow.account == cash_account(),
            )
        )
    )
    assert {row.wallet_id for row in cash_rows} == {paper.SETTLEMENT_WALLET_ID}

    entry = place(session, settings, dex, jupiter, make_request(amount_usd="25", key="asset-entry"))
    assert entry.status == "rejected"
    assert entry.reason == "SETTLEMENT_ASSET_UNKNOWN"
    assert entry.reasons is not None
    assert entry.reasons[0]["detail"] == {"chain": CHAIN, "asset": "EUR"}


def test_the_no_risk_funding_leg_is_settlement_capital(session, dex, jupiter) -> None:
    """The funded top-up says where it went: the settlement wallet, not nowhere.

    A no-risk shortfall is minted into the cash account as settlement capital, so its
    cash leg names the settlement wallet. The ``funding:USD`` contra leg is not a cash
    account and carries no wallet id.
    """
    funded = make_settings(paper_no_risk=True, paper_starting_cash_usd=Decimal("10"))
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))

    order = place(
        session, funded, dex, jupiter, make_request(amount_usd="100", key="funded-wallet")
    )

    assert order.status == "filled"
    rows = list(
        session.scalars(
            select(LedgerEntryRow).where(LedgerEntryRow.group_id == f"funding:{order.id}")
        )
    )
    by_account = {row.account: row for row in rows}
    assert by_account[cash_account()].wallet_id == paper.SETTLEMENT_WALLET_ID
    assert by_account["funding:USD"].wallet_id is None


def test_wallet_list_adds_the_unallocated_bucket_so_the_rows_sum_to_cash(
    session, settings, dex, jupiter
) -> None:
    """Cash attributed to a deleted wallet stays visible and keeps the sum complete."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    assert (
        place(
            session, settings, dex, jupiter, make_request(amount_usd="100", key="bucket-buy")
        ).status
        == "filled"
    )
    wallet = session.scalar(select(PaperWalletRow).where(PaperWalletRow.chain == CHAIN))
    assert wallet is not None
    session.delete(wallet)
    session.commit()

    rows = paper.wallets(session)
    bucket = next(row for row in rows if row["chain"] == "unallocated")
    assert bucket["id"] is None
    assert bucket["asset"] == "USD"
    assert bucket["label"] == "Unallocated paper cash"
    assert Decimal(str(bucket["balance_usd"])) == Decimal("-100.3")
    assert sum((Decimal(str(row["balance_usd"])) for row in rows), Decimal(0)) == Decimal(
        paper.account_snapshot(session)["cash_usd"]
    )


def test_wallet_balances_equal_their_ledger_sums(session, settings, dex, jupiter) -> None:
    """Every wallet balance is the sum of its cash rows, and the wallets add up to cash."""
    dex.set(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="wallet-sum-blast"))
    dex.set(
        make_snapshot(
            price=Decimal("2000"),
            liquidity=Decimal("500000"),
            chain="solana",
            address=TOKEN,
        )
    )
    place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="50", chain="solana", key="wallet-sum-solana"),
    )

    # A row written before wallets existed carries no attribution; it belongs to the
    # genesis wallet, exactly where the starting cash was booked.
    session.add(
        LedgerEntryRow(
            group_id="legacy-row",
            account=cash_account(),
            asset="USD",
            amount=Decimal("5"),
            usd_value=Decimal("5"),
            kind="legacy",
        )
    )
    session.commit()

    wallets = _wallets(session)
    assert Decimal(str(wallets["settlement"]["balance_usd"])) == Decimal("10005")
    assert Decimal(str(wallets[CHAIN]["balance_usd"])) == Decimal("-100.3")
    assert Decimal(str(wallets["solana"]["balance_usd"])) == Decimal("-50.15")

    for wallet in wallets.values():
        assert Decimal(str(wallet["balance_usd"])) == _wallet_cash_sum(session, str(wallet["id"]))
    assert sum(
        (Decimal(str(row["balance_usd"])) for row in wallets.values()), Decimal(0)
    ) == _cash_sum(session)


def test_wallets_are_seeded_once_and_never_recreated(session: Session) -> None:
    paper.ensure_wallets(session)
    before = session.scalar(select(PaperWalletRow.id).where(PaperWalletRow.chain == CHAIN))
    assert before is not None

    paper.ensure_wallets(session)
    after = session.scalars(select(PaperWalletRow.id).where(PaperWalletRow.chain == CHAIN)).all()
    assert after == [before]


def test_a_rejected_chain_keeps_the_order_and_its_reason(session, settings, dex, jupiter) -> None:
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(chain="nowhere", key="unknown-chain-persisted"),
    )
    row = session.scalar(select(PaperOrderRow).where(PaperOrderRow.id == order.id))
    assert row is not None
    assert row.status == "rejected"
    assert row.reason == "WALLET_UNKNOWN"


@pytest.mark.parametrize("seed", [11, 23])
def test_wallet_balances_are_the_cash_replay_on_a_random_walk(
    session: Session, settings, seed: int
) -> None:
    """Every wallet balance sums to the exact Decimal cash the account reports.

    SQLite stores money as TEXT, so a SQL ``SUM()`` would coerce the rows to REAL and the
    wallets would drift from the account around the 13th decimal place. The property is
    exact equality after every step: one replay, one accountant (CONTRACT I1).
    """
    rng = random.Random(seed)
    price = Decimal("10")
    dex = FakeDexScreener(make_snapshot(price=price, liquidity=Decimal("1000000")))
    jupiter = FakeJupiter(price=price)

    for step in range(120):
        drift = Decimal(str(rng.uniform(-0.04, 0.04)))
        price = max(Decimal("0.5"), (price * (Decimal(1) + drift)).quantize(Decimal("0.0001")))
        dex.set(make_snapshot(price=price, liquidity=Decimal("1000000")))
        side = "buy" if rng.random() < 0.6 else "sell"
        amount = Decimal(rng.randint(5, 200))
        place(
            session,
            settings,
            dex,
            jupiter,
            make_request(side=side, amount_usd=str(amount), key=f"wallet-walk-{seed}-{step}"),
        )

        replay = paper.project_ledger(paper.ledger_rows(session))
        wallet_rows = paper.wallets(session)
        # SQLite keeps unrounded money strings, so an exact sum can exceed the default
        # 28-digit context. The property is exact equality in the same precision the
        # replay uses, never a tolerance.
        with localcontext() as context:
            context.prec = paper.LEDGER_PRECISION
            assert (
                sum((Decimal(str(row["balance_usd"])) for row in wallet_rows), Decimal(0))
                == replay.cash
            ), f"step {step}: wallets do not add up to cash"

            expected: dict[str, Decimal] = {}
            for row in paper.ledger_rows(session):
                if row.account != cash_account():
                    continue
                wallet_id = row.wallet_id or paper.SETTLEMENT_WALLET_ID
                expected[wallet_id] = expected.get(wallet_id, Decimal(0)) + row.usd_value
            assert sum(expected.values(), Decimal(0)) == replay.cash
            for row in wallet_rows:
                wallet_id = str(row["id"])
                assert Decimal(str(row["balance_usd"])) == expected.get(wallet_id, Decimal(0)), (
                    f"step {step}: {wallet_id}"
                )

        assert Decimal(paper.account_snapshot(session)["cash_usd"]) == replay.cash


def test_unallocated_row_keeps_cash_and_positions_without_a_wallet(
    session, settings, dex, jupiter
) -> None:
    """Cash and positions whose chain lost its wallet row stay visible in ``by_wallet``."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    paper.ensure_genesis(session, STARTING_CASH)
    place(session, settings, dex, jupiter, make_request(amount_usd="100", key="orphan-wallet"))

    wallet = session.scalar(select(PaperWalletRow).where(PaperWalletRow.chain == CHAIN))
    assert wallet is not None
    session.delete(wallet)
    session.commit()

    account = paper.account_snapshot(session)
    by_wallet = {row["chain"]: row for row in account["by_wallet"]}
    assert CHAIN not in by_wallet
    orphan = by_wallet["unallocated"]
    assert orphan["wallet_id"] is None
    # 100 notional plus the 30 bps fee left the chain's wallet, and its position is intact.
    assert Decimal(orphan["settled_usd"]) == Decimal("-100.3")
    assert Decimal(orphan["positions_usd"]) == Decimal("100")
    assert Decimal(orphan["market_value_usd"]) == Decimal("-0.3")
    assert orphan["mark_status"] == "known"

    # The decomposition still reconciles with the account: nothing vanished.
    assert sum(
        (Decimal(row["settled_usd"]) for row in account["by_wallet"]), Decimal(0)
    ) == Decimal(account["cash_usd"])
    assert sum((Decimal(row["market_value_usd"]) for row in account["by_wallet"]), Decimal(0)) == (
        Decimal(account["equity_usd"])
    )
