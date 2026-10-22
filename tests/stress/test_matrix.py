"""Chain matrix helpers: capability discovery, liquidity ranking, rejection vocabulary."""

from __future__ import annotations

from tools.stress.matrix import (
    STATUS_FAILED,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_REJECTED,
    ChainRow,
    documented_rejection,
    order_outcome,
    paper_chain_ids,
    pick_most_liquid,
    quote_kind_for,
    rejected_skip_status,
    summarize,
)


def chain(
    chain_id: str, *, paper: bool = True, quote_kind: str = "paper_mark"
) -> dict[str, object]:
    return {
        "id": chain_id,
        "capabilities": {
            "browse": True,
            "paper_execute": paper,
            "live_execute": False,
            "quote_kind": quote_kind,
        },
    }


def row(address: str, liquidity: str | None, price: str = "1") -> dict[str, object]:
    return {
        "chain": "base",
        "base_token": address,
        "symbol": "TKN",
        "price_usd": price,
        "liquidity_usd": liquidity,
    }


def test_only_paper_capable_chains_are_listed() -> None:
    chains = [chain("solana", quote_kind="jupiter"), chain("base", paper=False), chain("tonia")]
    assert paper_chain_ids(chains) == ["solana", "tonia"]
    assert quote_kind_for(chains, "solana") == "jupiter"
    assert quote_kind_for(chains, "base") == "paper_mark"
    assert quote_kind_for(chains, "nope") == "unknown"


def test_the_most_liquid_row_wins() -> None:
    rows = [row("0xaaa", "10"), row("0xbbb", "5000"), row("0xccc", "100")]
    chosen = pick_most_liquid(rows)
    assert chosen is not None
    assert chosen["base_token"] == "0xbbb"


def test_unknown_liquidity_never_outranks_a_known_value() -> None:
    rows = [row("0xaaa", None), row("0xbbb", "1")]
    chosen = pick_most_liquid(rows)
    assert chosen is not None
    assert chosen["base_token"] == "0xbbb"


def test_unknown_liquidity_is_still_usable_when_it_is_all_there_is() -> None:
    chosen = pick_most_liquid([row("0xaaa", None)])
    assert chosen is not None
    assert chosen["base_token"] == "0xaaa"


def test_negative_and_malformed_liquidity_are_ignored() -> None:
    rows = [row("0xaaa", "-5"), row("0xbbb", "NaN"), row("0xccc", None)]
    chosen = pick_most_liquid(rows)
    assert chosen is not None
    assert chosen["base_token"] == "0xccc"


def test_ranking_is_reproducible_on_a_tie() -> None:
    first = pick_most_liquid([row("0xaaa", "10"), row("0xbbb", "10")])
    second = pick_most_liquid([row("0xbbb", "10"), row("0xaaa", "10")])
    assert first is not None and second is not None
    assert first["base_token"] == second["base_token"]


def test_rows_without_a_token_are_not_candidates() -> None:
    assert pick_most_liquid([{"chain": "base", "liquidity_usd": "10"}]) is None
    assert pick_most_liquid([]) is None


def test_the_documented_rejection_vocabulary() -> None:
    for reason in (
        "LIQUIDITY_TOO_LOW",
        "PAIR_TOO_YOUNG",
        "PAPER_MARK_LIMIT",
        "QUOTE_STALE",
        "INSUFFICIENT_POSITION",
        "KILL_SWITCH_ACTIVE",
    ):
        assert documented_rejection(reason), reason
    assert documented_rejection("ORDER_LIMIT,PAPER_MARK_LIMIT")
    assert documented_rejection(" ORDER_LIMIT , PAPER_MARK_LIMIT ")
    assert not documented_rejection("SOMETHING_NEW")
    assert not documented_rejection("ORDER_LIMIT,BECAUSE_I_SAID_SO")
    assert not documented_rejection("")
    assert not documented_rejection(None)
    assert not documented_rejection(7)


def test_skip_labels_are_stable() -> None:
    assert rejected_skip_status("ORDER_LIMIT,PAPER_MARK_LIMIT") == (
        "rejected:ORDER_LIMIT,PAPER_MARK_LIMIT"
    )
    assert rejected_skip_status(" ORDER_LIMIT ") == "rejected:ORDER_LIMIT"
    assert rejected_skip_status("") == STATUS_REJECTED


def test_order_outcome_normalises_rows() -> None:
    assert order_outcome({"status": "filled", "reason": None}) == ("filled", "")
    assert order_outcome({"status": "rejected", "reason": "NO_PAIR"}) == ("rejected", "NO_PAIR")
    assert order_outcome(None) == ("missing", "")
    assert order_outcome({"status": None}) == ("unknown", "")


def test_matrix_rows_serialise_and_summarise() -> None:
    rows = [
        ChainRow(chain="base", status=STATUS_OK, token="0xaa", buy="filled", sell="filled"),
        ChainRow(chain="ton", status=STATUS_NO_DATA, reason="no market rows"),
        ChainRow(chain="near", status="rejected:PAIR_TOO_YOUNG", reason="PAIR_TOO_YOUNG"),
        ChainRow(chain="sui", status=STATUS_FAILED, reason="sell did not fill"),
    ]
    assert rows[0].to_dict()["status"] == "ok"
    counts = summarize(rows)
    assert counts == {
        "total": 4,
        "ok": 1,
        "no_data": 1,
        "rejected:PAIR_TOO_YOUNG": 1,
        "failed": 1,
    }
    assert summarize([]) == {"total": 0}
