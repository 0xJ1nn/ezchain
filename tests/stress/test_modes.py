"""Mode wiring: the new modes, ``--mode all``, ``--strategy-kind all`` and sell sizing.

No network: these are the pure decisions the scenarios make before a request is sent.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from tools.stress.cli import _parser, build_config
from tools.stress.config import MODES, STRATEGY_KINDS, default_duration
from tools.stress.scenarios import (
    ALL_MODE_SEQUENCE,
    STRATEGY_KIND_SEQUENCE,
    LedgerWindow,
    _dispatch,
    _mode_sequence,
    _note_strategy_order_ids,
    _Observation,
    _sell_full_quantity_amount,
    strategy_kinds,
)


def config(*argv: str) -> object:
    return build_config(_parser().parse_args(list(argv)))


def test_every_mode_is_accepted() -> None:
    assert MODES == (
        "smoke",
        "soak",
        "idempotency",
        "chaos",
        "matrix",
        "fuzz",
        "race",
        "killswitch",
        "all",
    )
    for mode in MODES:
        parsed = config("--mode", mode)
        assert parsed.mode == mode


def test_all_is_the_documented_sequence() -> None:
    assert ALL_MODE_SEQUENCE == ("smoke", "idempotency", "fuzz", "race", "killswitch", "matrix")
    for mode in ("soak", "chaos"):
        assert mode not in ALL_MODE_SEQUENCE, "soak and chaos stay explicit"
    assert _mode_sequence("all") == ALL_MODE_SEQUENCE
    assert _mode_sequence("fuzz") == ("fuzz",)


@pytest.mark.parametrize("mode", ["all", "matrix", "fuzz", "race", "killswitch"])
def test_new_modes_default_to_thirty_seconds(mode: str) -> None:
    assert default_duration(mode) == 30.0


def test_strategy_kind_all_covers_every_rule_kind() -> None:
    assert STRATEGY_KINDS == ("momentum", "dip", "dca", "threshold", "all")
    assert STRATEGY_KIND_SEQUENCE == ("momentum", "dip", "dca", "threshold")
    all_harness = SimpleNamespace(config=SimpleNamespace(strategy_kind="all"))
    assert strategy_kinds(all_harness) == STRATEGY_KIND_SEQUENCE  # type: ignore[arg-type]
    dip_harness = SimpleNamespace(config=SimpleNamespace(strategy_kind="dip"))
    assert strategy_kinds(dip_harness) == ("dip",)  # type: ignore[arg-type]


def test_an_unknown_mode_is_refused_before_any_request() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["--mode", "teleport"])


def test_dispatch_refuses_an_unwired_mode() -> None:
    with pytest.raises(ValueError):
        _dispatch(object(), object(), "nope")  # type: ignore[arg-type]


def test_sell_sizing_rounds_the_notional_up_to_close_the_position() -> None:
    # amount_usd is quantized to cents by PaperOrderIn: rounding down leaves dust.
    position = {"quantity": "2", "market_value_usd": "3.004", "mark_usd": "1.502"}
    assert _sell_full_quantity_amount(position) == Decimal("3.01")


def test_sell_sizing_falls_back_to_quantity_times_mark() -> None:
    position = {"quantity": "2", "market_value_usd": None, "mark_usd": "1.5"}
    assert _sell_full_quantity_amount(position) == Decimal("3.00")


def test_sell_sizing_never_returns_zero_or_a_missing_value() -> None:
    assert _sell_full_quantity_amount({"quantity": "0"}) is None
    assert _sell_full_quantity_amount({"quantity": "-1", "mark_usd": "1"}) is None
    assert _sell_full_quantity_amount({"quantity": "1"}) is None
    assert _sell_full_quantity_amount({"quantity": "1", "mark_usd": "NaN"}) is None
    tiny = _sell_full_quantity_amount({"quantity": "1", "mark_usd": "0.0000001"})
    assert tiny == Decimal("0.01"), "below one cent is still the smallest legal order"


class _BrokenStateHarness:
    """Only what LedgerWindow.close needs once the paper state is known to be broken."""

    def __init__(self) -> None:
        self.paper_state_broken = "oversold position"
        self.invariants: list[str] = []
        self.skipped: list[str] = []
        self.notes: list[str] = []
        self.rows: list[tuple[str, dict[str, object]]] = []

    def register_invariants(self, *identifiers: str) -> None:
        self.invariants.extend(identifiers)

    def skip_invariant(self, identifier: str, detail: str | None = None) -> None:
        self.skipped.append(identifier)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def record_row(self, section: str, row: dict[str, object]) -> None:
        self.rows.append((section, row))

    def request(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a broken paper state must not trigger more reads")


def test_ledger_recompute_is_skipped_when_the_account_is_inconsistent() -> None:
    harness = _BrokenStateHarness()
    window = LedgerWindow(harness, scenario="test")  # type: ignore[arg-type]
    window._before = _Observation(positions={("solana", "addr"): Decimal("5")})
    window.close()
    assert set(harness.skipped) == {"ledger.positions_match_fills", "ledger.cash_matches_fills"}
    assert harness.rows[0][0] == "ledger"
    assert harness.rows[0][1]["skip"] == "paper_state_broken"
    assert any("oversold position" in note for note in harness.notes)


def test_strategy_orders_are_attributed_from_the_run_log() -> None:
    """A strategy places its order inside the API; the run log is where its id surfaces."""
    harness = SimpleNamespace(noted=[])
    harness.note_order = harness.noted.append  # type: ignore[attr-defined]
    runs = [
        {"id": "r1", "action": "buy", "order_id": "o1"},
        {"id": "r2", "action": "hold", "order_id": None},
        {"id": "r3", "action": "sell", "order_id": "o3"},
    ]
    _note_strategy_order_ids(harness, runs)  # type: ignore[arg-type]
    assert harness.noted == ["o1", "o3"]
