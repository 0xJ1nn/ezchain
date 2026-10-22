"""Quote service: Jupiter on Solana, DexScreener paper mark elsewhere."""

from __future__ import annotations

from decimal import Decimal

from ezchain_adapters import DexScreenerClient, JupiterClient, NoRoute, NotFound
from ezchain_core import Quote, SourceRef, utcnow

from ..config import Settings


async def get_quote(
    *,
    chain: str,
    token_address: str,
    side: str,
    amount_usd: Decimal,
    quantity: Decimal | None,
    dexscreener: DexScreenerClient,
    jupiter: JupiterClient,
    settings: Settings,
) -> Quote:
    if chain == "solana":
        if side == "sell" and quantity is None:
            snapshot = await dexscreener.get_token(chain, token_address)
            if snapshot.price_usd is None or snapshot.price_usd <= 0:
                raise NoRoute("sell requires a positive price")
            quantity = amount_usd / snapshot.price_usd
        quote, _, _ = await jupiter.quote_paper(
            token_address=token_address,
            side=side,
            amount_usd=amount_usd,
            quantity=quantity,
        )
        if side == "buy" and quote.amount_out > 0:
            # The buy is only valid if the received size has an executable exit.
            try:
                await jupiter.quote_paper(
                    token_address=token_address,
                    side="sell",
                    amount_usd=Decimal(0),
                    quantity=quote.amount_out,
                )
            except (NoRoute, NotFound) as exc:
                raise NoRoute("buy has no full-size sell route") from exc
        return quote

    snapshot = await dexscreener.get_token(chain, token_address)
    if snapshot.price_usd is None or snapshot.price_usd <= 0:
        raise NoRoute("no executable paper mark")
    price = snapshot.price_usd
    if side == "buy":
        amount_in = amount_usd
        amount_out = amount_usd / price
    else:
        if quantity is None or quantity <= 0:
            quantity = amount_usd / price
        amount_in = quantity
        amount_out = quantity * price
    source = snapshot.source or SourceRef("dexscreener", "https://dexscreener.com", utcnow())
    return Quote(
        chain=chain,
        token_in="USDC" if side == "buy" else token_address,
        token_out=token_address if side == "buy" else "USDC",
        side="buy" if side == "buy" else "sell",
        amount_in=amount_in,
        amount_out=amount_out,
        price=price,
        price_impact_pct=None,
        fee_usd=Decimal(0),
        kind="paper_mark",
        route=None,
        source=source,
    )
