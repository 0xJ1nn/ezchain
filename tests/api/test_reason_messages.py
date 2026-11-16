"""Every taxonomy code renders a message on the order the risk page reads (GH-28)."""

from __future__ import annotations

from ezchain_api.schemas import OrderOut
from ezchain_core.reason_codes import REASON_CODES, message_for


def test_every_taxonomy_code_has_a_non_empty_message() -> None:
    """Iterate the taxonomy, not a hand sample: a new code with no text fails here."""
    assert REASON_CODES, "the taxonomy must be populated"
    for code in REASON_CODES:
        message = message_for(code)
        assert message and message.strip(), f"{code} has no display message"


def test_every_taxonomy_code_renders_on_the_order_schema() -> None:
    """A rejected order carries the message from the one taxonomy, not a copied table."""
    for code in REASON_CODES:
        order = OrderOut(
            id="order-id",
            chain="blast",
            token_address="0xabc",
            side="buy",
            amount_usd="1",
            status="rejected",
            reason=code,
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert order.reason_message == message_for(code)
        assert order.reasons and order.reasons[0].code == code
        assert order.reasons[0].message == message_for(code)


def test_an_unknown_code_is_labelled_itself_and_never_blank() -> None:
    order = OrderOut(
        id="order-id",
        chain="blast",
        token_address="0xabc",
        side="buy",
        amount_usd="1",
        status="rejected",
        reason="NOT_IN_THE_TAXONOMY",
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert order.reason_message == "NOT_IN_THE_TAXONOMY"
